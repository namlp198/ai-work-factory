"""Task Router (Plan §5.3): role, model class, worker requirements, sandbox mode, approval need."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from awf.agent_runtime import AgentDefinition, AgentRegistry
from awf.agent_runtime.providers import LLM_PROVIDERS  # providers that need a key on the worker
from awf.domain.models import AgentRun, Project, Task, WorkflowRun
from awf.model_router import ModelRoute, ModelRouter, RouteRequest
from awf.policy import PolicyEngine
from awf.workflow_engine import StageSpec


@dataclass
class TaskRoute:
    definition: AgentDefinition | None
    model_route: ModelRoute | None
    requirements: dict[str, Any] = field(default_factory=dict)
    write: bool = False
    needs_approval: bool = False
    engine: str = "native"


# Coding engines (ADR-011, ADR-012, ADR-013): the provider an engine talks to and what the worker must have.
# `key: False` engines sign in on their own (Antigravity with a Google account, e.g. a Google AI Pro plan; Kimi
# Code with `/login`, MiniMax Code with `mcode login` or a saved key), so they need the CLI but not an API key on the
# worker, and they pick the model themselves. DeepSeek Harness reads DEEPSEEK_API_KEY but chooses its own model.
NATIVE_ENGINE = "native"
CODING_ENGINES: dict[str, dict[str, Any]] = {
    NATIVE_ENGINE: {},
    "claude-code": {"provider": "anthropic", "capabilities": ["claude-code", "llm-anthropic"]},
    "codex": {"provider": "openai", "capabilities": ["codex", "llm-openai"]},
    "antigravity": {"provider": "google", "capabilities": ["antigravity"], "key": False},
    "qwen-code": {"provider": "qwen", "capabilities": ["qwen-code", "llm-qwen"]},
    "kimi-code": {"provider": "kimi", "capabilities": ["kimi-code"], "key": False},
    "deepseek-harness": {"provider": "deepseek", "capabilities": ["deepseek-harness", "llm-deepseek"]},
    "minimax-code": {"provider": "minimax", "capabilities": ["minimax-code"], "key": False},
}


def engine_route(models: ModelRouter, request: RouteRequest, route: ModelRoute, profile_engines: list[str],
                 engine: str | None) -> tuple[str, ModelRoute]:
    """The engine a role runs on, and its model route. An engine applies only to roles whose profile lists
    it, and only when its provider is reachable: a restricted project (LOCAL_PRIVATE) or the offline scripted
    provider keeps the native runtime."""
    if not engine or engine == NATIVE_ENGINE or engine not in profile_engines:
        return NATIVE_ENGINE, route
    provider = CODING_ENGINES[engine]["provider"]
    if route.provider not in (provider, "unavailable", "scripted"):
        route = models.resolve(replace(request, provider=provider))
    if route.provider != provider:
        return NATIVE_ENGINE, route
    note = f"engine {engine}" + ("" if CODING_ENGINES[engine].get("key", True) else " (signed-in CLI, its own model)")
    route.reason = f"{route.reason}; {note}" if route.reason else note
    return engine, route


def engine_capabilities(agents: AgentRegistry, models: ModelRouter, roles: set[str], confidentiality: str,
                        engine: str | None) -> set[str]:
    """What a worker needs for the roles of a workflow that will run on `engine`."""
    if engine not in CODING_ENGINES:
        raise ValueError(f"unknown coding engine '{engine}' (supported: {', '.join(CODING_ENGINES)})")
    caps: set[str] = set()
    for role in roles:
        profile = agents.get(role).profile
        request = RouteRequest(profile_class=profile.model_policy.class_, fallback_class=profile.model_policy.fallback,
                               task_type=profile.task_type, confidentiality=confidentiality)
        chosen, _ = engine_route(models, request, models.resolve(request), profile.engines, engine)
        if chosen != NATIVE_ENGINE:
            caps.update(CODING_ENGINES[chosen]["capabilities"])
    return caps


def llm_capabilities(agents: AgentRegistry, models: ModelRouter, roles: set[str], confidentiality: str,
                     provider: str | None) -> set[str]:
    """llm-<provider> for every provider the agents of a workflow will be routed to."""
    caps: set[str] = set()
    for role in roles:
        profile = agents.get(role).profile
        route = models.resolve(RouteRequest(profile_class=profile.model_policy.class_,
                                            fallback_class=profile.model_policy.fallback,
                                            task_type=profile.task_type, confidentiality=confidentiality,
                                            provider=provider))
        if route.provider in LLM_PROVIDERS:
            caps.add(f"llm-{route.provider}")
    return caps


def slug(text: str, max_len: int = 40) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value[:max_len].strip("-") or "task"


def project_spend(session: Session, project_id: str) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(AgentRun.cost_estimate), 0))
        .join(Task, Task.id == AgentRun.task_id)
        .join(WorkflowRun, WorkflowRun.id == Task.workflow_run_id)
        .where(WorkflowRun.project_id == project_id))
    return Decimal(str(total or 0))


class TaskRouter:
    def __init__(self, agents: AgentRegistry, models: ModelRouter, policy: PolicyEngine, default_platform: str):
        self.agents = agents
        self.models = models
        self.policy = policy
        self.default_platform = default_platform

    def resolve(self, session: Session, task: Task, stage: StageSpec, project: Project) -> TaskRoute:
        requirements = self._requirements(stage, project)
        gate = stage.gate is not None
        if task.task_type == "tool":
            return TaskRoute(None, None, requirements, write=False, needs_approval=gate)
        definition = self.agents.get(task.assigned_role or "")
        contract = definition.contract
        budget_remaining = None
        if project.budget_usd is not None:
            budget_remaining = Decimal(str(project.budget_usd)) - project_spend(session, project.id)
        run = session.get(WorkflowRun, task.workflow_run_id)
        request = RouteRequest(
            profile_class=definition.profile.model_policy.class_,
            fallback_class=definition.profile.model_policy.fallback,
            task_type=definition.profile.task_type,
            confidentiality=project.confidentiality,
            budget_remaining_usd=budget_remaining,
            provider=(run.context_json or {}).get("llm") if run else None,
        )
        engine, route = engine_route(self.models, request, self.models.resolve(request), definition.profile.engines,
                                     (run.context_json or {}).get("coding_engine") if run else None)
        if engine != NATIVE_ENGINE:
            requirements["capabilities"] = sorted(set(requirements["capabilities"])
                                                  | set(CODING_ENGINES[engine]["capabilities"]))
        if route.provider in LLM_PROVIDERS and CODING_ENGINES[engine].get("key", True):
            requirements["capabilities"] = sorted(set(requirements["capabilities"]) | {f"llm-{route.provider}"})
        write = self.policy.evaluate("fs.write", contract.permissions).allowed and \
            self.policy.evaluate("git.branch_write", contract.permissions).allowed
        return TaskRoute(definition, route, requirements, write=write,
                         needs_approval=gate or contract.human_approval, engine=engine)

    def _requirements(self, stage: StageSpec, project: Project) -> dict[str, Any]:
        repo = project.repository or {}
        platform = (stage.worker.platform if stage.worker and stage.worker.platform else None) \
            or (project.labels or {}).get("platform") or self.default_platform
        caps = {"git"} if repo.get("url") else set()
        caps.update(repo.get("required_capabilities") or [])
        if stage.worker:
            caps.update(stage.worker.capabilities)
        return {"platform": platform, "capabilities": sorted(caps)}
