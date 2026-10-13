"""Admin-facing API: projects, workflow runs, tasks, artifacts, approvals, audit, observability."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from awf.domain.enums import AgentRunStatus, ApprovalState, RunStatus, TaskState, WorkerStatus
from awf.domain.models import (
    AgentRun,
    Approval,
    Artifact,
    AuditEvent,
    Job,
    JobLog,
    Project,
    Task,
    Worker,
    WorkflowRun,
)
from awf.orchestrator import jobs as job_service
from awf.orchestrator.orchestrator import OrchestratorError
from awf_api import serializers as ser
from awf_api.deps import AppState, get_session, get_state, require_admin

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)])


def _get(session: Session, model, obj_id: str):
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {obj_id} not found")
    return obj


# ---------------------------------------------------------------------------- projects
class RepositoryIn(BaseModel):
    url: str
    base_branch: str = "main"
    build_command: list[str] | str | None = None
    test_command: list[str] | str | None = None
    lint_command: list[str] | str | None = None
    required_capabilities: list[str] = Field(default_factory=list)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    description: str = ""
    repository: RepositoryIn | None = None
    confidentiality: Literal["public", "internal", "restricted"] = "internal"
    budget_usd: float | None = Field(default=None, ge=0)
    labels: dict[str, Any] = Field(default_factory=dict)


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, state: AppState = Depends(get_state), session: Session = Depends(get_session, scope="function"),
                   actor: str = Depends(require_admin)):
    try:
        project = state.orchestrator.create_project(
            session, name=body.name, slug_value=body.slug, description=body.description,
            repository=body.repository.model_dump() if body.repository else {}, confidentiality=body.confidentiality,
            budget_usd=body.budget_usd, labels=body.labels, actor=actor)
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "a project with this slug already exists") from None
    return ser.project(project)


@router.get("/projects")
def list_projects(session: Session = Depends(get_session, scope="function")):
    return [ser.project(p) for p in session.scalars(select(Project).order_by(Project.created_at.desc()))]


@router.get("/projects/{project_id}")
def get_project(project_id: str, session: Session = Depends(get_session, scope="function")):
    project = _get(session, Project, project_id)
    spend = session.scalar(select(func.coalesce(func.sum(AgentRun.cost_estimate), 0))
                           .join(Task, Task.id == AgentRun.task_id)
                           .join(WorkflowRun, WorkflowRun.id == Task.workflow_run_id)
                           .where(WorkflowRun.project_id == project_id))
    return {**ser.project(project), "spend_usd": float(spend or 0)}


# ---------------------------------------------------------------------------- workflows
@router.get("/workflows")
def list_workflows(state: AppState = Depends(get_state)):
    return [{"id": w.spec.id, "version": w.spec.version, "name": w.spec.name, "status": w.spec.status,
             "description": w.spec.description, "inputs": w.spec.inputs,
             "stages": [s.model_dump(exclude_none=True) for s in w.spec.stages]}
            for w in state.platform.workflows.all()]


@router.get("/agents")
def list_agents(state: AppState = Depends(get_state)):
    return [{"role": d.role, "name": d.contract.role.name, "objective": d.contract.role.objective,
             "profile_version": d.profile.version, "contract_version": d.contract.version,
             "model_class": d.profile.model_policy.class_, "allowed_tools": d.contract.allowed_tools,
             "permissions": d.contract.permissions, "outputs": [o.name for o in d.contract.outputs],
             "completion_rules": d.contract.completion_rules, "human_approval": d.contract.human_approval}
            for d in state.platform.agents.all()]


class RunIn(BaseModel):
    workflow_id: str = "software-development"
    inputs: dict[str, Any]


@router.post("/projects/{project_id}/workflow-runs", status_code=201)
def start_run(project_id: str, body: RunIn, state: AppState = Depends(get_state),
              session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    project = _get(session, Project, project_id)
    try:
        run = state.orchestrator.start_run(session, project, body.workflow_id, body.inputs, actor=actor)
    except (OrchestratorError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from None
    return ser.run(run)


@router.get("/projects/{project_id}/workflow-runs")
def list_runs(project_id: str, session: Session = Depends(get_session, scope="function")):
    rows = session.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project_id)
                           .order_by(WorkflowRun.created_at.desc()))
    return [ser.run(r) for r in rows]


@router.get("/workflow-runs/{run_id}")
def get_run(run_id: str, state: AppState = Depends(get_state), session: Session = Depends(get_session, scope="function")):
    run = _get(session, WorkflowRun, run_id)
    cost = session.scalar(select(func.coalesce(func.sum(AgentRun.cost_estimate), 0))
                          .join(Task, Task.id == AgentRun.task_id).where(Task.workflow_run_id == run_id))
    data = ser.run(run, state.orchestrator.stage_overview(session, run))
    data["cost_usd"] = float(cost or 0)
    return data


@router.post("/workflow-runs/{run_id}/cancel")
def cancel_run(run_id: str, state: AppState = Depends(get_state), session: Session = Depends(get_session, scope="function"),
               actor: str = Depends(require_admin)):
    run = _get(session, WorkflowRun, run_id)
    try:
        state.orchestrator.cancel_run(session, run, actor=actor)
    except OrchestratorError as exc:
        raise HTTPException(409, str(exc)) from None
    return ser.run(run)


# ---------------------------------------------------------------------------- tasks
@router.get("/workflow-runs/{run_id}/tasks")
def list_tasks(run_id: str, session: Session = Depends(get_session, scope="function")):
    _get(session, WorkflowRun, run_id)
    rows = session.scalars(select(Task).where(Task.workflow_run_id == run_id).order_by(Task.created_at))
    return [ser.task(t) for t in rows]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, session: Session = Depends(get_session, scope="function")):
    task = _get(session, Task, task_id)
    runs = session.scalars(select(AgentRun).where(AgentRun.task_id == task_id).order_by(AgentRun.created_at))
    jobs = session.scalars(select(Job).where(Job.task_id == task_id).order_by(Job.created_at))
    approvals = session.scalars(select(Approval).where(Approval.task_id == task_id).order_by(Approval.created_at))
    artifacts = session.scalars(select(Artifact).where(Artifact.task_id == task_id).order_by(Artifact.created_at))
    return {**ser.task(task), "agent_runs": [ser.agent_run(a) for a in runs], "jobs": [ser.job(j) for j in jobs],
            "approvals": [ser.approval(a) for a in approvals], "artifacts": [ser.artifact(a) for a in artifacts]}


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, state: AppState = Depends(get_state), session: Session = Depends(get_session, scope="function"),
               actor: str = Depends(require_admin)):
    task = _get(session, Task, task_id)
    try:
        state.orchestrator.retry_task(session, task, actor=actor)
    except OrchestratorError as exc:
        raise HTTPException(409, str(exc)) from None
    return ser.task(task)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, session: Session = Depends(get_session, scope="function")):
    return ser.job(_get(session, Job, job_id), include_payload=True)


@router.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: str, after: int = 0, session: Session = Depends(get_session, scope="function")):
    rows = session.scalars(select(JobLog).where(JobLog.job_id == job_id, JobLog.id > after)
                           .order_by(JobLog.id).limit(2000))
    return [{"id": r.id, "stream": r.stream, "line": r.line, "created_at": r.created_at.isoformat()} for r in rows]


# ---------------------------------------------------------------------------- artifacts
@router.get("/projects/{project_id}/artifacts")
def list_artifacts(project_id: str, run_id: str | None = None, type: str | None = None,
                   session: Session = Depends(get_session, scope="function")):
    query = select(Artifact).where(Artifact.project_id == project_id)
    if run_id:
        query = query.where(Artifact.workflow_run_id == run_id)
    if type:
        query = query.where(Artifact.type == type)
    return [ser.artifact(a) for a in session.scalars(query.order_by(Artifact.created_at))]


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, session: Session = Depends(get_session, scope="function")):
    return ser.artifact(_get(session, Artifact, artifact_id))


@router.get("/artifacts/{artifact_id}/content")
def get_artifact_content(artifact_id: str, state: AppState = Depends(get_state),
                         session: Session = Depends(get_session, scope="function")):
    artifact = _get(session, Artifact, artifact_id)
    data = state.platform.artifacts.read_bytes(artifact)
    return Response(content=data, media_type=artifact.media_type + ("; charset=utf-8" if artifact.media_type.startswith(("text/", "application/json")) else ""),
                    headers={"X-Artifact-Checksum": artifact.checksum})


@router.get("/artifacts/{artifact_id}/relations")
def get_relations(artifact_id: str, state: AppState = Depends(get_state), session: Session = Depends(get_session, scope="function")):
    _get(session, Artifact, artifact_id)
    rel = state.platform.artifacts.relations(session, artifact_id)
    return {"outgoing": [ser.relation(r) for r in rel["outgoing"]],
            "incoming": [ser.relation(r) for r in rel["incoming"]]}


@router.get("/artifacts/{artifact_id}/impact")
def get_impact(artifact_id: str, state: AppState = Depends(get_state), session: Session = Depends(get_session, scope="function")):
    """Impact analysis: every artifact derived (transitively) from this one, plus the tasks that produced them."""
    _get(session, Artifact, artifact_id)
    impacted = []
    for aid, depth, rel in state.platform.artifacts.impact(session, artifact_id):
        art = session.get(Artifact, aid)
        impacted.append({**ser.artifact(art), "depth": depth, "via": rel})
    task_ids = sorted({a["task_id"] for a in impacted if a["task_id"]})
    return {"artifact_id": artifact_id, "impacted_artifacts": impacted, "impacted_task_ids": task_ids}


# ---------------------------------------------------------------------------- approvals
@router.get("/approvals")
def list_approvals(state_filter: str | None = Query(default=None, alias="state"),
                   session: Session = Depends(get_session, scope="function")):
    query = select(Approval).order_by(Approval.created_at.desc())
    if state_filter:
        query = query.where(Approval.state == state_filter.upper())
    return [ser.approval(a) for a in session.scalars(query)]


class ApprovalDecisionIn(BaseModel):
    comment: str | None = Field(default=None, max_length=4000)


def _resolve(approval_id: str, approved: bool, body: ApprovalDecisionIn, state: AppState, session: Session,
             actor: str) -> dict:
    approval = _get(session, Approval, approval_id)
    try:
        state.orchestrator.resolve_approval(session, approval, approved, actor, body.comment)
    except OrchestratorError as exc:
        raise HTTPException(409, str(exc)) from None
    return ser.approval(approval)


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, body: ApprovalDecisionIn | None = None, state: AppState = Depends(get_state),
            session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    return _resolve(approval_id, True, body or ApprovalDecisionIn(), state, session, actor)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, body: ApprovalDecisionIn | None = None, state: AppState = Depends(get_state),
           session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    return _resolve(approval_id, False, body or ApprovalDecisionIn(), state, session, actor)


# ---------------------------------------------------------------------------- observability
@router.get("/workers")
def list_workers(session: Session = Depends(get_session, scope="function")):
    return [ser.worker(w) for w in session.scalars(select(Worker).order_by(Worker.name))]


@router.post("/workers/{worker_id}/revoke")
def revoke_worker(worker_id: str, session: Session = Depends(get_session, scope="function"),
                  actor: str = Depends(require_admin)):
    """Lost/compromised machine: its token stops working at once and it cannot re-register."""
    worker = _get(session, Worker, worker_id)
    job_service.revoke_worker(session, worker, actor)
    return ser.worker(worker)


@router.delete("/workers/{worker_id}", status_code=204)
def delete_worker(worker_id: str, session: Session = Depends(get_session, scope="function")):
    """Remove a worker record (e.g. after revoking it, to allow the machine to join again)."""
    try:
        job_service.delete_worker(session, _get(session, Worker, worker_id))
    except job_service.WorkerRejected as exc:
        raise HTTPException(409, str(exc)) from None
    return Response(status_code=204)


@router.get("/audit")
def list_audit(project_id: str | None = None, run_id: str | None = None, task_id: str | None = None,
               event_type: str | None = None, limit: int = Query(default=200, le=2000),
               session: Session = Depends(get_session, scope="function")):
    query = select(AuditEvent)
    if project_id:
        query = query.where(AuditEvent.project_id == project_id)
    if run_id:
        query = query.where(AuditEvent.workflow_run_id == run_id)
    if task_id:
        query = query.where(AuditEvent.task_id == task_id)
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
    rows = session.scalars(query.order_by(AuditEvent.created_at.desc()).limit(limit))
    return [ser.audit_event(e) for e in rows]


@router.get("/overview")
def overview(session: Session = Depends(get_session, scope="function")):
    def count(model, *where) -> int:
        return session.scalar(select(func.count()).select_from(model).where(*where)) or 0

    active_tasks = [TaskState.READY, TaskState.WAITING, TaskState.RUNNING, TaskState.REVIEW]
    return {
        "projects": count(Project),
        "active_runs": count(WorkflowRun, WorkflowRun.status == RunStatus.RUNNING),
        "active_tasks": count(Task, Task.state.in_(active_tasks)),
        "failed_tasks": count(Task, Task.state == TaskState.FAILED),
        "waiting_approvals": count(Approval, Approval.state == ApprovalState.PENDING),
        "workers_online": count(Worker, Worker.status == WorkerStatus.ONLINE),
        "workers_total": count(Worker),
        "total_cost_usd": float(session.scalar(select(func.coalesce(func.sum(AgentRun.cost_estimate), 0))) or 0),
    }


@router.get("/stats/agents")
def agent_stats(session: Session = Depends(get_session, scope="function")):
    """Per-role KPIs (Plan §19, §26): run count, success/retry/critic-rejection rate, latency, cost."""
    rows = session.scalars(select(AgentRun)).all()
    by_role: dict[str, list[AgentRun]] = {}
    for r in rows:
        by_role.setdefault(r.role, []).append(r)
    stats = []
    for role, runs in sorted(by_role.items()):
        finished = [r for r in runs if r.status in (AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED)]
        succeeded = [r for r in finished if r.status == AgentRunStatus.SUCCEEDED]
        rejected = [r for r in succeeded if (r.critic_json or {}).get("decision") not in (None, "PASS")]
        n = len(finished) or 1
        stats.append({
            "role": role,
            "runs": len(runs),
            "success_rate": round(len(succeeded) / n, 3),
            "retry_rate": round(sum(1 for r in runs if r.retry_count) / (len(runs) or 1), 3),
            "critic_rejection_rate": round(len(rejected) / (len(succeeded) or 1), 3),
            "avg_latency_ms": int(sum(r.duration_ms for r in finished) / n),
            "avg_cost_usd": round(float(sum(r.cost_estimate for r in finished)) / n, 6),
            "tokens_in": sum(r.token_input for r in runs),
            "tokens_out": sum(r.token_output for r in runs),
        })
    return stats
