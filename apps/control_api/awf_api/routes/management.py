"""Admin-facing API: projects, workflow runs, tasks, artifacts, approvals, audit, observability."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from awf import githost
from awf.domain.enums import AgentRunStatus, ApprovalState, RunStatus, TaskState, WorkerStatus
from awf.domain.models import (
    AgentRun,
    Approval,
    Artifact,
    AuditEvent,
    Job,
    JobLog,
    Notification,
    Project,
    Task,
    Worker,
    WorkflowRun,
)
from awf.orchestrator import jobs as job_service
from awf.orchestrator import placement
from awf.orchestrator.orchestrator import OrchestratorError
from awf.tool_sdk.command import GitError
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


PLATFORM_PATTERN = re.compile(r"^(any|[a-z][a-z0-9]*(-[a-z0-9_]+)?)$")


def _check_labels(labels: dict[str, Any]) -> dict[str, Any]:
    """Every project says where it runs, so no run lands on a machine by accident (an Ubuntu box must never
    get a Windows-only .NET project). 'any' is an explicit choice, never a default."""
    value = labels.get("platform")
    if not value:
        raise ValueError("labels.platform is required: windows, linux, linux-arm64, ... or 'any'")
    if not isinstance(value, str) or not PLATFORM_PATTERN.match(value):
        raise ValueError(f"labels.platform '{value}' is not a platform like windows, linux, linux-arm64 or any")
    return labels


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    description: str = ""
    repository: RepositoryIn | None = None
    confidentiality: Literal["public", "internal", "restricted"] = "internal"
    budget_usd: float | None = Field(default=None, ge=0)
    labels: dict[str, Any]

    @field_validator("labels")
    @classmethod
    def _labels(cls, v):
        return _check_labels(v)


class ProjectPatch(BaseModel):
    """Only the fields sent change; `repository` and `labels` are replaced as a whole."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    repository: RepositoryIn | None = None
    confidentiality: Literal["public", "internal", "restricted"] | None = None
    budget_usd: float | None = Field(default=None, ge=0)
    labels: dict[str, Any] | None = None

    @field_validator("labels")
    @classmethod
    def _labels(cls, v):
        return None if v is None else _check_labels(v)


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
        other = session.scalar(select(Project).where(Project.slug == body.slug))
        if other is not None and other.status == "ARCHIVED":
            raise HTTPException(409, f"the archived project '{other.name}' uses the slug '{body.slug}': restore it, "
                                     "or choose another slug") from None
        raise HTTPException(409, "a project with this slug already exists") from None
    return ser.project(project)


@router.get("/projects")
def list_projects(archived: bool = False, session: Session = Depends(get_session, scope="function")):
    """Active projects; `archived=true` lists the archived ones instead."""
    status = Project.status == "ARCHIVED" if archived else Project.status != "ARCHIVED"
    return [ser.project(p) for p in session.scalars(select(Project).where(status).order_by(Project.created_at.desc()))]


class HostedRepoIn(BaseModel):
    # A URL or a path on the control plane to copy from (GitHub, a folder...); empty: a new empty repository.
    import_from: str | None = Field(default=None, max_length=2000)


def _git_info(state: AppState, project: Project) -> dict[str, Any]:
    from awf_api.routes.git import git_token

    root = state.platform.settings.git_root
    repo = project.repository or {}
    path = githost.repo_path(root, project.slug)
    base = repo.get("base_branch") or "main"
    return {"hosted": True, "path": f"/git/{project.slug}.git", "base_branch": base,
            "base_sha": githost.branch_sha(path, base), "branches": githost.branches(path),
            "token": git_token(root)}


