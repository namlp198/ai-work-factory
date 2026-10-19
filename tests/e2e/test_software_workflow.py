"""E2E (Design §20): Create Project -> Start Workflow -> Architecture -> Approval -> Development
-> Build/Test -> Review (changes required -> fix loop) -> QA -> PR -> Merge approval -> Completion."""

from __future__ import annotations

import pytest

from tests.conftest import git_branches, git_show

pytestmark = pytest.mark.e2e


def drive(client, orchestrator, worker, run_id: str, approve: bool = True, max_rounds: int = 80) -> dict:
    """Alternate orchestrator ticks, worker job execution and human approvals until the run ends.
    `worker` may be a list: every machine then asks for work in turn."""
    workers = worker if isinstance(worker, list) else [worker]
    approved = []
    for _ in range(max_rounds):
        orchestrator.run_until_idle()
        busy = True
        while busy:
            busy = False
            for w in workers:
                if w.run_once():
                    busy = True
                    orchestrator.run_until_idle()
        run = client.get(f"/api/v1/workflow-runs/{run_id}").json()
        if run["status"] != "RUNNING":
            run["approved_actions"] = approved
            return run
        pending = [a for a in client.get("/api/v1/approvals", params={"state": "pending"}).json()
                   if a["workflow_run_id"] == run_id]
        if not pending:
            continue
        for a in pending:
            action = "approve" if approve else "reject"
            resp = client.post(f"/api/v1/approvals/{a['id']}/{action}", json={"comment": "looks good"})
            assert resp.status_code == 200, resp.text
            approved.append(a["action"])
    raise AssertionError("workflow did not finish")


def test_full_software_workflow(client, orchestrator, worker, repository_config, demo_repo):
    project = client.post("/api/v1/projects", json={
        "name": "Perception Service", "repository": repository_config, "labels": {"platform": "linux"}}).json()
    run = client.post(f"/api/v1/projects/{project['id']}/workflow-runs", json={
        "workflow_id": "software-development",
        "inputs": {"objective": "Add a camera pipeline health-check API with tests and documentation."},
    })
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]

    final = drive(client, orchestrator, worker, run_id)
    assert final["status"] == "COMPLETED", final.get("error")
    assert final["approved_actions"] == ["approve_architecture", "approve_merge"]
    assert final["fix_loops"] == {"review": 1}
    assert all(s["status"] == "done" for s in final["stages"])

    tasks = client.get(f"/api/v1/workflow-runs/{run_id}/tasks").json()
    review_tasks = sorted((t for t in tasks if t["stage_id"] == "review"), key=lambda t: t["iteration"])
    assert [(t["iteration"], t["state"]) for t in review_tasks] == [(1, "CHANGES_REQUIRED"), (2, "DONE")]
    fix_task = next(t for t in tasks if t["stage_id"] == "development" and t["iteration"] == 2)
    assert any("AttributeError" in f for f in fix_task["input"]["feedback"])

    # Code landed on the integration branch in the origin repository; the base branch is untouched.
    integration = final["integration_branch"]
    assert integration in git_branches(demo_repo)
    health = git_show(demo_repo, f"{integration}:perception_service/health.py")
    assert "pipeline is None" in health
    assert "test_down_when_pipeline_missing" in git_show(demo_repo, f"{integration}:tests/test_health.py")
    assert "health.py" not in git_show(demo_repo, "main:perception_service/__init__.py")
    with pytest.raises(Exception):
        git_show(demo_repo, "main:perception_service/health.py")

    artifacts = client.get(f"/api/v1/projects/{project['id']}/artifacts", params={"run_id": run_id}).json()
    types = {a["type"] for a in artifacts}
    for expected in ("objective", "requirement", "architecture_document", "adr", "task_graph", "source_change",
                     "test_evidence", "test_report", "static_analysis_report", "review_report", "qa_report",
                     "source_patch", "pull_request"):
        assert expected in types, f"missing artifact type {expected}"
    assert all(a["checksum"].startswith("sha256:") for a in artifacts)

    # Every source change is versioned (one per development iteration) and traceable to the objective.
    source_changes = [a for a in artifacts if a["type"] == "source_change"]
    assert len(source_changes) == 2
    objective = next(a for a in artifacts if a["type"] == "objective")
    impact = client.get(f"/api/v1/artifacts/{objective['id']}/impact").json()
    impacted_types = {a["type"] for a in impact["impacted_artifacts"]}
    assert {"requirement", "architecture_document", "source_change", "pull_request"} <= impacted_types

    patch = next(a for a in artifacts if a["type"] == "source_patch")
    content = client.get(f"/api/v1/artifacts/{patch['id']}/content").text
    assert "perception_service/health.py" in content

    events = {e["event_type"] for e in client.get("/api/v1/audit", params={"run_id": run_id, "limit": 2000}).json()}
    for expected in ("WORKFLOW_STARTED", "TASK_ASSIGNED", "JOB_CLAIMED", "AGENT_STARTED", "TOOL_CALLED",
                     "ARTIFACT_CREATED", "CRITIC_PASSED", "APPROVAL_REQUESTED", "APPROVAL_GRANTED",
                     "FIX_LOOP_STARTED", "TASK_COMPLETED", "WORKFLOW_COMPLETED"):
        assert expected in events, f"missing audit event {expected}"

    stats = {s["role"]: s for s in client.get("/api/v1/stats/agents").json()}
    assert stats["engineering.developer"]["runs"] == 2
    overview = client.get("/api/v1/overview").json()
    assert overview["waiting_approvals"] == 0 and overview["workers_online"] == 1


