"""Workflow definition schema (Technical Design §8). Workflows are data, not code (ADR-003)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GateSpec(_Strict):
    type: Literal["human_approval"]
    action: str | None = None
    risk_level: int = 2
    description: str = ""


class FixLoopSpec(_Strict):
    goto: str
    max_iterations: int = Field(default=2, ge=1, le=10)


class WorkerRequirements(_Strict):
    platform: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class ParallelEntry(_Strict):
    id: str | None = None
    role: str | None = None
    tool: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> ParallelEntry:
        if bool(self.role) == bool(self.tool):
            raise ValueError("parallel entry needs exactly one of 'role' or 'tool'")
        return self

    @property
    def key(self) -> str:
        return self.id or (self.role or self.tool or "entry")


class StageSpec(_Strict):
    id: str
    title: str | None = None
    description: str = ""
    role: str | None = None
    tool: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    parallel: list[ParallelEntry] | None = None
    foreach: Literal["planned_task"] | None = None
    depends_on: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    gate: GateSpec | None = None
    on_changes_required: FixLoopSpec | None = None
    # Relation recorded from this stage's output artifacts to its input artifacts.
    relation: str = "derived_from"
    worker: WorkerRequirements | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def _shape(self) -> StageSpec:
        kinds = [k for k in ("role", "tool", "parallel") if getattr(self, k)]
        if len(kinds) > 1:
            raise ValueError(f"stage '{self.id}': only one of role/tool/parallel may be set, got {kinds}")
        if not kinds and not self.gate:
            raise ValueError(f"stage '{self.id}': needs role, tool, parallel or a gate")
        if self.foreach and not self.role:
            raise ValueError(f"stage '{self.id}': foreach requires a role")
        return self

    @property
    def is_gate_only(self) -> bool:
        return not (self.role or self.tool or self.parallel)


class WorkflowSpec(_Strict):
    id: str
    version: str
    name: str | None = None
    description: str = ""
    status: Literal["active", "draft"] = "active"
    # run: one machine executes the whole run (ADR-008). stage: every job may go to any matching machine.
    placement: Literal["run", "stage"] = "run"
    inputs: list[str] = Field(default_factory=list)
    stages: list[StageSpec]

    def stage(self, stage_id: str) -> StageSpec:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise KeyError(stage_id)

    def roles(self) -> set[str]:
        roles: set[str] = set()
        for stage in self.stages:
            if stage.role:
                roles.add(stage.role)
            for entry in stage.parallel or []:
                if entry.role:
                    roles.add(entry.role)
        return roles

    def tools(self) -> set[str]:
        tools: set[str] = set()
        for stage in self.stages:
            if stage.tool:
                tools.add(stage.tool)
            for entry in stage.parallel or []:
                if entry.tool:
                    tools.add(entry.tool)
        return tools
