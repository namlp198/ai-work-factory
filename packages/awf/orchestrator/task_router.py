"""Task Router (Plan §5.3): role, model class, worker requirements, sandbox mode, approval need."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from awf.agent_runtime import AgentDefinition, AgentRegistry
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
        route = self.models.resolve(RouteRequest(
            profile_class=definition.profile.model_policy.class_,
            fallback_class=definition.profile.model_policy.fallback,
            task_type=definition.profile.task_type,
            confidentiality=project.confidentiality,
            budget_remaining_usd=budget_remaining,
        ))
        write = self.policy.evaluate("fs.write", contract.permissions).allowed and \
            self.policy.evaluate("git.branch_write", contract.permissions).allowed
        return TaskRoute(definition, route, requirements, write=write,
                         needs_approval=gate or contract.human_approval)

    def _requirements(self, stage: StageSpec, project: Project) -> dict[str, Any]:
        repo = project.repository or {}
        platform = (stage.worker.platform if stage.worker and stage.worker.platform else None) \
            or (project.labels or {}).get("platform") or self.default_platform
        caps = {"git"}
        caps.update(repo.get("required_capabilities") or [])
        if stage.worker:
            caps.update(stage.worker.capabilities)
        return {"platform": platform, "capabilities": sorted(caps)}
