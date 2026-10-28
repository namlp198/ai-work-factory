"""Orchestrator: drives workflow runs through their task graph (Plan §5.1, Design §13).

Each `tick()` advances every active run by applying, in order:
    1. finished jobs        -> artifacts, critic evaluation, task transitions
    2. resolved approvals   -> task transitions
    3. stage verdicts       -> fix loops (re-run upstream stages with feedback)
    4. failure propagation  -> fail the run, cancel what is left
    5. materialization      -> create tasks for stages whose dependencies are done
    6. promotion            -> NEW tasks whose task dependencies are done become READY
    7. dispatch             -> gates request approval; agent/tool tasks become jobs on the queue
    8. completion           -> all stages done => run COMPLETED

The orchestrator never writes code itself; it only routes work and applies results.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from awf.domain.enums import (
    ACTIVE_JOB_STATES,
    TERMINAL_RUN_STATUSES,
    TERMINAL_TASK_STATES,
    AgentRunStatus,
    ApprovalState,
    CriticDecision,
    EventType,
    JobState,
    JobType,
    RunStatus,
    TaskState,
    TaskType,
    WorkerStatus,
)
from awf.domain.models import (
    AgentRun,
    Approval,
    Artifact,
    Job,
    Project,
    Task,
    TaskDependency,
    Worker,
    WorkflowRun,
    new_id,
)
from awf.domain.state_machine import can_transition_task, check_task_transition
from awf.logging import get_logger
from awf.orchestrator import audit
from awf.orchestrator import placement
from awf.orchestrator.jobs import expire_leases, mismatch_reasons, requeue_or_fail, requirements_met
from awf.orchestrator.context import ContextBuilder, task_meta
from awf.orchestrator.critic import CHANGES_REQUIRED, Critic, CriticResult
from awf.orchestrator.task_router import NATIVE_ENGINE, TaskRouter, run_capabilities, slug
from awf.platform import Platform
from awf.workflow_engine import LoadedWorkflow, StageSpec

logger = get_logger("orchestrator")

# Extra time on top of a task's own timeout before a claimed job counts as lost (slow uploads, clone time).
LEASE_MARGIN_SECONDS = 900
PROJECT_ACTIVE = "ACTIVE"
PROJECT_ARCHIVED = "ARCHIVED"

# Outputs of standalone tool jobs: output key -> (artifact type, media type, extension)
TOOL_OUTPUTS: dict[str, dict[str, tuple[str, str, str]]] = {
    "lint.run": {"report": ("static_analysis_report", "application/json", "json")},
    "test.run": {"report": ("test_report", "application/json", "json")},
    "build.run": {"report": ("build_report", "application/json", "json")},
    "git.export_patch": {"patch": ("source_patch", "text/x-diff", "patch"),
                         "pull_request": ("pull_request", "text/markdown", "md")},
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class OrchestratorError(Exception):
    pass


class Orchestrator:
    def __init__(self, platform: Platform):
        self.p = platform
        self.critic = Critic()
        self.router = TaskRouter(platform.agents, platform.router, platform.policy,
                                 platform.settings.default_worker_platform)
        self.context = ContextBuilder(platform.artifacts, platform.settings.knowledge_dir,
                                      platform.settings.context_max_chars_per_artifact)

    # ================================================================== commands
    def update_project(self, session: Session, project: Project, changes: dict[str, Any], actor: str = "admin") -> Project:
        """Partial update; runs already started keep the requirements they were created with."""
        for key, value in changes.items():
            setattr(project, key, value)
        audit.record(session, EventType.PROJECT_UPDATED, actor_type="human", actor_id=actor, project_id=project.id,
                     payload={"fields": sorted(changes), "labels": project.labels,
                              "required_capabilities": (project.repository or {}).get("required_capabilities")})
        return project

    def archive_project(self, session: Session, project: Project, actor: str = "admin") -> Project:
        """Hide a project that has history; its runs, artifacts and audit trail stay readable."""
        running = session.scalar(select(func.count(WorkflowRun.id)).where(
            WorkflowRun.project_id == project.id, WorkflowRun.status == RunStatus.RUNNING)) or 0
        if running:
            raise OrchestratorError(f"project has {running} running run(s); cancel them before archiving")
        project.status = PROJECT_ARCHIVED
        audit.record(session, EventType.PROJECT_ARCHIVED, actor_type="human", actor_id=actor, project_id=project.id,
                     payload={"name": project.name})
        return project

    def restore_project(self, session: Session, project: Project, actor: str = "admin") -> Project:
        project.status = PROJECT_ACTIVE
        audit.record(session, EventType.PROJECT_RESTORED, actor_type="human", actor_id=actor, project_id=project.id,
                     payload={"name": project.name})
        return project

    def delete_project(self, session: Session, project: Project, actor: str = "admin") -> None:
        """Delete a project that never ran. One with runs is archived instead, so nothing it produced is lost."""
        runs = session.scalar(select(func.count(WorkflowRun.id)).where(WorkflowRun.project_id == project.id)) or 0
        if runs:
            raise OrchestratorError(f"project has {runs} run(s) and cannot be deleted; archive it instead")
        audit.record(session, EventType.PROJECT_DELETED, actor_type="human", actor_id=actor, project_id=project.id,
                     payload={"name": project.name, "slug": project.slug})
        session.delete(project)

    def create_project(self, session: Session, *, name: str, slug_value: str | None = None, description: str = "",
                       repository: dict[str, Any] | None = None, confidentiality: str = "internal",
                       budget_usd: float | None = None, labels: dict[str, Any] | None = None,
                       actor: str = "admin") -> Project:
        project = Project(id=new_id(), name=name, slug=slug_value or slug(name, 100), description=description,
                          repository=repository or {}, confidentiality=confidentiality,
                          budget_usd=budget_usd, labels=labels or {})
        session.add(project)
        session.flush()
        audit.record(session, EventType.PROJECT_CREATED, actor_type="human", actor_id=actor, project_id=project.id,
                     payload={"name": name, "slug": project.slug})
        return project

    def start_run(self, session: Session, project: Project, workflow_id: str, inputs: dict[str, Any],
                  actor: str = "admin", worker_name: str | None = None, llm: str | None = None,
                  coding_engine: str | None = None, engine_scope: str | None = None) -> WorkflowRun:
        """Start a run. With run placement (the default) `worker_name` pins it to that machine; otherwise
        the orchestrator picks the least loaded matching machine at the first dispatch. `llm` chooses the model
        provider for every agent of the run (default: the project's labels.llm, then default_provider).
        `coding_engine` runs the roles that support it on another engine, e.g. claude-code (default: the project's
        labels.coding_engine, else native). `engine_scope` `all` runs every role on that engine, e.g. to use a
        signed-in subscription plan instead of API keys (default: labels.engine_scope, else `coding`)."""
        if project.status == PROJECT_ARCHIVED:
            raise OrchestratorError("project is archived; restore it before starting a run")
        workflow = self.p.workflows.get(workflow_id)
        if workflow.spec.status != "active":
            raise OrchestratorError(f"workflow '{workflow_id}' is not active (status {workflow.spec.status})")
        inputs = dict(inputs)
        if "repository" in workflow.spec.inputs and not inputs.get("repository"):
            if not (project.repository or {}).get("url"):
                raise OrchestratorError("workflow needs a repository; set project.repository.url or pass inputs.repository")
            inputs["repository"] = project.repository
        missing = [name for name in workflow.spec.inputs if not inputs.get(name)]
        if missing:
            raise OrchestratorError(f"missing workflow inputs: {missing}")
        provider = llm or (project.labels or {}).get("llm") or self.p.router.default_provider
        if provider not in self.p.router.providers():
            raise OrchestratorError(f"unknown model provider '{provider}' (configured: "
                                    f"{', '.join(self.p.router.providers())})")
        engine = coding_engine or (project.labels or {}).get("coding_engine") or NATIVE_ENGINE
        scope = engine_scope or (project.labels or {}).get("engine_scope") or "coding"
        if scope == "all" and engine == NATIVE_ENGINE:
            raise OrchestratorError("engine scope 'all' needs a coding engine (e.g. claude-code or antigravity)")
        try:
            llm_caps = run_capabilities(self.p.agents, self.p.router, workflow.spec.roles(),
                                        project.confidentiality, provider, engine, scope)
        except ValueError as exc:
            raise OrchestratorError(str(exc)) from None
        placed = workflow.spec.placement == placement.RUN
        requirements: dict[str, Any] = {}
        if placed:
            try:
                requirements = placement.run_requirements(workflow.spec, project, inputs.get("repository"),
                                                          self.p.settings.default_worker_platform, llm_caps)
            except placement.PlacementError as exc:
                raise OrchestratorError(str(exc)) from None
            self._check_placeable(session, requirements, worker_name)
        elif worker_name:
            raise OrchestratorError(f"workflow '{workflow_id}' uses placement: stage; it cannot be pinned to a worker")
        run_id = new_id()
        run = WorkflowRun(
            id=run_id, project_id=project.id, workflow_id=workflow.spec.id, workflow_version=workflow.spec.version,
            status=RunStatus.RUNNING, input_json=inputs, started_at=utcnow(),
            context_json={"stage_iteration": {}, "fix_loops": {}, "integration_branch": f"awf/run-{run_id[:8]}",
                          "placement": {"mode": workflow.spec.placement, "worker": worker_name, "wait": None},
                          "llm": provider, "coding_engine": engine, "engine_scope": scope},
            requirements=requirements,
        )
        session.add(run)
        session.flush()
        objective = self.p.artifacts.create(
            session, project_id=project.id, name=f"runs/{run_id[:8]}/objective.md", type="objective",
            content=f"# Objective\n\n{inputs.get('objective', '')}\n", workflow_run_id=run.id, stage_id="input",
            metadata={"actor": actor})
        self._update_ctx(run, objective_artifact_id=objective.id)
        audit.record(session, EventType.WORKFLOW_STARTED, actor_type="human", actor_id=actor, project_id=project.id,
                     workflow_run_id=run.id, payload={"workflow_id": workflow.spec.id,
                                                      "version": workflow.spec.version,
                                                      "placement": workflow.spec.placement,
                                                      "requirements": requirements, "worker": worker_name,
                                                      "llm": provider, "coding_engine": engine,
                                                      "engine_scope": scope})
        return run

    @staticmethod
    def _check_placeable(session: Session, requirements: dict[str, Any], worker_name: str | None) -> None:
        """Refuse runs no registered machine could ever execute (an Ubuntu box must never get a Windows-only
        .NET project). With no workers registered yet the run is accepted and waits for one."""
        if worker_name:
            worker = session.scalar(select(Worker).where(Worker.name == worker_name))
            if worker is None:
                raise OrchestratorError(f"worker '{worker_name}' is not registered")
            if worker.status == WorkerStatus.REVOKED:
                raise OrchestratorError(f"worker '{worker_name}' is revoked")
            reasons = mismatch_reasons(worker, requirements)
            if reasons:
                raise OrchestratorError(f"worker '{worker_name}' does not meet {placement.describe(requirements)}: "
                                        + "; ".join(reasons))
            return
        workers = session.scalars(select(Worker).where(Worker.status != WorkerStatus.REVOKED)).all()
        if workers and not any(requirements_met(w, requirements) for w in workers):
            registered = "; ".join(f"{w.name}: {', '.join(mismatch_reasons(w, requirements))}" for w in workers)
            raise OrchestratorError(f"no registered worker meets {placement.describe(requirements)} "
                                    f"({registered})")

    def reassign_run(self, session: Session, run: WorkflowRun, worker_name: str | None,
                     actor: str = "admin") -> WorkflowRun:
        """Move a run to `worker_name` (pinned from now on) or, with None, let the orchestrator choose again."""
        if run.status in TERMINAL_RUN_STATUSES:
            raise OrchestratorError(f"run is already {run.status}")
        if not placement.is_run_placed(run):
            raise OrchestratorError("run does not use run placement")
        if run.assigned_worker_id and placement.active_jobs_on(session, run, run.assigned_worker_id):
            raise OrchestratorError("a job of this run is executing; wait for it to finish before moving the run")
        self._check_placeable(session, run.requirements or {}, worker_name)
        placement.set_pinned_worker(run, worker_name)
        reason = f"moved to {worker_name}" if worker_name else "placement reset"
        if run.assigned_worker_id:
            placement.release_run(session, run, f"{reason} by {actor}", actor=actor)
        self._place(session, run, session.get(Project, run.project_id), actor=actor)
        return run

    def cancel_run(self, session: Session, run: WorkflowRun, actor: str = "admin", reason: str = "cancelled") -> None:
        if run.status in TERMINAL_RUN_STATUSES:
            raise OrchestratorError(f"run is already {run.status}")
        self._close_run(session, run, RunStatus.CANCELLED, reason)
        audit.record(session, EventType.WORKFLOW_CANCELLED, actor_type="human", actor_id=actor,
                     project_id=run.project_id, workflow_run_id=run.id, payload={"reason": reason})

    def retry_task(self, session: Session, task: Task, actor: str = "admin") -> None:
        if task.state != TaskState.FAILED:
            raise OrchestratorError(f"only FAILED tasks can be retried (task is {task.state})")
        run = session.get(WorkflowRun, task.workflow_run_id)
        if run.status == RunStatus.CANCELLED:
            raise OrchestratorError("run was cancelled")
        task.max_attempts = max(task.max_attempts, task.attempt + 1)
        self._set_state(task, TaskState.READY)
        if run.status == RunStatus.FAILED:
            run.status = RunStatus.RUNNING
            run.error = None
            run.finished_at = None
        audit.record(session, EventType.TASK_RETRY, actor_type="human", actor_id=actor, project_id=run.project_id,
                     workflow_run_id=run.id, task_id=task.id, payload={"manual": True})

    def resolve_approval(self, session: Session, approval: Approval, approved: bool, actor: str,
                         comment: str | None = None) -> Approval:
        if approval.state != ApprovalState.PENDING:
            raise OrchestratorError(f"approval is already {approval.state}")
        approval.state = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        approval.approved_by = actor
        approval.comment = comment
        approval.resolved_at = utcnow()
        approval.processed = False
        audit.record(session, EventType.APPROVAL_GRANTED if approved else EventType.APPROVAL_REJECTED,
                     actor_type="human", actor_id=actor, project_id=approval.project_id,
                     workflow_run_id=approval.workflow_run_id, task_id=approval.task_id,
                     payload={"approval_id": approval.id, "action": approval.action, "comment": comment})
        return approval

    # ================================================================== loop
    def tick(self) -> bool:
        changed = False
        with self.p.db.transaction() as session:
            changed |= self._check_workers(session)
        with self.p.db.session() as session:
            run_ids = session.scalars(select(WorkflowRun.id).where(WorkflowRun.status == RunStatus.RUNNING)).all()
        for run_id in run_ids:
            try:
                with self.p.db.transaction() as session:
                    run = session.get(WorkflowRun, run_id)
                    if run is not None and run.status == RunStatus.RUNNING:
                        changed |= self._advance_run(session, run)
            except Exception:
                logger.exception("failed to advance run %s", run_id)
        return changed

    def run_until_idle(self, max_ticks: int = 100) -> int:
        for i in range(max_ticks):
            if not self.tick():
                return i
        return max_ticks

    def _advance_run(self, session: Session, run: WorkflowRun) -> bool:
        workflow = self.p.workflows.get(run.workflow_id)
        project = session.get(Project, run.project_id)
        changed = self._process_jobs(session, run, workflow)
        changed |= self._process_approvals(session, run)
        if run.status != RunStatus.RUNNING:
            return changed
        changed |= self._evaluate_stage_verdicts(session, run, workflow)
        if self._propagate_failure(session, run):
            return True
        changed |= self._materialize(session, run, workflow)
        changed |= self._promote(session, run)
        changed |= self._ensure_placement(session, run, project)
        changed |= self._dispatch(session, run, workflow, project)
        changed |= self._check_completion(session, run, workflow)
        return changed

    # ================================================================== helpers
    @staticmethod
    def _update_ctx(run: WorkflowRun, **changes: Any) -> dict[str, Any]:
        ctx = copy.deepcopy(run.context_json or {})
        ctx.update(changes)
        run.context_json = ctx
        return ctx

    @staticmethod
    def _iteration(run: WorkflowRun, stage_id: str) -> int:
        return int((run.context_json or {}).get("stage_iteration", {}).get(stage_id, 1))

    @staticmethod
    def _set_state(task: Task, target: TaskState) -> None:
        if task.state == target:
            return
        task.state = check_task_transition(task.state, target)

    @staticmethod
    def _merge_output(task: Task, **values: Any) -> None:
        task.output_json = {**(task.output_json or {}), **values}

    def _tasks(self, session: Session, run: WorkflowRun) -> list[Task]:
        return list(session.scalars(select(Task).where(Task.workflow_run_id == run.id).order_by(Task.created_at)))

    def _current_tasks(self, session: Session, run: WorkflowRun) -> list[Task]:
        return [t for t in self._tasks(session, run) if t.iteration == self._iteration(run, t.stage_id)]

    @staticmethod
    def _stage_status(tasks: list[Task], stage_id: str) -> str:
        stage_tasks = [t for t in tasks if t.stage_id == stage_id]
        if not stage_tasks:
            return "pending"
        if all(t.state == TaskState.DONE for t in stage_tasks):
            return "done"
        if any(t.state == TaskState.FAILED for t in stage_tasks):
            return "failed"
        return "active"

    def stage_overview(self, session: Session, run: WorkflowRun) -> list[dict[str, Any]]:
        workflow = self.p.workflows.get(run.workflow_id)
        current = self._current_tasks(session, run)
        result = []
        for stage in workflow.spec.stages:
            result.append({
                "id": stage.id,
                "title": stage.title or stage.id,
                "depends_on": stage.depends_on,
                "iteration": self._iteration(run, stage.id),
                "status": self._stage_status(current, stage.id),
                "kind": "gate" if stage.is_gate_only else ("tool" if stage.tool else
                                                            "parallel" if stage.parallel else "agent"),
                "gate": stage.gate.type if stage.gate else None,
            })
        return result

    def _fail_task(self, session: Session, run: WorkflowRun, task: Task, reason: str,
                   event: EventType = EventType.TASK_FAILED) -> None:
        self._set_state(task, TaskState.FAILED)
        self._merge_output(task, failure_reason=reason)
        audit.record(session, event, project_id=run.project_id, workflow_run_id=run.id, task_id=task.id,
                     payload={"reason": reason, "title": task.title})

    def _retry_or_fail(self, session: Session, run: WorkflowRun, task: Task, feedback: list[str]) -> None:
        """Re-run a task with feedback if it has attempts left, otherwise fail it."""
        if task.attempt < task.max_attempts:
            if can_transition_task(task.state, TaskState.CHANGES_REQUIRED):
                self._set_state(task, TaskState.CHANGES_REQUIRED)
            self._set_state(task, TaskState.READY)
            task.input_json = {**task.input_json, "feedback": feedback}
            audit.record(session, EventType.TASK_RETRY, project_id=run.project_id, workflow_run_id=run.id,
                         task_id=task.id, payload={"attempt": task.attempt, "feedback": feedback[:10]})
        else:
            self._fail_task(session, run, task, "; ".join(feedback)[:2000] or "attempts exhausted")

    # ================================================================== 1. jobs
    def _process_jobs(self, session: Session, run: WorkflowRun, workflow: LoadedWorkflow) -> bool:
        jobs = session.scalars(select(Job).where(
            Job.workflow_run_id == run.id, Job.processed.is_(False),
            Job.state.in_([JobState.SUCCESS, JobState.FAILED]))).all()
        for job in jobs:
            job.processed = True
            task = session.get(Task, job.task_id)
            if task.state in TERMINAL_TASK_STATES:
                continue
            if job.state == JobState.SUCCESS:
                self._on_job_success(session, run, workflow, job, task)
            else:
                self._on_job_failure(session, run, job, task)
        return bool(jobs)

    def _on_job_failure(self, session: Session, run: WorkflowRun, job: Job, task: Task) -> None:
        agent_run = session.get(AgentRun, job.agent_run_id) if job.agent_run_id else None
        self._record_agent_run(session, run, job, agent_run, failed=True)
        if job.retryable and job.attempt < job.max_attempts:
            job.state = JobState.RETRYABLE
            job.state = JobState.QUEUED
            job.attempt += 1
            job.worker_id = None
            job.processed = False
            if agent_run:
                agent_run.status = AgentRunStatus.QUEUED
                agent_run.retry_count += 1
                agent_run.error = job.error
            self._set_state(task, TaskState.WAITING)
            audit.record(session, EventType.JOB_REQUEUED, project_id=run.project_id, workflow_run_id=run.id,
                         task_id=task.id, payload={"job_id": job.id, "attempt": job.attempt, "error": job.error})
            return
        job.state = JobState.TERMINAL
        if agent_run:
            agent_run.status = AgentRunStatus.FAILED
            agent_run.error = job.error
        self._retry_or_fail(session, run, task, [f"previous attempt failed: {job.error}"])

    def _record_agent_run(self, session: Session, run: WorkflowRun, job: Job, agent_run: AgentRun | None,
                          failed: bool = False) -> None:
        result = job.result or {}
        calls = result.get("tool_calls") or []
        if agent_run:
            usage = result.get("usage") or {}
            agent_run.token_input += int(usage.get("input_tokens") or 0)
            agent_run.token_output += int(usage.get("output_tokens") or 0)
            if usage.get("cost_usd") is not None:  # an engine that reports its own spend (claude-code)
                agent_run.cost_estimate = (Decimal(str(agent_run.cost_estimate or 0))
                                           + Decimal(str(usage["cost_usd"]))).quantize(Decimal("0.000001"))
            else:
                agent_run.cost_estimate = self.p.router.estimate_cost(
                    agent_run.model_name, agent_run.token_input, agent_run.token_output)
            agent_run.duration_ms += int(result.get("duration_ms") or 0)
            agent_run.tool_calls = calls
            agent_run.output_json = {k: v for k, v in result.items()
                                     if k in ("summary", "decision", "findings", "evidence", "commit", "usage")}
            agent_run.finished_at = utcnow()
            if not failed:
                agent_run.status = AgentRunStatus.SUCCEEDED
        if calls:
            audit.record(session, EventType.TOOL_CALLED, actor_type="agent",
                         actor_id=agent_run.role if agent_run else "tool-job", project_id=run.project_id,
                         workflow_run_id=run.id, task_id=job.task_id,
                         payload={"job_id": job.id, "count": len(calls),
                                  "tools": sorted({c.get("tool", "?") for c in calls})})
        for call in calls:
            verdict = (call.get("policy") or {}).get("verdict")
            if verdict in ("DENY", "REQUIRE_APPROVAL"):
                audit.record(session, EventType.POLICY_DENIED, actor_type="agent",
                             actor_id=agent_run.role if agent_run else "tool-job", project_id=run.project_id,
                             workflow_run_id=run.id, task_id=job.task_id,
                             payload={"tool": call.get("tool"), "policy": call.get("policy")})
            elif verdict == "ALLOW_NOTIFY":
                audit.record(session, EventType.POLICY_NOTIFY, actor_type="agent",
                             actor_id=agent_run.role if agent_run else "tool-job", project_id=run.project_id,
                             workflow_run_id=run.id, task_id=job.task_id,
                             payload={"tool": call.get("tool"), "policy": call.get("policy")})

    def _store_outputs(self, session: Session, run: WorkflowRun, stage: StageSpec, task: Task, job: Job,
                       outputs: dict[str, Any], specs: dict[str, tuple[str, str, str]]) -> list[str]:
        ids: list[str] = []
        inputs = job.payload.get("input_artifact_ids") or []
        for name, (atype, media, ext) in specs.items():
            value = outputs.get(name)
            if value is None or value == "" or value == {}:
                continue
            content = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
            art = self.p.artifacts.create(
                session, project_id=run.project_id, workflow_run_id=run.id, task_id=task.id,
                agent_run_id=job.agent_run_id, stage_id=task.stage_id,
                name=f"runs/{run.id[:8]}/{task.stage_id}/{slug(task.task_key, 60)}/{name}.{ext}",
                type=atype, media_type=media, content=content, input_artifact_ids=inputs,
                relation_type=stage.relation,
                metadata={"output_name": name, "iteration": task.iteration, "attempt": task.attempt})
            ids.append(art.id)
        return ids

    def _on_job_success(self, session: Session, run: WorkflowRun, workflow: LoadedWorkflow, job: Job,
                        task: Task) -> None:
        stage = workflow.spec.stage(task.stage_id)
        result = job.result or {}
        agent_run = session.get(AgentRun, job.agent_run_id) if job.agent_run_id else None
        self._record_agent_run(session, run, job, agent_run)
        self._set_state(task, TaskState.REVIEW)

        definition = None
        if task.task_type == TaskType.TOOL:
            tool_name = task.input_json["tool"]
            artifact_ids = self._store_outputs(session, run, stage, task, job, result.get("outputs") or {},
                                               TOOL_OUTPUTS.get(tool_name, {}))
            critic = self.critic.evaluate_tool(tool_name, result)
        else:
            definition = self.p.agents.get(task.assigned_role)
            contract = definition.contract
            specs = {o.name: (o.resolved_artifact_type, o.media_type, o.extension) for o in contract.outputs}
            artifact_ids = self._store_outputs(session, run, stage, task, job, result.get("outputs") or {}, specs)
            critic = self.critic.evaluate_agent(contract, result)
        if agent_run:
            agent_run.critic_json = critic.as_dict()
        self._merge_output(task, critic=critic.as_dict(), artifact_ids=artifact_ids, summary=result.get("summary"),
                           decision=result.get("decision"), agent_findings=result.get("findings") or [],
                           commit=result.get("commit"))
        self._apply_critic(session, run, stage, task, critic, artifact_ids, definition, result)

    def _apply_critic(self, session: Session, run: WorkflowRun, stage: StageSpec, task: Task, critic: CriticResult,
                      artifact_ids: list[str], definition, result: dict[str, Any]) -> None:
        if critic.decision == CriticDecision.BLOCK:
            audit.record(session, EventType.CRITIC_BLOCKED, actor_type="critic", actor_id="critic",
                         project_id=run.project_id, workflow_run_id=run.id, task_id=task.id, payload=critic.as_dict())
            self._fail_task(session, run, task, "critic blocked: " + "; ".join(
                f.message for f in critic.findings if f.severity == "BLOCKER"))
            return
        if critic.decision == CriticDecision.CHANGES_REQUIRED:
            audit.record(session, EventType.TASK_CHANGES_REQUIRED, actor_type="critic", actor_id="critic",
                         project_id=run.project_id, workflow_run_id=run.id, task_id=task.id, payload=critic.as_dict())
            feedback = [f"[{f.severity}/{f.code}] {f.message}" for f in critic.findings if f.severity in ("MAJOR", "BLOCKER")]
            self._retry_or_fail(session, run, task, feedback)
            return

        audit.record(session, EventType.CRITIC_PASSED, actor_type="critic", actor_id="critic",
                     project_id=run.project_id, workflow_run_id=run.id, task_id=task.id,
                     payload={"verdict": critic.verdict, "findings": len(critic.findings)})
        if definition is not None:
            self._capture_plan(run, definition, result)

        if critic.verdict == CHANGES_REQUIRED:
            # Output is valid, but the verdict asks for changes upstream: handled at stage level.
            self._set_state(task, TaskState.CHANGES_REQUIRED)
            self._merge_output(task, verdict_changes=True)
            return
        needs_approval = stage.gate is not None or bool(definition and definition.contract.human_approval)
        if needs_approval:
            self._request_approval(session, run, stage, task, artifact_ids)
            return
        self._set_state(task, TaskState.DONE)
        audit.record(session, EventType.TASK_COMPLETED, project_id=run.project_id, workflow_run_id=run.id,
                     task_id=task.id, payload={"artifact_ids": artifact_ids})

    def _capture_plan(self, run: WorkflowRun, definition, result: dict[str, Any]) -> None:
        for out in definition.contract.agent_outputs():
            if out.type == "json" and out.schema_ and "tasks" in (out.schema_.get("properties") or {}):
                graph = (result.get("outputs") or {}).get(out.name)
                if isinstance(graph, str):
                    graph = json.loads(graph)
                self._update_ctx(run, planned_tasks=graph.get("tasks", []))

    def _request_approval(self, session: Session, run: WorkflowRun, stage: StageSpec, task: Task,
                          artifact_ids: list[str]) -> Approval:
        self._set_state(task, TaskState.APPROVAL_REQUIRED)
        gate = stage.gate
        approval = Approval(project_id=run.project_id, workflow_run_id=run.id, task_id=task.id,
                            action=(gate.action if gate and gate.action else f"approve_{stage.id}"),
                            risk_level=gate.risk_level if gate else 2,
                            scope={"project_id": run.project_id, "workflow_run_id": run.id,
                                   "stage_id": stage.id, "artifact_ids": artifact_ids,
                                   "description": gate.description if gate else ""})
        session.add(approval)
        session.flush()
        audit.record(session, EventType.APPROVAL_REQUESTED, project_id=run.project_id, workflow_run_id=run.id,
                     task_id=task.id, payload={"approval_id": approval.id, "action": approval.action,
                                               "artifact_ids": artifact_ids})
        return approval

    # ================================================================== 2. approvals
    def _process_approvals(self, session: Session, run: WorkflowRun) -> bool:
        approvals = session.scalars(select(Approval).where(
            Approval.workflow_run_id == run.id, Approval.processed.is_(False),
            Approval.state.in_([ApprovalState.APPROVED, ApprovalState.REJECTED]))).all()
        for approval in approvals:
            approval.processed = True
            task = session.get(Task, approval.task_id) if approval.task_id else None
            if task is None or task.state != TaskState.APPROVAL_REQUIRED:
                continue
            if approval.state == ApprovalState.APPROVED:
                self._set_state(task, TaskState.DONE)
                audit.record(session, EventType.TASK_COMPLETED, project_id=run.project_id, workflow_run_id=run.id,
                             task_id=task.id, payload={"approval_id": approval.id})
            elif task.task_type == TaskType.GATE:
                self._fail_task(session, run, task, f"rejected by {approval.approved_by}: {approval.comment or ''}")
            else:
                self._retry_or_fail(session, run, task, [
                    f"Human reviewer {approval.approved_by} rejected the result: {approval.comment or 'no comment'}"])
        return bool(approvals)

    # ================================================================== 3. stage verdicts / fix loops
    def _evaluate_stage_verdicts(self, session: Session, run: WorkflowRun, workflow: LoadedWorkflow) -> bool:
        changed = False
        current = self._current_tasks(session, run)
        for stage in workflow.spec.stages:
            stage_tasks = [t for t in current if t.stage_id == stage.id]
            flagged = [t for t in stage_tasks if t.state == TaskState.CHANGES_REQUIRED
                       and (t.output_json or {}).get("verdict_changes") and not t.output_json.get("superseded")]
            if not flagged:
                continue
            settled = all(t.state == TaskState.DONE or t in flagged for t in stage_tasks)
            if not settled:
                continue
            changed = True
            loop = stage.on_changes_required
            used = int((run.context_json.get("fix_loops") or {}).get(stage.id, 0))
            if loop is None or used >= loop.max_iterations:
                reason = (f"stage '{stage.id}' still requires changes after {used} fix iteration(s)" if loop
                          else f"stage '{stage.id}' requires changes and defines no fix loop")
                for t in flagged:
                    self._fail_task(session, run, t, reason)
                continue
            self._start_fix_loop(session, run, workflow, stage, loop.goto, flagged, used + 1)
        return changed

    def _start_fix_loop(self, session: Session, run: WorkflowRun, workflow: LoadedWorkflow, stage: StageSpec,
                        goto: str, flagged: list[Task], loop_no: int) -> None:
        span = workflow.graph.loop_span(goto, stage.id)
        iterations = dict(run.context_json.get("stage_iteration") or {})
        for sid in span:
            iterations[sid] = iterations.get(sid, 1) + 1
        notes: list[str] = []
        artifact_ids: list[str] = []
        for t in flagged:
            self._merge_output(t, superseded=True)
            artifact_ids += (t.output_json or {}).get("artifact_ids") or []
            for f in (t.output_json or {}).get("agent_findings") or []:
                where = f" ({f['file']})" if f.get("file") else ""
                notes.append(f"[{f.get('severity', '?')}] {f.get('message', '')}{where}")
            for f in ((t.output_json or {}).get("critic") or {}).get("findings") or []:
                if f.get("code", "").endswith("_FAILED") or f.get("code", "").startswith("VERDICT_"):
                    notes.append(f"[{f.get('code')}] {f.get('message')}")
        loops = dict(run.context_json.get("fix_loops") or {})
        loops[stage.id] = loop_no
        self._update_ctx(run, stage_iteration=iterations, fix_loops=loops, fix_feedback={
            "stage": goto, "iteration": iterations[goto], "from_stage": stage.id,
            "artifact_ids": artifact_ids, "notes": notes})
        audit.record(session, EventType.FIX_LOOP_STARTED, project_id=run.project_id, workflow_run_id=run.id,
                     payload={"from_stage": stage.id, "goto": goto, "loop": loop_no, "stages": span,
                              "notes": notes[:20]})

    # ================================================================== 4. failure propagation
    def _propagate_failure(self, session: Session, run: WorkflowRun) -> bool:
        failed = [t for t in self._current_tasks(session, run) if t.state == TaskState.FAILED]
        if not failed:
            return False
        reason = "; ".join(f"{t.stage_id}/{t.task_key}: {(t.output_json or {}).get('failure_reason', 'failed')}"
                           for t in failed)
        self._close_run(session, run, RunStatus.FAILED, reason[:4000])
        audit.record(session, EventType.WORKFLOW_FAILED, project_id=run.project_id, workflow_run_id=run.id,
                     payload={"reason": reason[:2000]})
        return True

    def _close_run(self, session: Session, run: WorkflowRun, status: RunStatus, reason: str) -> None:
        run.status = status
        run.error = reason if status != RunStatus.COMPLETED else None
        run.finished_at = utcnow()
        for task in self._tasks(session, run):
            if (task.output_json or {}).get("superseded"):
                continue  # keep fix-loop history as it was (CHANGES_REQUIRED)
            if task.state not in TERMINAL_TASK_STATES and can_transition_task(task.state, TaskState.CANCELLED):
                task.state = TaskState.CANCELLED
        for job in session.scalars(select(Job).where(Job.workflow_run_id == run.id)):
            if job.state == JobState.QUEUED or job.state in ACTIVE_JOB_STATES:
                job.state = JobState.CANCELLED
                job.processed = True
                if job.agent_run_id:
                    ar = session.get(AgentRun, job.agent_run_id)
                    if ar and ar.status in (AgentRunStatus.QUEUED, AgentRunStatus.RUNNING):
                        ar.status = AgentRunStatus.CANCELLED
        for approval in session.scalars(select(Approval).where(Approval.workflow_run_id == run.id,
                                                               Approval.state == ApprovalState.PENDING)):
            approval.state = ApprovalState.EXPIRED
            approval.processed = True
            approval.resolved_at = utcnow()

    # ================================================================== 5. materialization
    def _materialize(self, session: Session, run: WorkflowRun, workflow: LoadedWorkflow) -> bool:
        all_tasks = self._tasks(session, run)
        current = [t for t in all_tasks if t.iteration == self._iteration(run, t.stage_id)]
        existing = {(t.stage_id, t.iteration) for t in all_tasks}
        changed = False
        for sid in workflow.graph.order:
            iteration = self._iteration(run, sid)
            if (sid, iteration) in existing:
                continue
            stage = workflow.spec.stage(sid)
            if not all(self._stage_status(current, dep) == "done" for dep in stage.depends_on):
                continue
            created = self._create_stage_tasks(session, run, stage, iteration)
            current += created
            existing.add((sid, iteration))
            changed = True
            audit.record(session, EventType.STAGE_MATERIALIZED, project_id=run.project_id, workflow_run_id=run.id,
                         payload={"stage": sid, "iteration": iteration, "tasks": [t.title for t in created]})
        return changed

    def _max_attempts(self, stage: StageSpec, role: str | None) -> int:
        if stage.max_attempts:
            return stage.max_attempts
        if role:
            return self.p.agents.get(role).contract.retry_policy.max_attempts
        return 1

    def _new_task(self, session: Session, run: WorkflowRun, stage: StageSpec, *, key: str, title: str,
                  task_type: TaskType, role: str | None, iteration: int, description: str = "",
                  criteria: list[str] | None = None, input_json: dict[str, Any] | None = None) -> Task:
        payload = dict(input_json or {})
        fb = run.context_json.get("fix_feedback") or {}
        if fb.get("stage") == stage.id and fb.get("iteration") == iteration:
            payload.setdefault("feedback", fb.get("notes") or [])
            payload["feedback_artifact_ids"] = fb.get("artifact_ids") or []
        task = Task(id=new_id(), workflow_run_id=run.id, stage_id=stage.id, task_key=key, task_type=task_type,
                    title=title[:500], description=description, assigned_role=role, iteration=iteration,
                    acceptance_criteria=criteria or [], input_json=payload, state=TaskState.NEW,
                    max_attempts=self._max_attempts(stage, role) if task_type != TaskType.GATE else 1)
        session.add(task)
        return task

    def _create_stage_tasks(self, session: Session, run: WorkflowRun, stage: StageSpec, iteration: int) -> list[Task]:
        title = stage.title or stage.id.replace("_", " ").title()
        created: list[Task] = []
        if stage.is_gate_only:
            created.append(self._new_task(session, run, stage, key=stage.id, title=f"Approval: {title}",
                                          task_type=TaskType.GATE, role=None, iteration=iteration,
                                          description=stage.gate.description if stage.gate else ""))
        elif stage.tool:
            created.append(self._new_task(session, run, stage, key=stage.id, title=title, task_type=TaskType.TOOL,
                                          role=None, iteration=iteration, description=stage.description,
                                          input_json={"tool": stage.tool, "tool_input": stage.tool_input}))
        elif stage.parallel:
            for entry in stage.parallel:
                if entry.tool:
                    created.append(self._new_task(
                        session, run, stage, key=f"{stage.id}:{entry.key}", title=entry.title or f"{title}: {entry.tool}",
                        task_type=TaskType.TOOL, role=None, iteration=iteration,
                        input_json={"tool": entry.tool, "tool_input": entry.tool_input}))
                else:
                    created.append(self._new_task(
                        session, run, stage, key=f"{stage.id}:{entry.key}", title=entry.title or f"{title}: {entry.role}",
                        task_type=TaskType.AGENT, role=entry.role, iteration=iteration, description=stage.description))
        elif stage.foreach == "planned_task":
            created += self._create_planned_tasks(session, run, stage, iteration, title)
        else:
            created.append(self._new_task(session, run, stage, key=stage.id, title=title, task_type=TaskType.AGENT,
                                          role=stage.role, iteration=iteration,
                                          description=stage.description or run.input_json.get("objective", "")))
        session.flush()
        return created

    def _create_planned_tasks(self, session: Session, run: WorkflowRun, stage: StageSpec, iteration: int,
                              title: str) -> list[Task]:
        if iteration > 1:
            return [self._new_task(
                session, run, stage, key=f"{stage.id}:fix-{iteration}",
                title=f"Address review findings (iteration {iteration})", task_type=TaskType.AGENT, role=stage.role,
                iteration=iteration,
                description="Fix every issue raised by the downstream stage. Keep changes minimal and add tests "
                            "that prove the fix.",
                criteria=["All findings in the feedback are resolved", "Build and unit tests pass"])]
        planned = run.context_json.get("planned_tasks") or []
        if not planned:
            planned = [{"key": "T1", "title": run.input_json.get("objective", title), "description": "",
                        "acceptance_criteria": [], "depends_on": []}]
        by_key: dict[str, Task] = {}
        for item in planned:
            task = self._new_task(session, run, stage, key=f"{stage.id}:{item['key']}",
                                  title=item.get("title") or item["key"], task_type=TaskType.AGENT, role=stage.role,
                                  iteration=iteration, description=item.get("description", ""),
                                  criteria=list(item.get("acceptance_criteria") or []),
                                  input_json={"planned_task": item})
            by_key[item["key"]] = task
        session.flush()
        for item in planned:
            for dep in item.get("depends_on") or []:
                if dep in by_key:
                    session.add(TaskDependency(task_id=by_key[item["key"]].id, depends_on_task_id=by_key[dep].id))
        return list(by_key.values())

    # ================================================================== 6. promotion
    def _promote(self, session: Session, run: WorkflowRun) -> bool:
        changed = False
        for task in self._current_tasks(session, run):
            if task.state != TaskState.NEW:
                continue
            deps = session.scalars(select(TaskDependency.depends_on_task_id)
                                   .where(TaskDependency.task_id == task.id)).all()
            if all(session.get(Task, d).state == TaskState.DONE for d in deps):
                self._set_state(task, TaskState.READY)
                audit.record(session, EventType.TASK_READY, project_id=run.project_id, workflow_run_id=run.id,
                             task_id=task.id, payload={"title": task.title, "role": task.assigned_role})
                changed = True
        return changed

    # ================================================================== 7. dispatch
    def _dispatch(self, session: Session, run: WorkflowRun, workflow: LoadedWorkflow, project: Project) -> bool:
        changed = False
        for task in self._current_tasks(session, run):
            if task.state != TaskState.READY:
                continue
            stage = workflow.spec.stage(task.stage_id)
            if task.task_type == TaskType.GATE:
                changed = True
                self._request_approval(session, run, stage, task, self._stage_input_artifacts(session, run, workflow, stage))
                continue
            if placement.is_run_placed(run) and not run.assigned_worker_id:
                continue  # waiting for a machine (see _ensure_placement); the task stays READY
            changed = True
            try:
                self._dispatch_job(session, run, workflow, project, stage, task)
            except Exception as exc:  # routing/config errors fail the task, not the orchestrator
                logger.exception("dispatch failed for task %s", task.id)
                self._fail_task(session, run, task, f"dispatch failed: {exc}")
        return changed

    def _stage_input_artifacts(self, session: Session, run: WorkflowRun, workflow: LoadedWorkflow,
                               stage: StageSpec) -> list[str]:
        from awf.orchestrator.context import upstream_artifacts

        return [a.id for a in upstream_artifacts(session, run, set(stage.depends_on))]

    def _dispatch_job(self, session: Session, run: WorkflowRun, workflow: LoadedWorkflow, project: Project,
                      stage: StageSpec, task: Task) -> Job:
        route = self.router.resolve(session, task, stage, project)
        repository = run.input_json.get("repository") or project.repository or {}
        task.attempt += 1
        integration = run.context_json.get("integration_branch")
        task_branch = f"awf/tasks/{run.id[:8]}/{slug(task.task_key, 40)}-i{task.iteration}-a{task.attempt}"
        built = self.context.build(session, project=project, run=run, task=task, workflow=workflow,
                                   definition=route.definition)
        payload: dict[str, Any] = {
            "job_type": JobType.TOOL if task.task_type == TaskType.TOOL else JobType.AGENT,
            "project": {"id": project.id, "slug": project.slug},
            "workflow_run_id": run.id,
            "task": task_meta(task),
            "repository": repository,
            "workspace": {"integration_branch": integration, "task_branch": task_branch, "write": route.write},
            "context": {"user_message": built.user_message, "objective": run.input_json.get("objective", ""),
                        "trace_summary": self._trace_summary(session, run)},
            "input_artifact_ids": built.input_artifact_ids,
        }
        if route.definition is not None:
            lease = route.definition.contract.timeout_policy.seconds + LEASE_MARGIN_SECONDS
        else:
            lease = 3 * self.p.policy.config.limit("command_timeout_seconds", 600) + LEASE_MARGIN_SECONDS
        payload["limits"] = {"lease_seconds": lease}
        agent_run = None
        if task.task_type == TaskType.TOOL:
            payload["tool"] = {"name": task.input_json["tool"], "input": task.input_json.get("tool_input") or {}}
        else:
            definition, model = route.definition, route.model_route
            if model.provider == "unavailable":
                raise OrchestratorError(f"no model available for class {model.model_class} ({model.reason})")
            payload["agent"] = {
                "role": definition.role,
                "profile_version": definition.profile.version,
                "contract": definition.contract.model_dump(by_alias=True),
                "system_prompt": definition.system_prompt,
                "route": model.as_dict(),
                "engine": route.engine,
            }
            agent_run = AgentRun(id=new_id(), task_id=task.id, role=definition.role,
                                 profile_version=f"{definition.profile.version}/{definition.contract.version}",
                                 model_class=model.model_class, model_provider=model.provider, model_name=model.model,
                                 route_reason=model.reason, status=AgentRunStatus.QUEUED,
                                 input_json={"input_artifact_ids": built.input_artifact_ids,
                                             "task": task_meta(task), "context_chars": len(built.user_message)})
            session.add(agent_run)
        placed = placement.is_run_placed(run)
        job = Job(id=new_id(), task_id=task.id, workflow_run_id=run.id, agent_run_id=agent_run.id if agent_run else None,
                  job_type=payload["job_type"], payload=payload,
                  requirements=run.requirements if placed else route.requirements,
                  pinned=placed, assigned_worker_id=run.assigned_worker_id if placed else None,
                  state=JobState.QUEUED, max_attempts=self.p.settings.job_max_attempts)
        session.add(job)
        if agent_run:
            agent_run.job_id = job.id
        self._set_state(task, TaskState.WAITING)
        audit.record(session, EventType.TASK_ASSIGNED, project_id=run.project_id, workflow_run_id=run.id,
                     task_id=task.id, payload={
                         "job_id": job.id, "role": task.assigned_role, "attempt": task.attempt,
                         "requirements": job.requirements, "worker_id": job.assigned_worker_id,
                         "model": route.model_route.as_dict() if route.model_route else None})
        return job

    def _trace_summary(self, session: Session, run: WorkflowRun) -> str:
        arts = session.scalars(select(Artifact).where(Artifact.workflow_run_id == run.id)
                               .order_by(Artifact.created_at)).all()
        latest: dict[str, Artifact] = {}
        for a in arts:
            latest[a.name] = a
        return "\n".join(f"- `{a.type}` {a.name} v{a.version} ({a.checksum[:19]})" for a in latest.values())

    # ================================================================== 8. completion
    def _check_completion(self, session: Session, run: WorkflowRun, workflow: LoadedWorkflow) -> bool:
        current = self._current_tasks(session, run)
        if all(self._stage_status(current, s.id) == "done" for s in workflow.spec.stages):
            self._close_run(session, run, RunStatus.COMPLETED, "")
            audit.record(session, EventType.WORKFLOW_COMPLETED, project_id=run.project_id, workflow_run_id=run.id,
                         payload={"tasks": len(current)})
            return True
        return False

    # ================================================================== worker liveness
    def _check_workers(self, session: Session) -> bool:
        timeout = timedelta(seconds=self.p.settings.worker_heartbeat_timeout_seconds)
        now = utcnow()
        changed = False
        for worker in session.scalars(select(Worker).where(
                Worker.status.not_in([WorkerStatus.OFFLINE, WorkerStatus.REVOKED]))):
            last = aware(worker.last_heartbeat)
            if last is not None and now - last <= timeout:
                continue
            changed = True
            worker.status = WorkerStatus.OFFLINE
            audit.record(session, EventType.WORKER_OFFLINE, actor_type="worker", actor_id=worker.id,
                         payload={"name": worker.name, "last_heartbeat": str(last)})
            for job in session.scalars(select(Job).where(Job.worker_id == worker.id,
                                                          Job.state.in_(list(ACTIVE_JOB_STATES)))):
                requeue_or_fail(session, job, f"worker {worker.name} went offline")
        changed |= expire_leases(session) > 0
        changed |= self._check_placements(session)
        return changed

    def _check_placements(self, session: Session) -> bool:
        """Take runs off machines that can no longer execute them (ADR-008). An offline machine keeps its
        runs for run_reassign_after_seconds (restarts, network blips) and only while they have work queued;
        a run waiting on a human approval stays where it is until it has something to run."""
        grace = timedelta(seconds=self.p.settings.run_reassign_after_seconds)
        now = utcnow()
        changed = False
        runs = session.scalars(select(WorkflowRun).where(WorkflowRun.status == RunStatus.RUNNING,
                                                         WorkflowRun.assigned_worker_id.is_not(None))).all()
        for run in runs:
            worker = session.get(Worker, run.assigned_worker_id)
            offline = placement.offline_for(worker, now) if worker is not None else None
            if worker is None:
                reason = "assigned worker was deleted"
            elif worker.status == WorkerStatus.REVOKED:
                reason = f"worker {worker.name} was revoked"
            elif not requirements_met(worker, run.requirements or {}):
                reason = f"worker {worker.name} no longer meets {placement.describe(run.requirements or {})}"
            elif worker.status == WorkerStatus.OFFLINE and (offline is None or offline >= grace) \
                    and self._has_pending_work(session, run):
                minutes = int((offline or grace).total_seconds() // 60)
                reason = f"worker {worker.name} offline for {minutes} min"
            else:
                continue
            if worker is not None and placement.active_jobs_on(session, run, worker.id):
                continue  # let the job finish or its lease expire first
            placement.release_run(session, run, reason)
            self._place(session, run, session.get(Project, run.project_id))
            changed = True
        return changed

    def _has_pending_work(self, session: Session, run: WorkflowRun) -> bool:
        if session.scalar(select(Job.id).where(Job.workflow_run_id == run.id, Job.state == JobState.QUEUED).limit(1)):
            return True
        return any(t.state == TaskState.READY and t.task_type != TaskType.GATE for t in self._current_tasks(session, run))

    # ================================================================== placement
    def _ensure_placement(self, session: Session, run: WorkflowRun, project: Project) -> bool:
        """Give a run-placed run its machine as soon as it has something to execute."""
        if not placement.is_run_placed(run) or run.assigned_worker_id:
            return False
        if not self._has_pending_work(session, run):
            return False
        return self._place(session, run, project)

    def _place(self, session: Session, run: WorkflowRun, project: Project, actor: str = "orchestrator") -> bool:
        choice = placement.choose_worker(session, run, project, self.p.settings.worker_heartbeat_timeout_seconds)
        if choice.worker is None:
            return placement.set_wait(run, choice.reason)
        placement.assign_run(session, run, choice, actor=actor)
        return True


__all__ = ["Orchestrator", "OrchestratorError"]
