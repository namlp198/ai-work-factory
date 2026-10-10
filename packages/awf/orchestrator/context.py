"""L1 run context: builds the task prompt from the task, upstream artifacts, feedback and knowledge.

Only what the task needs goes in (Plan §27 'context bloat'): upstream artifacts from ancestor stages
(latest version only), the task's own feedback, and the knowledge documents named by the profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from awf.agent_runtime import AgentDefinition
from awf.domain.enums import TaskState
from awf.domain.models import Artifact, Project, Task, WorkflowRun
from awf.orchestrator.artifacts import ArtifactService
from awf.workflow_engine import LoadedWorkflow

DEFAULT_INCLUDE = ["objective", "task", "acceptance_criteria", "upstream", "feedback", "workspace"]


@dataclass
class BuiltContext:
    user_message: str
    input_artifact_ids: list[str] = field(default_factory=list)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... clipped by the platform: {len(text) - limit} more characters; " \
                          f"read the file from the workspace or ask for a narrower input ...]"


def upstream_artifacts(session: Session, run: WorkflowRun, stage_ids: set[str]) -> list[Artifact]:
    """Latest version of each artifact produced by DONE tasks of the given stages (current iterations)."""
    iterations = run.context_json.get("stage_iteration", {})
    tasks = session.scalars(select(Task).where(Task.workflow_run_id == run.id, Task.stage_id.in_(stage_ids))).all()
    task_ids = [t.id for t in tasks if t.state == TaskState.DONE and t.iteration == iterations.get(t.stage_id, 1)]
    if not task_ids:
        return []
    rows = session.scalars(select(Artifact).where(Artifact.task_id.in_(task_ids)).order_by(Artifact.created_at)).all()
    latest: dict[str, Artifact] = {}
    for art in rows:
        if art.name not in latest or art.version > latest[art.name].version:
            latest[art.name] = art
    return list(latest.values())


class ContextBuilder:
    def __init__(self, artifacts: ArtifactService, knowledge_dir: Path, max_chars: int = 60000):
        self.artifacts = artifacts
        self.knowledge_dir = knowledge_dir
        self.max_chars = max_chars

    def build(self, session: Session, *, project: Project, run: WorkflowRun, task: Task,
              workflow: LoadedWorkflow, definition: AgentDefinition | None) -> BuiltContext:
        include = (definition.profile.context.include if definition and definition.profile.context.include
                   else DEFAULT_INCLUDE)
        parts: list[str] = []
        input_ids: list[str] = []

        objective = run.input_json.get("objective", "")
        if "objective" in include:
            parts += ["# Objective", "", objective, ""]
            obj_id = run.context_json.get("objective_artifact_id")
            if obj_id:
                input_ids.append(obj_id)

        if "task" in include:
            parts += [f"# Task: {task.title}", "",
                      f"- Stage: `{task.stage_id}` (iteration {task.iteration}, attempt {task.attempt})",
                      f"- Role: `{task.assigned_role}`", ""]
            if task.description:
                parts += ["## Description", "", task.description, ""]

        if "acceptance_criteria" in include and task.acceptance_criteria:
            parts += ["## Acceptance criteria", ""] + [f"- {c}" for c in task.acceptance_criteria] + [""]

        upstream_stages = self._upstream_stages(include, workflow, task.stage_id)
        if upstream_stages:
            arts = upstream_artifacts(session, run, upstream_stages)
            if arts:
                parts += ["# Input artifacts", ""]
                for art in arts:
                    input_ids.append(art.id)
                    body = self.artifacts.read_text(art) if art.size_bytes < 5_000_000 else "(binary/large artifact)"
                    parts += [f"## {art.name} (v{art.version}, type `{art.type}`, stage `{art.stage_id}`)", "",
                              _clip(body, self.max_chars), ""]

        if "feedback" in include:
            feedback = task.input_json.get("feedback") or []
            fb_ids = task.input_json.get("feedback_artifact_ids") or []
            if feedback or fb_ids:
                parts += ["# Feedback to address", "",
                          "A previous attempt or a downstream review requested changes. Address every point.", ""]
                parts += [f"- {item}" for item in feedback] + [""]
                for art in session.scalars(select(Artifact).where(Artifact.id.in_(fb_ids))).all():
                    input_ids.append(art.id)
                    parts += [f"## {art.name} (v{art.version})", "", _clip(self.artifacts.read_text(art), self.max_chars), ""]

        knowledge = definition.profile.context.knowledge if definition else []
        for doc in knowledge:
            path = (self.knowledge_dir / doc).resolve()
            if self.knowledge_dir.resolve() in path.parents and path.is_file():
                parts += [f"# Knowledge: {doc}", "", _clip(path.read_text(encoding="utf-8"), self.max_chars), ""]

        if "workspace" in include:
            repo = project.repository or {}
            parts += ["# Workspace", "",
                      f"- Repository base branch: `{repo.get('base_branch', 'main')}`",
                      f"- Integration branch for this run: `{run.context_json.get('integration_branch')}`",
                      f"- Build command configured: {'yes' if repo.get('build_command') else 'no'}",
                      f"- Test command configured: {'yes' if repo.get('test_command') else 'no'}",
                      f"- Lint command configured: {'yes' if repo.get('lint_command') else 'no'}", ""]

        return BuiltContext(user_message="\n".join(parts).strip() + "\n", input_artifact_ids=list(dict.fromkeys(input_ids)))

    @staticmethod
    def _upstream_stages(include: list[str], workflow: LoadedWorkflow, stage_id: str) -> set[str]:
        ancestors = workflow.graph.ancestors(stage_id)
        if "upstream" in include:
            return ancestors
        named = {i.split(":", 1)[1] for i in include if i.startswith("upstream:")}
        return named & ancestors


def task_meta(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "key": task.task_key,
        "title": task.title,
        "stage_id": task.stage_id,
        "iteration": task.iteration,
        "attempt": task.attempt,
        "acceptance_criteria": task.acceptance_criteria,
    }
