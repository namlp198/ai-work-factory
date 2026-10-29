"""Which model and effort each role gets (ADR-015): the strong model only for architecture and planning, harder
work climbs a level, and any task at effort high or xhigh waits for a human's approval first."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from awf.domain.models import Job, Task, WorkflowRun
from awf.model_router import ModelRouter, RouteRequest
from awf.orchestrator.task_router import task_level
from awf.worker_sdk import ControlPlaneClient
from awf.worker_sdk import Worker as WorkerDaemon

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def router(platform):
    return ModelRouter.load(platform.settings.config_dir / "model-routing.yaml")


def _route(router, platform, role, level=0, provider=None):
    profile = platform.agents.get(role).profile
    request = RouteRequest(profile_class=profile.model_policy.class_, fallback_class=profile.model_policy.fallback,
                           task_type=profile.task_type, provider=provider, level=level, level_reason="test")
    route = router.resolve(request)
    return route.model, route.params.get("effort"), route.reason


@pytest.mark.parametrize("role, normal, hard, very_hard", [
    ("engineering.solution-architect", ("claude-opus-5-5", "medium"), ("claude-opus-5-5", "high"), ("claude-opus-5-5", "xhigh")),
    ("control.planner", ("claude-opus-5-5", "medium"), ("claude-opus-5-5", "high"), ("claude-opus-5-5", "xhigh")),
    ("engineering.developer", ("claude-sonnet-5-5", "medium"), ("claude-sonnet-5-5", "high"), ("claude-opus-5-5", "high")),
    ("engineering.code-reviewer", ("claude-sonnet-5-5", "medium"), ("claude-sonnet-5-5", "high"), ("claude-opus-5-5", "high")),
    ("engineering.qa", ("claude-sonnet-5-5", "medium"), ("claude-sonnet-5-5", "high"), ("claude-opus-5-5", "high")),
    ("product.analyst", ("claude-sonnet-5-5", "medium"), ("claude-sonnet-5-5", "high"), ("claude-opus-5-5", "high")),
])
def test_the_strong_model_only_where_thinking_pays(router, platform, role, normal, hard, very_hard):
    assert _route(router, platform, role)[:2] == normal
    model, effort, reason = _route(router, platform, role, level=1)
    assert (model, effort) == hard and "hard (test)" in reason and f"effort {normal[1]} -> {hard[1]}" in reason
    assert _route(router, platform, role, level=2)[:2] == very_hard


def test_effort_never_passes_the_providers_top(router, platform):
    assert _route(router, platform, "engineering.solution-architect", level=2, provider="google")[:2] == (
        "gemini-4-argon", "high")  # Gemini's thinking levels stop at high
    assert _route(router, platform, "engineering.developer", provider="google")[:2] == ("gemini-3.8-flash", "medium")


def test_a_task_climbs_a_level_when_it_was_sent_back():
    run = WorkflowRun(context_json={"complexity": "normal"})
    assert task_level(run, Task(iteration=1, input_json={})) == (0, "")
    assert task_level(run, Task(iteration=2, input_json={})) == (1, "fix loop 2")
    assert task_level(run, Task(iteration=1, input_json={"sent_back": 1})) == (1, "sent back for changes")
    # A timeout or a lost worker is not the work being too hard.
    assert task_level(run, Task(iteration=1, input_json={"feedback": ["previous attempt failed: timeout"]}))[0] == 0
    hard = WorkflowRun(context_json={"complexity": "hard"})
    assert task_level(hard, Task(iteration=1, input_json={})) == (1, "run complexity")
    assert task_level(hard, Task(iteration=3, input_json={}))[0] == 2
    assert task_level(WorkflowRun(context_json={"complexity": "very_hard"}), Task(iteration=3, input_json={}))[0] == 2


# ---------------------------------------------------------------------------- approval before high effort
def _start(client, platform, orchestrator, router, repository_config, tmp_path, complexity):
    platform.router = router
    orchestrator.router.models = router
    box = WorkerDaemon(ControlPlaneClient(http=client), name="box", workspace_root=tmp_path / "w",
                       policy_path=ROOT / "config" / "policies.yaml", platform_name="linux-amd64",
                       capabilities=["git", "python", "llm-anthropic"])
    box.register("")
    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config,
                                                    "labels": {"platform": "linux"}}).json()
    run = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                      json={"complexity": complexity, "inputs": {"objective": "x"}})
    assert run.status_code == 201, run.text
    assert run.json()["complexity"] == complexity
    orchestrator.run_until_idle()
    return run.json()["id"]


def _jobs(platform):
    with platform.db.session() as s:
        return s.scalars(select(Job)).all()


def test_normal_work_never_asks(client, platform, orchestrator, router, repository_config, tmp_path):
    _start(client, platform, orchestrator, router, repository_config, tmp_path, "normal")
    assert client.get("/api/v1/approvals", params={"state": "pending"}).json() == []
    (job,) = _jobs(platform)
    assert job.payload["agent"]["route"]["params"]["effort"] == "medium"


@pytest.mark.parametrize("decision, effort, note", [("approve", "high", ""), ("reject", "medium", "not approved")])
def test_high_effort_waits_for_a_human(client, platform, orchestrator, router, repository_config, tmp_path,
                                       decision, effort, note):
    run_id = _start(client, platform, orchestrator, router, repository_config, tmp_path, "hard")
    assert _jobs(platform) == []  # nothing runs before the answer
    (approval,) = client.get("/api/v1/approvals", params={"state": "pending"}).json()
    assert approval["action"] == "approve_effort" and approval["workflow_run_id"] == run_id
    scope = approval["scope"]
    assert (scope["model"], scope["effort"], scope["role"]) == ("claude-sonnet-5-5", "high", "product.analyst")
    assert "effort high" in scope["description"]
    tasks = client.get(f"/api/v1/workflow-runs/{run_id}/tasks").json()
    assert tasks[0]["state"] == "APPROVAL_REQUIRED"

    client.post(f"/api/v1/approvals/{approval['id']}/{decision}", json={"comment": "ok"}, headers={"X-Actor": "ndev"})
    orchestrator.run_until_idle()
    (job,) = _jobs(platform)
    route = job.payload["agent"]["route"]
    assert route["params"]["effort"] == effort and note in route["reason"]
    assert client.get("/api/v1/approvals", params={"state": "pending"}).json() == []  # asked once
    (task, *_) = client.get(f"/api/v1/workflow-runs/{run_id}/tasks").json()
    assert task["model"]["effort"] == effort and task["model"]["name"] == "claude-sonnet-5-5"


def test_deepseek_reasoning_effort_counts_as_effort(router):
    from awf.model_router import ModelRoute

    route = ModelRoute(model_class="REASONING", provider="deepseek", model="deepseek-v4-pro",
                       params={"extra": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}})
    assert router.effort_gate(route) == "high"
    router.cap_effort(route)
    assert route.params["extra"] == {"thinking": {"type": "enabled"}} and router.effort_gate(route) is None
