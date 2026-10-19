"""Central control plane + remote workers over the internet: dashboard sessions, transport security,
network-outage resilience, job leases, worker identity and revocation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from awf.domain.models import Job, Worker
from awf.security import new_login_ticket, sign_claims
from awf.worker_sdk import ControlPlaneClient, ControlPlaneError
from awf.worker_sdk.client import InsecureTransport, check_transport_security
from awf.workflow_engine import parse_workflow
from awf_api.routes import session as session_routes

TOKEN = "admin-token-for-tests-0123456789abcdef"
DEV_ONLY = {"id": "dev-only", "version": "1", "inputs": ["objective", "repository"],
            "stages": [{"id": "development", "role": "engineering.developer"}]}


@pytest.fixture(autouse=True)
def fresh_throttle(monkeypatch):
    monkeypatch.setattr(session_routes, "_throttle", session_routes._Throttle())


@pytest.fixture
def secured(platform, client):
    platform.settings.api_token = TOKEN
    client.cookies.clear()
    return client


# ------------------------------------------------------------------------------- dashboard sessions
def test_login_once_then_cookie_session(secured, platform, orchestrator):
    c = secured
    assert c.get("/api/v1/projects").status_code == 401
    assert c.get("/api/v1/session").json() == {"auth_required": True, "authenticated": False}
    assert c.post("/api/v1/session", json={"token": "wrong"}).status_code == 401
    resp = c.post("/api/v1/session", json={"token": TOKEN})
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=2592000" in cookie
    assert c.get("/api/v1/projects").status_code == 200

    # Cookie-authenticated writes need the dashboard header (CSRF protection); scripts use Bearer.
    assert c.post("/api/v1/projects", json={"name": "x", "labels": {"platform": "linux"}}).status_code == 403
    assert c.post("/api/v1/projects", json={"name": "x", "labels": {"platform": "linux"}}, headers={"X-AWF-Client": "dashboard"}).status_code == 201

    # Still signed in after a control-plane restart (signing key derives from the stable admin token).
    from fastapi.testclient import TestClient
    from awf_api.main import create_app

    with TestClient(create_app(platform, orchestrator, embedded_orchestrator=False)) as restarted:
        restarted.cookies.update(c.cookies)
        assert restarted.get("/api/v1/projects").status_code == 200
        # Rotating the admin token ends every session.
        platform.settings.api_token = TOKEN + "-rotated"
        assert restarted.get("/api/v1/projects").status_code == 401
        platform.settings.api_token = TOKEN

    assert c.delete("/api/v1/session").status_code == 200
    c.cookies.clear()
    assert c.get("/api/v1/projects").status_code == 401
    assert c.get("/api/v1/projects", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_one_time_login_link(secured):
    c = secured
    ticket = new_login_ticket(TOKEN)
    assert c.post("/api/v1/session", json={"ticket": ticket}).status_code == 200
    c.cookies.clear()
    assert c.post("/api/v1/session", json={"ticket": ticket}).status_code == 401  # single use
    expired = sign_claims(TOKEN, {"purpose": "login", "sub": "admin", "exp": 1, "nonce": "n"})
    assert c.post("/api/v1/session", json={"ticket": expired}).status_code == 401
    session_cookie = sign_claims(TOKEN, {"purpose": "session", "sub": "admin", "exp": 9e9, "nonce": "s"})
    assert c.post("/api/v1/session", json={"ticket": session_cookie}).status_code == 401  # wrong purpose


def test_brute_force_is_throttled(secured):
    for _ in range(session_routes.MAX_FAILURES):
        assert secured.post("/api/v1/session", json={"token": "guess"}).status_code == 401
    assert secured.post("/api/v1/session", json={"token": TOKEN}).status_code == 429


# ------------------------------------------------------------------------------- transport security
@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8000", "http://factory:8000",
                                 "http://192.168.1.20:8000", "http://10.0.0.5", "http://100.101.102.103:8000",
                                 "http://factory.tail1234.ts.net:8000", "https://factory.example.com"])
def test_allowed_control_plane_urls(url, monkeypatch):
    monkeypatch.delenv("AWF_ALLOW_INSECURE_HTTP", raising=False)
    check_transport_security(url)


@pytest.mark.parametrize("url", ["http://factory.example.com", "http://1.1.1.1:8000"])
def test_plain_http_to_public_address_is_refused(url, monkeypatch):
    monkeypatch.delenv("AWF_ALLOW_INSECURE_HTTP", raising=False)
    with pytest.raises(InsecureTransport):
        ControlPlaneClient(url)
    monkeypatch.setenv("AWF_ALLOW_INSECURE_HTTP", "1")
    check_transport_security(url)


# ------------------------------------------------------------------------------- network resilience
def _flaky_transport(failures: int, status: int = 200):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= failures:
            raise httpx.ConnectError("connection reset", request=request)
        return httpx.Response(status, json={"state": "SUCCESS"})

    return httpx.MockTransport(handler), calls


def test_result_delivery_retries_through_outage():
    transport, calls = _flaky_transport(failures=3)
    sleeps: list[float] = []
    client = ControlPlaneClient(http=httpx.Client(transport=transport, base_url="https://cp"),
                                result_retry_seconds=60, sleep=sleeps.append)
    assert client.complete("job-1", {"status": "succeeded"}) == {"state": "SUCCESS"}
    assert calls["n"] == 4 and len(sleeps) == 3 and sleeps[1] > sleeps[0]  # exponential backoff


def test_client_errors_are_not_retried():
    transport, calls = _flaky_transport(failures=0, status=409)
    client = ControlPlaneClient(http=httpx.Client(transport=transport, base_url="https://cp"),
                                result_retry_seconds=60, sleep=lambda s: None)
    with pytest.raises(ControlPlaneError) as exc:
        client.complete("job-1", {})
    assert exc.value.status == 409 and calls["n"] == 1


def _start_dev_run(client, platform, repository_config) -> str:
    loaded = parse_workflow(DEV_ONLY, platform.agents.roles())
    platform.workflows._workflows[loaded.spec.id] = loaded
    p = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config, "labels": {"platform": "linux"}}).json()
    return client.post(f"/api/v1/projects/{p['id']}/workflow-runs",
                       json={"workflow_id": "dev-only", "inputs": {"objective": "x"}}).json()["id"]


def test_workflow_survives_network_outage_during_result_upload(client, platform, orchestrator, worker,
                                                                repository_config):
    run_id = _start_dev_run(client, platform, repository_config)
    orchestrator.run_until_idle()
    real_request = worker.client.http.request
    outages = {"n": 0}

    def flaky(method, url, **kwargs):
        if url.endswith("/complete") and outages["n"] < 3:
            outages["n"] += 1
            raise httpx.ConnectError("network down")
        return real_request(method, url, **kwargs)

    worker.client.http.request = flaky
    worker.client._sleep = lambda s: None
    assert worker.run_once()
    orchestrator.run_until_idle()
    assert outages["n"] == 3
    assert client.get(f"/api/v1/workflow-runs/{run_id}").json()["status"] == "COMPLETED"


# ------------------------------------------------------------------------------- leases and identity
def test_expired_lease_requeues_lost_job(client, platform, orchestrator, worker, repository_config):
    _start_dev_run(client, platform, repository_config)
    orchestrator.run_until_idle()
    job = worker.client.claim()  # the worker then "disappears" without a word, heartbeats keep coming
    with platform.db.session() as s:
        lease = s.get(Job, job["id"]).lease_expires_at
        assert lease is not None
    with platform.db.transaction() as s:
        s.get(Job, job["id"]).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    orchestrator.tick()
    with platform.db.session() as s:
        requeued = s.get(Job, job["id"])
        assert requeued.state == "QUEUED" and requeued.attempt == 2 and requeued.worker_id is None
    events = [e["event_type"] for e in client.get("/api/v1/audit").json()]
    assert "JOB_LEASE_EXPIRED" in events


def test_worker_restart_requeues_its_jobs(client, platform, orchestrator, worker, repository_config):
    _start_dev_run(client, platform, repository_config)
    orchestrator.run_until_idle()
    job = worker.client.claim()
    worker.register("")  # same machine, process restarted
    with platform.db.session() as s:
        assert s.get(Job, job["id"]).state == "QUEUED"


def test_worker_name_clash_between_machines(client):
    body = {"name": "site-a-01", "platform": "linux-amd64", "capabilities": ["git"]}
    assert client.post("/api/v1/workers/register", json={**body, "machine_id": "m1"}).status_code == 201
    clash = client.post("/api/v1/workers/register", json={**body, "machine_id": "m2"})
    assert clash.status_code == 409 and "AWF_WORKER_NAME" in clash.text
    assert client.post("/api/v1/workers/register", json={**body, "machine_id": "m1"}).status_code == 201


def test_revoked_worker_is_locked_out_until_deleted(client, platform):
    body = {"name": "laptop-lost", "platform": "windows-amd64", "machine_id": "m9"}
    w = client.post("/api/v1/workers/register", json=body).json()
    auth = {"Authorization": f"Bearer {w['worker_token']}"}
    assert client.post(f"/api/v1/workers/{w['id']}/claim", headers=auth).status_code == 200
    assert client.post(f"/api/v1/workers/{w['id']}/revoke").json()["status"] == "revoked"
    assert client.post(f"/api/v1/workers/{w['id']}/claim", headers=auth).status_code == 401
    assert client.post("/api/v1/workers/register", json=body).status_code == 409
    orchestrator_tick_keeps_revoked = client.get("/api/v1/workers").json()[0]["status"]
    assert orchestrator_tick_keeps_revoked == "revoked"
    assert client.delete(f"/api/v1/workers/{w['id']}").status_code == 204
    assert client.post("/api/v1/workers/register", json=body).status_code == 201
    with platform.db.session() as s:
        assert s.query(Worker).filter_by(name="laptop-lost").count() == 1