@router.post("/projects/{project_id}/git")
def host_repository(project_id: str, body: HostedRepoIn, state: AppState = Depends(get_state),
                    session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    """Keep the project's repository on this control plane (ADR-016): new and empty (push it from your PC), or a
    copy of `import_from`. Workers then clone it from here, and your PC uses it as the `factory` remote."""
    project = _get(session, Project, project_id)
    repo = dict(project.repository or {})
    if githost.is_hosted(repo.get("url")):
        raise HTTPException(409, "this project's repository is already hosted on the control plane")
    base = repo.get("base_branch") or "main"
    try:
        githost.create_repo(state.platform.settings.git_root, project.slug, base, (body.import_from or "").strip() or None)
    except (githost.GitHostError, GitError) as exc:
        raise HTTPException(422, str(exc)) from None
    project.repository = {**repo, "url": f"{githost.HOSTED_SCHEME}{project.slug}", "base_branch": base}
    session.flush()
    return _git_info(state, project)


@router.get("/projects/{project_id}/git")
def repository_info(project_id: str, state: AppState = Depends(get_state),
                    session: Session = Depends(get_session, scope="function")):
    project = _get(session, Project, project_id)
    if not githost.is_hosted((project.repository or {}).get("url")):
        return {"hosted": False}
    return _git_info(state, project)


@router.get("/projects/{project_id}")
def get_project(project_id: str, session: Session = Depends(get_session, scope="function")):
    project = _get(session, Project, project_id)
    spend = session.scalar(select(func.coalesce(func.sum(AgentRun.cost_estimate), 0))
                           .join(Task, Task.id == AgentRun.task_id)
                           .join(WorkflowRun, WorkflowRun.id == Task.workflow_run_id)
                           .where(WorkflowRun.project_id == project_id))
    runs = session.scalar(select(func.count(WorkflowRun.id)).where(WorkflowRun.project_id == project_id)) or 0
    return {**ser.project(project), "spend_usd": float(spend or 0), "run_count": runs, **_machines(session, project)}


def _machines(session: Session, project: Project) -> dict[str, Any]:
    """Which registered machines can run this project, and what the others lack (workflow stages may add more)."""
    requirements = placement.project_requirements(project)
    rows = []
    for w in session.scalars(select(Worker).where(Worker.status != WorkerStatus.REVOKED).order_by(Worker.name)):
        reasons = job_service.mismatch_reasons(w, requirements) if requirements["platform"] else ["project has no platform"]
        rows.append({"id": w.id, "name": w.name, "status": w.status, "platform": w.platform,
                     "matches": not reasons, "reasons": reasons})
    return {"requirements": requirements, "machines": rows}


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectPatch, state: AppState = Depends(get_state),
                   session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    project = _get(session, Project, project_id)
    changes = body.model_dump(exclude_unset=True)
    for key in ("name", "confidentiality", "repository", "labels"):
        if key in changes and changes[key] is None:
            raise HTTPException(422, f"{key} cannot be null")
    if "repository" in changes:
        changes["repository"] = body.repository.model_dump()
    state.orchestrator.update_project(session, project, changes, actor=actor)
    return {**ser.project(project), **_machines(session, project)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, state: AppState = Depends(get_state),
                   session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    """Delete a project that never ran (409 otherwise: archive it)."""
    project = _get(session, Project, project_id)
    try:
        state.orchestrator.delete_project(session, project, actor=actor)
    except OrchestratorError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"deleted": project_id}


@router.post("/projects/{project_id}/archive")
def archive_project(project_id: str, state: AppState = Depends(get_state),
                    session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    project = _get(session, Project, project_id)
    try:
        state.orchestrator.archive_project(session, project, actor=actor)
    except OrchestratorError as exc:
        raise HTTPException(409, str(exc)) from None
    return ser.project(project)


@router.post("/projects/{project_id}/restore")
def restore_project(project_id: str, state: AppState = Depends(get_state),
                    session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    project = _get(session, Project, project_id)
    state.orchestrator.restore_project(session, project, actor=actor)
    return ser.project(project)


# ---------------------------------------------------------------------------- models
@router.get("/models")
def list_models(state: AppState = Depends(get_state)):
    """Providers a run can use, and the model each class maps to on each of them."""
    router_ = state.platform.router
    tables = {router_.default_provider: {c: e.get("model") for c, e in router_.classes.items()}}
    for provider, table in router_.alternatives.items():
        tables[provider] = {c: e.get("model") for c, e in table.items()}
    return {"default_provider": router_.default_provider, "providers": router_.providers(), "classes": tables}


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


class PlacementIn(BaseModel):
    # Name of the machine that must execute the whole run; omitted: the orchestrator chooses.
    worker: str | None = Field(default=None, min_length=1, max_length=200)


class RunIn(BaseModel):
    workflow_id: str = "software-development"
    inputs: dict[str, Any]
    placement: PlacementIn | None = None
    # Model provider for every agent of this run: anthropic (Claude) or google (Gemini); default: project/config.
    llm: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{1,30}$")
    # Coding engine for the roles that support one (ADR-011): native or claude-code; default: project/native.
    coding_engine: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{1,30}$")
    # Which roles use that engine: coding (the Developer) or all (every role, e.g. on a signed-in Pro plan).
    engine_scope: Literal["coding", "all"] | None = None
    # How hard the work is: raises effort (and the model, for very_hard) for every role; default: project/normal.
    complexity: Literal["normal", "hard", "very_hard"] | None = None


@router.post("/projects/{project_id}/workflow-runs", status_code=201)
def start_run(project_id: str, body: RunIn, state: AppState = Depends(get_state),
              session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    project = _get(session, Project, project_id)
    try:
        run = state.orchestrator.start_run(session, project, body.workflow_id, body.inputs, actor=actor,
                                           worker_name=body.placement.worker if body.placement else None,
                                           llm=body.llm, coding_engine=body.coding_engine,
                                           engine_scope=body.engine_scope, complexity=body.complexity)
    except (OrchestratorError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from None
    return ser.run(run, worker=None)


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
    worker = session.get(Worker, run.assigned_worker_id) if run.assigned_worker_id else None
    data = ser.run(run, state.orchestrator.stage_overview(session, run), worker=worker)
    data["cost_usd"] = float(cost or 0)
    data["activity"] = run_activity(session, run_id)
    return data


_ACTIVE_TASK_STATES = (TaskState.READY, TaskState.RUNNING, TaskState.WAITING)


def run_activity(session: Session, run_id: str) -> list[dict[str, Any]]:
    """What each unfinished task is doing now: its latest job, the last platform note from the worker (model
    retries, engine progress) and the error of the previous attempt, so a run stuck at 0% says why."""
    out = []
    tasks = session.scalars(select(Task).where(Task.workflow_run_id == run_id, Task.state.in_(_ACTIVE_TASK_STATES))
                            .order_by(Task.created_at))
    for task in tasks:
        job = session.scalars(select(Job).where(Job.task_id == task.id).order_by(Job.created_at.desc())).first()
        note = None
        if job:
            note = session.scalars(select(JobLog).where(JobLog.job_id == job.id,
                                                        JobLog.stream.in_(("system", "stderr")))
                                   .order_by(JobLog.id.desc())).first()
        out.append({
            "task_id": task.id, "title": task.title, "state": task.state,
            "attempt": task.attempt, "max_attempts": task.max_attempts,
            "job_state": job.state if job else None,
            "started_at": job.started_at.isoformat() if job and job.started_at else None,
            "previous_error": job.error if job and job.error else None,
            "note": note.line[:500] if note else None,
            "note_at": note.created_at.isoformat() if note else None,
        })
    return out


@router.post("/workflow-runs/{run_id}/reassign")
def reassign_run(run_id: str, body: PlacementIn, state: AppState = Depends(get_state),
                 session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    """Move a run to another machine (`worker`), or let the orchestrator choose again (no `worker`)."""
    run = _get(session, WorkflowRun, run_id)
    try:
        state.orchestrator.reassign_run(session, run, body.worker, actor=actor)
    except OrchestratorError as exc:
        raise HTTPException(409, str(exc)) from None
    worker = session.get(Worker, run.assigned_worker_id) if run.assigned_worker_id else None
    return ser.run(run, worker=worker)


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
    rows = list(session.scalars(select(Task).where(Task.workflow_run_id == run_id).order_by(Task.created_at)))
    latest: dict[str, AgentRun] = {}
    for ar in session.scalars(select(AgentRun).where(AgentRun.task_id.in_([t.id for t in rows]))
                              .order_by(AgentRun.created_at)):
        latest[ar.task_id] = ar  # the last attempt's route: what the task runs on now
    out = []
    for t in rows:
        data = ser.task(t)
        ar = latest.get(t.id)
        if ar:
            data["model"] = {"provider": ar.model_provider, "name": ar.model_name, "class": ar.model_class,
                             "effort": (ar.input_json or {}).get("effort"),
                             "engine": (ar.input_json or {}).get("engine"), "reason": ar.route_reason}
        out.append(data)
    return out


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


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, session: Session = Depends(get_session, scope="function")):
    """Everything needed to decide from a phone: what, which project/run, and the artifacts to look at."""
    approval = _get(session, Approval, approval_id)
    run = session.get(WorkflowRun, approval.workflow_run_id) if approval.workflow_run_id else None
    project = session.get(Project, approval.project_id)
    ids = (approval.scope or {}).get("artifact_ids") or []
    artifacts = [ser.artifact(a) for a in session.scalars(select(Artifact).where(Artifact.id.in_(ids)))] if ids else []
    return {**ser.approval(approval), "project": ser.project(project) if project else None,
            "run": ser.run(run) if run else None, "artifacts": artifacts}


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


# ---------------------------------------------------------------------------- notifications
@router.get("/notifications")
def list_notifications(limit: int = Query(default=50, ge=1, le=500),
                       session: Session = Depends(get_session, scope="function")):
    from awf.notify.config import NotifyConfig

    config = NotifyConfig.from_env()
    rows = session.scalars(select(Notification).order_by(Notification.created_at.desc()).limit(limit))
    return {"channels": config.channels, "events": list(config.kinds), "public_url": config.public_url or None,
            "telegram_approvals": bool(config.telegram and config.telegram.approvers),
            "items": [ser.notification(n) for n in rows]}


@router.post("/notifications/test", status_code=202)
def test_notification(session: Session = Depends(get_session, scope="function"), actor: str = Depends(require_admin)):
    """Queue a test message to every configured channel; watch its state in GET /notifications."""
    from awf.notify.config import NotifyConfig

    config = NotifyConfig.from_env()
    if not config.enabled:
        raise HTTPException(409, "no notification channel configured on the control plane "
                                 "(AWF_TELEGRAM_BOT_TOKEN, AWF_WHATSAPP_TOKEN or AWF_ZALO_APP_ID in .env)")
    note = Notification(kind="test", event_type="TEST", title="🧪 Tin thử từ AI Work Factory",
                        body=f"Gửi bởi {actor}. Nếu bạn đọc được tin này, kênh đã hoạt động.",
                        link=f"{config.public_url}/#/notifications" if config.public_url else None, deliveries={})
    session.add(note)
    session.flush()
    return ser.notification(note)


# ---------------------------------------------------------------------------- observability
@router.get("/workers")
def list_workers(session: Session = Depends(get_session, scope="function")):
    runs = dict(session.execute(select(WorkflowRun.assigned_worker_id, func.count(WorkflowRun.id))
                                .where(WorkflowRun.status == RunStatus.RUNNING,
                                       WorkflowRun.assigned_worker_id.is_not(None))
                                .group_by(WorkflowRun.assigned_worker_id)).all())
    return [{**ser.worker(w), "active_runs": runs.get(w.id, 0)}
            for w in session.scalars(select(Worker).order_by(Worker.name))]


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
def overview(state: AppState = Depends(get_state), session: Session = Depends(get_session, scope="function")):
    def count(model, *where) -> int:
        return session.scalar(select(func.count()).select_from(model).where(*where)) or 0

    active_tasks = [TaskState.READY, TaskState.WAITING, TaskState.RUNNING, TaskState.REVIEW]
    return {
        "projects": count(Project, Project.status != "ARCHIVED"),
        "active_runs": count(WorkflowRun, WorkflowRun.status == RunStatus.RUNNING),
        "active_tasks": count(Task, Task.state.in_(active_tasks)),
        "failed_tasks": count(Task, Task.state == TaskState.FAILED),
        "waiting_approvals": count(Approval, Approval.state == ApprovalState.PENDING),
        "workers_online": count(Worker, Worker.status == WorkerStatus.ONLINE),
        "workers_total": count(Worker),
        "total_cost_usd": float(session.scalar(select(func.coalesce(func.sum(AgentRun.cost_estimate), 0))) or 0),
        "runs": _active_runs(state, session),
        "recent_runs": _recent_runs(session),
    }


def _recent_runs(session: Session, limit: int = 6) -> list[dict[str, Any]]:
    """The last finished runs: what just completed, failed or was cancelled."""
    rows = session.execute(select(WorkflowRun, Project.name).join(Project, Project.id == WorkflowRun.project_id)
                           .where(WorkflowRun.status != RunStatus.RUNNING)
                           .order_by(WorkflowRun.created_at.desc()).limit(limit)).all()
    return [{"id": r.id, "project": name, "workflow_id": r.workflow_id, "status": r.status,
             "objective": (r.input_json or {}).get("objective", "")[:200], "error": (r.error or "")[:300],
             "finished_at": r.finished_at.isoformat() if r.finished_at else None,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r, name in rows]


def _active_runs(state: AppState, session: Session, limit: int = 20) -> list[dict[str, Any]]:
    """Running runs at a glance (the phone view): progress through the stages, current stage, machine."""
    rows = []
    for run in session.scalars(select(WorkflowRun).where(WorkflowRun.status == RunStatus.RUNNING)
                               .order_by(WorkflowRun.created_at.desc()).limit(limit)):
        try:
            stages = state.orchestrator.stage_overview(session, run)
        except KeyError:  # workflow definition removed since the run started
            stages = []
        current = next((s for s in stages if s["status"] in ("active", "failed")), None)
        worker = session.get(Worker, run.assigned_worker_id) if run.assigned_worker_id else None
        project = session.get(Project, run.project_id)
        placement = (run.context_json or {}).get("placement") or {}
        rows.append({"id": run.id, "workflow_id": run.workflow_id, "project": project.name if project else None,
                     "objective": (run.input_json or {}).get("objective", "")[:200],
                     "stages_done": sum(1 for s in stages if s["status"] == "done"), "stages_total": len(stages),
                     "current_stage": current["title"] if current else None,
                     "current_gate": bool(current and current.get("gate")),
                     "worker": worker.name if worker else None, "waiting_for": placement.get("wait"),
                     "created_at": run.created_at.isoformat() if run.created_at else None})
    return rows


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