def test_whole_software_run_executes_on_one_machine(client, platform, orchestrator, worker, repository_config,
                                                    tmp_path):
    """Two matching machines: requirement -> ... -> merge, fix loop included, never leaves the chosen one."""
    from sqlalchemy import select

    from awf.domain.models import Job
    from awf.worker_sdk import ControlPlaneClient, Worker
    from tests.conftest import ROOT

    other = Worker(ControlPlaneClient(http=client), name="test-linux-02", workspace_root=tmp_path / "ws2",
                   policy_path=ROOT / "config" / "policies.yaml", capabilities=["git", "python"],
                   platform_name="linux-amd64")
    other.register("")
    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config, "labels": {"platform": "linux"}}).json()
    run_id = client.post(f"/api/v1/projects/{project['id']}/workflow-runs", json={
        "inputs": {"objective": "Add a camera pipeline health-check API with tests and documentation."}}).json()["id"]

    final = drive(client, orchestrator, [other, worker], run_id)
    assert final["status"] == "COMPLETED", final.get("error")
    assert final["fix_loops"] == {"review": 1}
    with platform.db.session() as s:
        jobs = s.scalars(select(Job).where(Job.workflow_run_id == run_id)).all()
    assert len(jobs) >= 9 and {j.worker_id for j in jobs} == {final["assigned_worker"]["id"]}


def test_architecture_rejection_reruns_architect_then_fails(client, orchestrator, worker, repository_config):
    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config, "labels": {"platform": "linux"}}).json()
    run_id = client.post(f"/api/v1/projects/{project['id']}/workflow-runs", json={
        "inputs": {"objective": "Add a health check."}}).json()["id"]

    final = drive(client, orchestrator, worker, run_id, approve=False)
    assert final["status"] == "FAILED"
    tasks = client.get(f"/api/v1/workflow-runs/{run_id}/tasks").json()
    arch = next(t for t in tasks if t["stage_id"] == "architecture")
    assert arch["state"] == "FAILED" and arch["attempt"] == 2  # rejected twice: retried once with feedback
    assert not any(t["stage_id"] == "development" for t in tasks)
    states = {t["state"] for t in tasks}
    assert "RUNNING" not in states and "WAITING" not in states
