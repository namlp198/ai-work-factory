"""Load and validate workflow YAML files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from awf.workflow_engine.graph import StageGraph, WorkflowValidationError
from awf.workflow_engine.schema import WorkflowSpec


@dataclass(frozen=True)
class LoadedWorkflow:
    spec: WorkflowSpec
    graph: StageGraph
    path: Path | None = None


def parse_workflow(data: dict, known_roles: Iterable[str] | None = None,
                   known_tools: Iterable[str] | None = None, path: Path | None = None) -> LoadedWorkflow:
    try:
        spec = WorkflowSpec.model_validate(data)
    except ValidationError as exc:
        raise WorkflowValidationError(f"{path or data.get('id', '?')}: {exc}") from exc
    graph = StageGraph(spec)
    if spec.status == "active":
        if known_roles is not None:
            missing = spec.roles() - set(known_roles)
            if missing:
                raise WorkflowValidationError(f"workflow '{spec.id}' references unknown roles: {sorted(missing)}")
        if known_tools is not None:
            missing = spec.tools() - set(known_tools)
            if missing:
                raise WorkflowValidationError(f"workflow '{spec.id}' references unknown tools: {sorted(missing)}")
    return LoadedWorkflow(spec=spec, graph=graph, path=path)


class WorkflowRegistry:
    def __init__(self, directory: Path, known_roles: Iterable[str] | None = None,
                 known_tools: Iterable[str] | None = None):
        self.directory = directory
        self._workflows: dict[str, LoadedWorkflow] = {}
        roles = set(known_roles) if known_roles is not None else None
        tools = set(known_tools) if known_tools is not None else None
        for path in sorted(directory.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            loaded = parse_workflow(data, roles, tools, path)
            self._workflows[loaded.spec.id] = loaded

    def get(self, workflow_id: str) -> LoadedWorkflow:
        try:
            return self._workflows[workflow_id]
        except KeyError:
            raise KeyError(f"unknown workflow '{workflow_id}'") from None

    def all(self) -> list[LoadedWorkflow]:
        return list(self._workflows.values())
