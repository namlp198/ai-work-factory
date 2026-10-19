"""Run placement (ADR-008): every job of a run goes to one machine, chosen by requirements and load."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from awf.domain.models import Job, Worker, WorkflowRun
from awf.workflow_engine import parse_workflow
from awf.worker_sdk import ControlPlaneClient
from awf.worker_sdk import Worker as WorkerDaemon

ROOT = Path(__file__).resolve().parents[2]

# Two agent stages and a tool stage: three jobs that must all land on the same machine.
THREE_STEP = {
    "id": "three-step", "version": "1", "inputs": ["objective", "repository"],
    "stages": [
        {"id": "requirement", "role": "product.analyst"},
        {"id": "development", "role": "engineering.developer", "depends_on": ["requirement"]},
        {"id": "lint", "tool": "lint.run", "depends_on": ["development"]},
    ],
}


def _use(platform, data):
    loaded = parse_workflow(data, platform.agents.roles())
    platform.workflows._workflows[loaded.spec.id] = loaded


def _daemon(client, tmp_path, name, platform_name="linux-amd64", capabilities=("git", "python"), labels=None):
    w = WorkerDaemon(ControlPlaneClient(http=client), name=name, workspace_root=tmp_path / name,
                     policy_path=ROOT / "config" / "policies.yaml", capabilities=list(capabilities),
                     platform_name=platform_name, labels=labels or {})
    w.register("")
    return w


def _project(client, repository_config, **extra):
    return client.post("/api/v1/projects", json={"name": "P", "repository": repository_config, "labels": {"platform": "linux"}, **extra}).json()


def _start(client, project, workflow_id="three-step", worker=None):
    body = {"workflow_id": workflow_id, "inputs": {"objective": "Add a health check"}}
    if worker:
        body["placement"] = {"worker": worker}
    return client.post(f"/api/v1/projects/{project['id']}/workflow-runs", json=body)


def _drive(client, orchestrator, workers, run_ids, rounds=40):
    for _ in range(rounds):
        orchestrator.run_until_idle()
        busy = True
        while busy:
            busy = False
            for w in workers:  # every machine asks for work in turn, as they would in parallel
                if w.run_once():
                    busy = True
                    orchestrator.run_until_idle()
        runs = [client.get(f"/api/v1/workflow-runs/{r}").json() for r in run_ids]
        if all(r["status"] != "RUNNING" for r in runs):
            return runs
    raise AssertionError("runs did not finish")


def _job_workers(platform, run_id) -> set[str]:
    with platform.db.session() as s:
        return {j.worker_id for j in s.scalars(select(Job).where(Job.workflow_run_id == run_id))}


def _names(platform, ids) -> set[str]:
    with platform.db.session() as s:
        return {s.get(Worker, i).name for i in ids}


def _age_heartbeat(platform, worker, seconds):
    with platform.db.transaction() as s:
        s.get(Worker, worker.client.worker_id).last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=seconds)


# ---------------------------------------------------------------------------- one machine per run
def test_every_job_of_a_run_runs_on_one_machine_and_runs_spread_by_load(client, platform, orchestrator,
                                                                        repository_config, tmp_path):
    _use(platform, THREE_STEP)
    a = _daemon(client, tmp_path, "pc-a")
    b = _daemon(client, tmp_path, "pc-b")
    project = _project(client, repository_config)
    first = _start(client, project).json()["id"]
    orchestrator.run_until_idle()
    second = _start(client, project).json()["id"]
    orchestrator.run_until_idle()

    runs = _drive(client, orchestrator, [a, b], [first, second])
    assert [r["status"] for r in runs] == ["COMPLETED", "COMPLETED"]
    first_machines, second_machines = _job_workers(platform, first), _job_workers(platform, second)
    assert len(first_machines) == 1 and len(second_machines) == 1
    # pc-a already had the first run queued, so the second one went to the idle pc-b.
    assert _names(platform, first_machines) == {"pc-a"} and _names(platform, second_machines) == {"pc-b"}
    assert runs[0]["assigned_worker"]["name"] == "pc-a"
    assert runs[0]["requirements"] == {"platform": "linux", "capabilities": ["git"]}
    with platform.db.session() as s:
        assert all(j.pinned for j in s.scalars(select(Job)))
    events = client.get("/api/v1/audit", params={"run_id": first}).json()
    assigned = [e for e in events if e["event_type"] == "RUN_ASSIGNED"]
    assert len(assigned) == 1 and assigned[0]["payload"]["worker"] == "pc-a"


def test_other_machines_never_get_a_pinned_job(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, THREE_STEP)
    a = _daemon(client, tmp_path, "pc-a")
    b = _daemon(client, tmp_path, "pc-b")
    run_id = _start(client, _project(client, repository_config)).json()["id"]
    orchestrator.run_until_idle()
    assert b.client.claim() is None
    job = a.client.claim()
    assert job is not None and job["pinned"] and job["assigned_worker_id"] == a.client.worker_id
    a.run_job(job)
    assert _drive(client, orchestrator, [b, a], [run_id])[0]["status"] == "COMPLETED"
    assert _names(platform, _job_workers(platform, run_id)) == {"pc-a"}


def test_preferred_for_label_breaks_ties(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, THREE_STEP)
    _daemon(client, tmp_path, "pc-a")
    _daemon(client, tmp_path, "pc-b", labels={"preferred_for": "robo-station, other"})
    project = client.post("/api/v1/projects", json={"name": "Robo", "slug": "robo-station", "labels": {"platform": "linux"},
                                                    "repository": repository_config}).json()
    run_id = _start(client, project).json()["id"]
    orchestrator.run_until_idle()
    assert client.get(f"/api/v1/workflow-runs/{run_id}").json()["assigned_worker"]["name"] == "pc-b"


# ---------------------------------------------------------------------------- matching the right machine
def test_run_requirements_cover_every_stage(client, platform, orchestrator, repository_config, tmp_path):
    workflow = {**THREE_STEP, "id": "needs-cuda",
                "stages": [*THREE_STEP["stages"][:2],
                           {**THREE_STEP["stages"][2], "worker": {"capabilities": ["cuda"]}}]}
    _use(platform, workflow)
    plain = _daemon(client, tmp_path, "pc-plain")
    gpu = _daemon(client, tmp_path, "pc-gpu", capabilities=("git", "python", "cuda"))
    run_id = _start(client, _project(client, repository_config), "needs-cuda").json()["id"]
    run = client.get(f"/api/v1/workflow-runs/{run_id}").json()
    assert run["requirements"]["capabilities"] == ["cuda", "git"]
    # The first stage needs no GPU, but the run as a whole does: it goes to the GPU machine from the start.
    assert _drive(client, orchestrator, [plain, gpu], [run_id])[0]["status"] == "COMPLETED"
    assert _names(platform, _job_workers(platform, run_id)) == {"pc-gpu"}


def test_windows_dotnet_project_is_refused_when_only_ubuntu_is_registered(client, platform, orchestrator,
                                                                          repository_config, tmp_path):
    _use(platform, THREE_STEP)
    _daemon(client, tmp_path, "ubuntu-01", capabilities=("git", "python", "dotnet"))
    project = _project(client, {**repository_config, "required_capabilities": ["dotnet"]},
                       labels={"platform": "windows"})
    resp = _start(client, project)
    assert resp.status_code == 422
    assert "no registered worker meets platform windows" in resp.json()["detail"]
    assert "ubuntu-01: platform is linux-amd64, needs windows" in resp.json()["detail"]
    with platform.db.session() as s:
        assert s.scalars(select(WorkflowRun)).all() == []


def test_conflicting_stage_platforms_are_refused(client, platform, repository_config):
    workflow = {**THREE_STEP, "id": "two-os",
                "stages": [{**THREE_STEP["stages"][0], "worker": {"platform": "linux"}},
                           {**THREE_STEP["stages"][1], "worker": {"platform": "windows"}}]}
    _use(platform, workflow)
    resp = _start(client, _project(client, repository_config), "two-os")
    assert resp.status_code == 422 and "placement: stage" in resp.json()["detail"]


def test_stage_placement_keeps_the_old_behaviour(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, {**THREE_STEP, "id": "per-stage", "placement": "stage"})
    a = _daemon(client, tmp_path, "pc-a")
    b = _daemon(client, tmp_path, "pc-b")
    project = _project(client, repository_config)
    assert _start(client, project, "per-stage", worker="pc-a").status_code == 422  # cannot pin
    run_id = _start(client, project, "per-stage").json()["id"]
    orchestrator.run_until_idle()
    assert b.client.claim() is not None  # whoever asks first gets it
    with platform.db.session() as s:
        assert s.get(WorkflowRun, run_id).assigned_worker_id is None
        assert not any(j.pinned for j in s.scalars(select(Job)))
    del a


def test_a_run_waits_when_no_matching_machine_is_online(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, THREE_STEP)
    a = _daemon(client, tmp_path, "pc-a")
    _age_heartbeat(platform, a, 3600)
    orchestrator.tick()  # pc-a is marked offline
    run_id = _start(client, _project(client, repository_config)).json()["id"]  # registered, so accepted
    orchestrator.run_until_idle()
    run = client.get(f"/api/v1/workflow-runs/{run_id}").json()
    assert run["status"] == "RUNNING" and run["assigned_worker"] is None
    assert "waiting for an online worker" in run["placement"]["wait"]
    with platform.db.session() as s:
        assert s.scalars(select(Job)).all() == []  # nothing queued for nobody
    a.heartbeat()
    orchestrator.run_until_idle()
    run = client.get(f"/api/v1/workflow-runs/{run_id}").json()
    assert run["assigned_worker"]["name"] == "pc-a" and run["placement"]["wait"] is None


# ---------------------------------------------------------------------------- choosing by hand
def test_run_pinned_to_a_chosen_machine(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, THREE_STEP)
    a = _daemon(client, tmp_path, "pc-a")
    b = _daemon(client, tmp_path, "pc-b")
    project = _project(client, repository_config)
    assert _start(client, project, worker="nope").status_code == 422
    resp = _start(client, project, worker="pc-b")
    assert resp.status_code == 201 and resp.json()["placement"]["worker"] == "pc-b"
    run_id = resp.json()["id"]
    assert _drive(client, orchestrator, [a, b], [run_id])[0]["status"] == "COMPLETED"
    assert _names(platform, _job_workers(platform, run_id)) == {"pc-b"}


def test_chosen_machine_must_meet_the_requirements(client, platform, repository_config, tmp_path):
    _use(platform, THREE_STEP)
    _daemon(client, tmp_path, "win-01", platform_name="windows-amd64")
    resp = _start(client, _project(client, repository_config), worker="win-01")
    assert resp.status_code == 422 and "does not meet platform linux" in resp.json()["detail"]


def test_reassign_moves_a_run_between_jobs(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, THREE_STEP)
    a = _daemon(client, tmp_path, "pc-a")
    b = _daemon(client, tmp_path, "pc-b")
    run_id = _start(client, _project(client, repository_config)).json()["id"]
    orchestrator.run_until_idle()
    job = a.client.claim()
    busy = client.post(f"/api/v1/workflow-runs/{run_id}/reassign", json={"worker": "pc-b"})
    assert busy.status_code == 409 and "executing" in busy.json()["detail"]
    a.run_job(job)  # first job finishes on pc-a
    orchestrator.run_until_idle()

    moved = client.post(f"/api/v1/workflow-runs/{run_id}/reassign", json={"worker": "pc-b"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["assigned_worker"]["name"] == "pc-b" and moved.json()["placement"]["worker"] == "pc-b"
    assert a.client.claim() is None
    assert _drive(client, orchestrator, [a, b], [run_id])[0]["status"] == "COMPLETED"
    with platform.db.session() as s:
        jobs = sorted(s.scalars(select(Job).where(Job.workflow_run_id == run_id)), key=lambda j: j.created_at)
        assert [s.get(Worker, j.worker_id).name for j in jobs] == ["pc-a", "pc-b", "pc-b"]
    assert client.post(f"/api/v1/workflow-runs/{run_id}/reassign", json={}).status_code == 409  # finished


# ---------------------------------------------------------------------------- failover
def test_short_outage_keeps_the_run_on_its_machine(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, THREE_STEP)
    a = _daemon(client, tmp_path, "pc-a")
    b = _daemon(client, tmp_path, "pc-b")
    run_id = _start(client, _project(client, repository_config)).json()["id"]
    orchestrator.run_until_idle()
    _age_heartbeat(platform, a, platform.settings.worker_heartbeat_timeout_seconds + 60)  # offline, < 10 min
    orchestrator.tick()
    assert b.client.claim() is None
    a.register("")  # the machine restarts
    assert a.client.claim() is not None
    assert client.get(f"/api/v1/workflow-runs/{run_id}").json()["assigned_worker"]["name"] == "pc-a"


def test_machine_offline_too_long_hands_the_run_to_another(client, platform, orchestrator, repository_config,
                                                          tmp_path):
    _use(platform, THREE_STEP)
    a = _daemon(client, tmp_path, "pc-a")
    b = _daemon(client, tmp_path, "pc-b")
    run_id = _start(client, _project(client, repository_config)).json()["id"]
    orchestrator.run_until_idle()
    a.run_job(a.client.claim())  # requirement done on pc-a
    orchestrator.run_until_idle()
    lost = a.client.claim()  # pc-a dies in the middle of development
    assert lost is not None
    _age_heartbeat(platform, a, platform.settings.run_reassign_after_seconds + 60)
    b.heartbeat()
    orchestrator.tick()

    run = client.get(f"/api/v1/workflow-runs/{run_id}").json()
    assert run["assigned_worker"]["name"] == "pc-b"
    final = _drive(client, orchestrator, [b], [run_id])[0]
    assert final["status"] == "COMPLETED"
    reasons = [e["payload"]["reason"] for e in client.get("/api/v1/audit", params={"run_id": run_id}).json()
               if e["event_type"] == "RUN_UNASSIGNED"]
    assert reasons and "offline for" in reasons[0]


def test_run_waiting_for_approval_stays_on_an_offline_machine(client, platform, orchestrator, repository_config,
                                                              tmp_path):
    workflow = {**THREE_STEP, "id": "with-gate",
                "stages": [THREE_STEP["stages"][0],
                           {"id": "approve", "depends_on": ["requirement"],
                            "gate": {"type": "human_approval", "action": "approve_requirement"}},
                           {**THREE_STEP["stages"][1], "depends_on": ["approve"]}]}
    _use(platform, workflow)
    a = _daemon(client, tmp_path, "pc-a")
    _daemon(client, tmp_path, "pc-b")
    run_id = _start(client, _project(client, repository_config), "with-gate").json()["id"]
    orchestrator.run_until_idle()
    a.run_once()
    orchestrator.run_until_idle()
    assert client.get("/api/v1/approvals", params={"state": "pending"}).json()
    _age_heartbeat(platform, a, platform.settings.run_reassign_after_seconds + 60)
    orchestrator.tick()
    assert client.get(f"/api/v1/workflow-runs/{run_id}").json()["assigned_worker"]["name"] == "pc-a"


def test_revoked_machine_hands_its_runs_over_at_once(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, THREE_STEP)
    a = _daemon(client, tmp_path, "pc-a")
    b = _daemon(client, tmp_path, "pc-b")
    run_id = _start(client, _project(client, repository_config)).json()["id"]
    orchestrator.run_until_idle()
    assert a.client.claim() is not None
    assert client.post(f"/api/v1/workers/{a.client.worker_id}/revoke").status_code == 200
    orchestrator.tick()
    assert client.get(f"/api/v1/workflow-runs/{run_id}").json()["assigned_worker"]["name"] == "pc-b"
    assert _drive(client, orchestrator, [b], [run_id])[0]["status"] == "COMPLETED"


def test_draining_machine_finishes_its_runs_but_takes_no_new_ones(client, platform, orchestrator,
                                                                  repository_config, tmp_path):
    _use(platform, THREE_STEP)
    a = _daemon(client, tmp_path, "pc-a")
    project = _project(client, repository_config)
    first = _start(client, project).json()["id"]
    orchestrator.run_until_idle()
    a.client.heartbeat({"status": "draining"})
    second = _start(client, project).json()["id"]
    orchestrator.run_until_idle()
    assert client.get(f"/api/v1/workflow-runs/{second}").json()["assigned_worker"] is None
    while a.run_once():  # drains: only the first run's jobs
        orchestrator.run_until_idle()
    assert client.get(f"/api/v1/workflow-runs/{first}").json()["status"] == "COMPLETED"
    assert client.get(f"/api/v1/workflow-runs/{second}").json()["status"] == "RUNNING"


def test_worker_list_shows_slots_and_runs(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, THREE_STEP)
    _daemon(client, tmp_path, "pc-a")
    _start(client, _project(client, repository_config))
    orchestrator.run_until_idle()
    row = client.get("/api/v1/workers").json()[0]
    assert row["max_jobs"] == 1 and row["active_runs"] == 1


@pytest.mark.parametrize("a,b,expected", [
    ("linux", "linux-arm64", "linux-arm64"), ("linux-arm64", "linux", "linux-arm64"),
    (None, "windows", "windows"), ("any", "linux", "linux"), ("linux", None, "linux"),
])
def test_merge_platform(a, b, expected):
    from awf.orchestrator.placement import merge_platform

    assert merge_platform(a, b) == expected


def test_merge_platform_conflict():
    from awf.orchestrator.placement import PlacementError, merge_platform

    with pytest.raises(PlacementError):
        merge_platform("linux-amd64", "linux-arm64")
