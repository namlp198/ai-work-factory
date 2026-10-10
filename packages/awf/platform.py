"""Composition root shared by the control API and the orchestrator process."""

from __future__ import annotations

from dataclasses import dataclass

from awf.agent_runtime import AgentRegistry
from awf.domain.db import Database
from awf.model_router import ModelRouter
from awf.orchestrator.artifacts import ArtifactService, build_store
from awf.policy import PolicyConfig, PolicyEngine
from awf.settings import Settings, get_settings
from awf.tool_sdk import JOB_TOOLS
from awf.workflow_engine import WorkflowRegistry


@dataclass
class Platform:
    settings: Settings
    db: Database
    agents: AgentRegistry
    workflows: WorkflowRegistry
    router: ModelRouter
    policy: PolicyEngine
    artifacts: ArtifactService

    @classmethod
    def build(cls, settings: Settings | None = None, db: Database | None = None) -> Platform:
        settings = settings or get_settings()
        agents = AgentRegistry(settings.agents_dir)
        return cls(
            settings=settings,
            db=db or Database(settings.database_url),
            agents=agents,
            workflows=WorkflowRegistry(settings.workflows_dir, known_roles=agents.roles(), known_tools=JOB_TOOLS),
            router=ModelRouter.load(settings.config_dir / "model-routing.yaml", settings.model_provider_override),
            policy=PolicyEngine(PolicyConfig.load(settings.config_dir / "policies.yaml")),
            artifacts=ArtifactService(build_store(settings)),
        )
