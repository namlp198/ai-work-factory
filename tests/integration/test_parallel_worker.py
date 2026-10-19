"""Workers with several job slots (AWF_WORKER_MAX_JOBS): parallel tasks of a run execute at the same time on
the run's one machine, and the control plane never hands a machine more jobs than it declared."""

from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path

from sqlalchemy import select

from awf.domain.models import Job
from awf.workflow_engine import parse_workflow
from awf.worker_sdk import ControlPlaneClient
from awf.worker_sdk import Worker as WorkerDaemon

ROOT = Path(__file__).resolve().parents[2]
LINUX = {"platform": "linux"}
_projects = itertools.count(1)

# Three tool jobs that become ready together.
FAN_OUT = {
    "id": "fan-out", "version": "1", "inputs": ["objective", "repository"],
    "stages": [{"id": "checks", "parallel": [{"id": "lint", "tool": "lint.run"}, {"id": "test", "tool": "test.run"},
                                             {"id": "build", "tool": "build.run"}]}],
}


def _use(platform, data):
    loaded = parse_workflow(data, platform.agents.roles())
    platform.workflows._workflows[loaded.spec.id] = loaded


def _daemon(client, tmp_path, name, max_jobs, poll=0.05):
    w = WorkerDaemon(ControlPlaneClient(http=client), name=name, workspace_root=tmp_path / name,
                     policy_path=ROOT / "config" / "policies.yaml", capabilities=["git", "python"],
                     platform_name="linux-amd64", max_jobs=max_jobs, poll_interval=poll, heartbeat_interval=0.2)
    w.register("")
    return w


def _start(client, repository_config, workflow_id="fan-out"):
    p = client.post("/api/v1/projects", json={"name": f"P{next(_projects)}", "repository": repository_config,
                                              "labels": LINUX}).json()
    resp = client.post(f"/api/v1/projects/{p['id']}/workflow-runs",
                       json={"workflow_id": workflow_id, "inputs": {"objective": "x"}})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class _Concurrency:
    """Wraps the real executor and records how many jobs were executing at the same time."""

    def __init__(self, inner, hold: float = 0.0):
        self.inner, self.hold = inner, hold
        self.lock = threading.Lock()
        self.now = self.peak = 0

    def execute(self, *args, **kwargs):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        try:
            time.sleep(self.hold)  # keep the slot busy long enough for the other jobs to be claimed
            return self.inner.execute(*args, **kwargs)
        finally:
            with self.lock:
                self.now -= 1


def _serve(worker, client, orchestrator, run_id, timeout=90, approve=False):
    thread = threading.Thread(target=worker.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            orchestrator.run_until_idle()
            if approve:
                for a in client.get("/api/v1/approvals", params={"state": "pending"}).json():
                    client.post(f"/api/v1/approvals/{a['id']}/approve", json={"comment": "ok"})
            run = client.get(f"/api/v1/workflow-runs/{run_id}").json()
            if run["status"] != "RUNNING":
                return run
            time.sleep(0.05)
        raise AssertionError("run did not finish")
    finally:
        worker.stop()
        assert worker.wait_idle(30)
        thread.join(5)


# ---------------------------------------------------------------------------- control plane
def test_claims_stop_at_the_declared_slots(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, FAN_OUT)
    one = _daemon(client, tmp_path, "one-slot", max_jobs=1)
    _start(client, repository_config)
    orchestrator.run_until_idle()
    first = one.client.claim()
    assert first is not None
    assert one.client.claim() is None  # three jobs queued for it, but its only slot is taken
    one.run_job(first)
    assert one.client.claim() is not None  # slot free again


def test_three_slots_take_three_jobs_at_once(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, FAN_OUT)
    three = _daemon(client, tmp_path, "three-slots", max_jobs=3)
    run_id = _start(client, repository_config)
    orchestrator.run_until_idle()
    claimed = [three.client.claim() for _ in range(4)]
    assert [j is not None for j in claimed] == [True, True, True, False]
    assert {w["name"]: w["max_jobs"] for w in client.get("/api/v1/workers").json()} == {"three-slots": 3}
    with platform.db.session() as s:
        assert {j.worker_id for j in s.scalars(select(Job).where(Job.workflow_run_id == run_id))} == \
            {three.client.worker_id}


def test_slots_count_in_placement_load(client, platform, orchestrator, repository_config, tmp_path):
    """A 4-slot machine with 3 queued jobs (0.75) is less loaded than a 1-slot machine with 1 (1.0)."""
    _use(platform, FAN_OUT)
    big = _daemon(client, tmp_path, "big", max_jobs=4)
    small = _daemon(client, tmp_path, "small", max_jobs=1)
    first = _start(client, repository_config)
    orchestrator.run_until_idle()  # 3 jobs -> 'big' (alphabetical tie-break at zero load)
    second = _start(client, repository_config)
    orchestrator.run_until_idle()  # big: 3/4, small: 0/1 -> small
    third = _start(client, repository_config)
    orchestrator.run_until_idle()  # big: 3/4 = 0.75, small: 3/1 = 3 -> big
    names = {r: client.get(f"/api/v1/workflow-runs/{r}").json()["assigned_worker"]["name"]
             for r in (first, second, third)}
    assert names == {first: "big", second: "small", third: "big"}
    del small


# ---------------------------------------------------------------------------- the daemon
def test_daemon_executes_parallel_jobs_at_the_same_time(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, FAN_OUT)
    worker = _daemon(client, tmp_path, "pc", max_jobs=3)
    probe = _Concurrency(worker.executor, hold=0.5)
    worker.executor = probe
    run_id = _start(client, repository_config)
    final = _serve(worker, client, orchestrator, run_id)
    assert final["status"] == "COMPLETED", final.get("error")
    assert probe.peak == 3
    assert worker.running_jobs == 0


def test_single_slot_daemon_runs_one_job_at_a_time(client, platform, orchestrator, repository_config, tmp_path):
    _use(platform, FAN_OUT)
    worker = _daemon(client, tmp_path, "pc", max_jobs=1)
    probe = _Concurrency(worker.executor, hold=0.2)
    worker.executor = probe
    run_id = _start(client, repository_config)
    assert _serve(worker, client, orchestrator, run_id)["status"] == "COMPLETED"
    assert probe.peak == 1


def test_full_software_run_on_a_multi_slot_worker(client, platform, orchestrator, repository_config, demo_repo,
                                                 tmp_path):
    """Requirement -> merge with gates, fix loop and parallel verification, executed by a 3-slot daemon."""
    worker = _daemon(client, tmp_path, "pc", max_jobs=3)
    probe = _Concurrency(worker.executor, hold=0.3)
    worker.executor = probe
    run_id = _start(client, repository_config, "software-development")
    final = _serve(worker, client, orchestrator, run_id, timeout=180, approve=True)
    assert final["status"] == "COMPLETED", final.get("error")
    assert final["fix_loops"] == {"review": 1}
    assert probe.peak >= 2  # tests + static analysis ran side by side
    from tests.conftest import git_show

    assert "pipeline is None" in git_show(demo_repo, f"{final['integration_branch']}:perception_service/health.py")
