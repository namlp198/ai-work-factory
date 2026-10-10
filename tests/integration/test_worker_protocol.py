"""Integration: API <-> DB <-> worker protocol, plus the failure scenarios of Design §20."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from awf.agent_runtime import AgentRuntime
from awf.agent_runtime.adapters import ModelError
from awf.agent_runtime.adapters.scripted import ScriptedAdapter
from awf.domain.models import Job, Worker
from awf.tool_sdk import ToolContext, ToolRegistry
from awf.workflow_engine import parse_workflow
from awf.worker_sdk import ControlPlaneClient, ControlPlaneError, JobExecutor
from awf.worker_sdk import Worker as WorkerDaemon

ROOT = Path(__file__).resolve().parents[2]

DEV_ONLY = {
    "id": "dev-only", "version": "1", "inputs": ["objective", "repository"],
    "stages": [{"id": "development", "role": "engineering.developer"}],
}


def _use_workflow(platform, data):
    loaded = parse_workflow(data, platform.agents.roles())
    platform.workflows._workflows[loaded.spec.id] = loaded


def _script(tmp_path, monkeypatch, roles: dict) -> None:
    path = tmp_path / "script.yaml"
    path.write_text(yaml.safe_dump({"roles": roles}), encoding="utf-8")
    monkeypatch.setenv("AWF_SCRIPTED_RESPONSES", str(path))


def _start(client, repository_config, workflow_id="dev-only", **project):
    p = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config, **project}).json()
    resp = client.post(f"/api/v1/projects/{p['id']}/workflow-runs",
                       json={"workflow_id": workflow_id, "inputs": {"objective": "Add a health check"}})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _drive(client, orchestrator, worker, run_id, rounds=30):
    for _ in range(rounds):
        orchestrator.run_until_idle()
        while worker.run_once():
            orchestrator.run_until_idle()
        run = client.get(f"/api/v1/workflow-runs/{run_id}").json()
        if run["status"] != "RUNNING":
            return run
    return client.get(f"/api/v1/workflow-runs/{run_id}").json()


def _dev_turns(files: dict[str, str]):
    return [{"turns": [
        {"tool_calls": [{"name": "fs.write", "input": {"path": p, "content": c}} for p, c in files.items()]},
        {"tool_calls": [{"name": "build.run", "input": {}}, {"name": "test.run", "input": {}}]},
        {"submit": {"summary": "done"}},
    ]}]


# ---------------------------------------------------------------------------- auth
def test_registration_and_worker_auth(client, platform):
    platform.settings.worker_registration_token = "reg-secret"
    body = {"name": "w1", "platform": "linux-amd64", "capabilities": ["git"]}
    assert client.post("/api/v1/workers/register", json=body).status_code == 401
    resp = client.post("/api/v1/workers/register", json=body, headers={"X-Registration-Token": "reg-secret"})
    assert resp.status_code == 201
    worker = resp.json()
    assert client.post(f"/api/v1/workers/{worker['id']}/claim").status_code == 401
    assert client.post(f"/api/v1/workers/{worker['id']}/claim",
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.post(f"/api/v1/workers/{worker['id']}/claim",
                     headers={"Authorization": f"Bearer {worker['worker_token']}"})
    assert ok.status_code == 200 and ok.json() == {"job": None}
    assert "worker_token" not in client.get("/api/v1/workers").json()[0]


def test_admin_api_requires_token_when_configured(client, platform):
    platform.settings.api_token = "admin-secret"
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/projects", headers={"Authorization": "Bearer admin-secret"}).status_code == 200
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------- scheduling
def test_capability_routing_and_duplicate_events(client, platform, orchestrator, repository_config, tmp_path):
    _use_workflow(platform, DEV_ONLY)
    _start(client, repository_config)
    orchestrator.run_until_idle()

    windows = WorkerDaemon(ControlPlaneClient(http=client), name="win-01", workspace_root=tmp_path / "w",
                           policy_path=ROOT / "config" / "policies.yaml", capabilities=["git", "python"],
                           platform_name="windows-amd64")
    windows.register("")
    assert windows.client.claim() is None  # job requires a linux worker

    linux = WorkerDaemon(ControlPlaneClient(http=client), name="lin-01", workspace_root=tmp_path / "l",
                         policy_path=ROOT / "config" / "policies.yaml", capabilities=["python"],
                         platform_name="linux-amd64")
    linux.register("")
    assert linux.client.claim() is None  # missing the 'git' capability

    linux.capabilities = ["git", "python"]
    linux.register("")
    job = linux.client.claim()
    assert job is not None and job["state"] == "CLAIMED"
    linux.client.complete(job["id"], {"status": "succeeded", "outputs": {}})
    with pytest.raises(ControlPlaneError) as exc:  # duplicate completion event is rejected
        linux.client.complete(job["id"], {"status": "succeeded", "outputs": {}})
    assert exc.value.status == 409
    with pytest.raises(ControlPlaneError):  # another worker cannot report on this job
        windows.client.fail(job["id"], "x", True)


def test_offline_worker_jobs_are_requeued(client, platform, orchestrator, worker, repository_config):
    _use_workflow(platform, DEV_ONLY)
    _start(client, repository_config)
    orchestrator.run_until_idle()
    job = worker.client.claim()
    assert job is not None
    with platform.db.transaction() as s:
        w = s.get(Worker, worker.client.worker_id)
        w.last_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)
    orchestrator.tick()
    with platform.db.session() as s:
        requeued = s.get(Job, job["id"])
        assert requeued.state == "QUEUED" and requeued.attempt == 2 and requeued.worker_id is None
        assert s.get(Worker, worker.client.worker_id).status == "offline"
    worker.heartbeat()  # worker comes back
    assert worker.client.claim()["id"] == job["id"]


# ---------------------------------------------------------------------------- failure scenarios
def test_build_and_test_failures_exhaust_retries(client, platform, orchestrator, worker, repository_config,
                                                 tmp_path, monkeypatch):
    _use_workflow(platform, DEV_ONLY)
    _script(tmp_path, monkeypatch, {"engineering.developer": _dev_turns({
        "perception_service/broken.py": "def oops(:\n",
        "tests/test_broken.py": "def test_fails():\n    assert False\n"})})
    run_id = _start(client, repository_config)
    run = _drive(client, orchestrator, worker, run_id)
    assert run["status"] == "FAILED"
    task = client.get(f"/api/v1/workflow-runs/{run_id}/tasks").json()[0]
    assert task["state"] == "FAILED" and task["attempt"] == task["max_attempts"] == 3
    codes = {f["code"] for f in task["output"]["critic"]["findings"]}
    assert "COMPLETION_RULE_FAILED" in codes
    detail = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert len(detail["agent_runs"]) == 3
    assert all(r["critic"]["decision"] == "CHANGES_REQUIRED" for r in detail["agent_runs"])


def test_critic_blocks_secret_in_diff(client, platform, orchestrator, worker, repository_config, tmp_path,
                                      monkeypatch):
    _use_workflow(platform, DEV_ONLY)
    _script(tmp_path, monkeypatch, {"engineering.developer": _dev_turns({
        "perception_service/config.py": 'API_KEY = "sk-ant-api03-THISISNOTAREALKEY0000"\n'})})
    run_id = _start(client, repository_config)
    run = _drive(client, orchestrator, worker, run_id)
    assert run["status"] == "FAILED"
    task = client.get(f"/api/v1/workflow-runs/{run_id}/tasks").json()[0]
    assert task["output"]["critic"]["decision"] == "BLOCK"
    events = [e["event_type"] for e in client.get("/api/v1/audit", params={"run_id": run_id}).json()]
    assert "CRITIC_BLOCKED" in events
    arts = client.get(f"/api/v1/projects/{run['project_id']}/artifacts", params={"run_id": run_id}).json()
    diff = next(a for a in arts if a["type"] == "source_change")
    assert "THISISNOTAREALKEY" not in client.get(f"/api/v1/artifacts/{diff['id']}/content").text


def test_restricted_project_never_reaches_hosted_model(client, platform, orchestrator, worker, repository_config):
    _use_workflow(platform, DEV_ONLY)
    platform.router.provider_override = ""
    run_id = _start(client, repository_config, confidentiality="restricted")
    run = _drive(client, orchestrator, worker, run_id)
    assert run["status"] == "FAILED" and "LOCAL_PRIVATE" in run["error"]


def test_cancel_run_cancels_queued_jobs(client, platform, orchestrator, repository_config):
    _use_workflow(platform, DEV_ONLY)
    run_id = _start(client, repository_config)
    orchestrator.run_until_idle()
    assert client.post(f"/api/v1/workflow-runs/{run_id}/cancel").json()["status"] == "CANCELLED"
    task = client.get(f"/api/v1/workflow-runs/{run_id}/tasks").json()[0]
    assert task["state"] == "CANCELLED"
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["jobs"][0]["state"] == "CANCELLED"
    assert client.post(f"/api/v1/workflow-runs/{run_id}/cancel").status_code == 409


def test_manual_retry_resumes_failed_run(client, platform, orchestrator, worker, repository_config, tmp_path,
                                         monkeypatch):
    _use_workflow(platform, DEV_ONLY)
    _script(tmp_path, monkeypatch, {"engineering.developer": _dev_turns({"tests/test_x.py": "def test_x():\n    assert False\n"})})
    run_id = _start(client, repository_config)
    assert _drive(client, orchestrator, worker, run_id)["status"] == "FAILED"
    task = client.get(f"/api/v1/workflow-runs/{run_id}/tasks").json()[0]
    _script(tmp_path, monkeypatch, {"engineering.developer": _dev_turns({
        "tests/test_x.py": "def test_x():\n    assert True\n"})})
    assert client.post(f"/api/v1/tasks/{task['id']}/retry").status_code == 200
    assert _drive(client, orchestrator, worker, run_id)["status"] == "COMPLETED"


# ---------------------------------------------------------------------------- runtime / executor
def test_malformed_structured_output_is_rejected_then_fixed(platform, tmp_path):
    contract = platform.agents.get("engineering.solution-architect").contract
    adapter = ScriptedAdapter(script={"roles": {"a": [{"turns": [
        {"submit": {"outputs": {"architecture_document": "# no sections"}}}]}]}})
    ctx = ToolContext(workspace=tmp_path, permissions=contract.permissions, policy=platform.policy)
    result = AgentRuntime(ToolRegistry()).run(
        contract=contract, system_prompt="s", user_message="u", adapter=adapter, model="scripted", params={},
        tool_ctx=ctx, meta={"role": "a", "task": {"title": "t"}, "contract": contract.model_dump(by_alias=True)})
    assert result.status == "succeeded" and result.turns == 2
    rejected = [c for c in ctx.calls if c.get("status") == "rejected"]
    assert rejected and rejected[0]["problems"][0]["code"] == "MISSING_SECTION"


def test_model_errors_are_reported_as_retryable_failures(platform, tmp_path, demo_repo):
    class TimeoutAdapter:
        def start(self, **_):
            raise ModelError("model timeout", retryable=True)

    executor = JobExecutor(tmp_path / "ws", platform.policy, adapter_factory=lambda provider: TimeoutAdapter())
    definition = platform.agents.get("engineering.code-reviewer")
    payload = {
        "job_type": "agent", "repository": {"url": str(demo_repo), "base_branch": "main"},
        "workspace": {"integration_branch": "awf/run-test", "task_branch": None, "write": False},
        "task": {"id": "t", "title": "Review", "stage_id": "review", "iteration": 1, "attempt": 1},
        "context": {"user_message": "review", "objective": "x"},
        "agent": {"role": definition.role, "contract": definition.contract.model_dump(by_alias=True),
                  "system_prompt": definition.system_prompt,
                  "route": {"provider": "x", "model": "m", "params": {}}},
    }
    result = executor.execute("job-1", payload)
    assert result["status"] == "failed" and result["retryable"] is True and "timeout" in result["error"]
    assert not (tmp_path / "ws" / "job-1").exists()  # workspace cleaned up
