"""Run placement (ADR-008): one machine executes every job of a workflow run.

    start_run       -> run_requirements(): platform + capabilities merged over the project and every stage
    first dispatch  -> choose_worker(): online machines that meet them, least loaded first -> assign_run()
    machine lost    -> release_run(): the run (and its queued jobs) go back to placement, another machine
                       picks it up from the integration branch on origin and the artifacts on the control plane

The run's own state lives in git (pushed after every writing task) and in the database, never on the
machine, which is what makes moving a run safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from awf.domain.enums import ACTIVE_JOB_STATES, EventType, JobState, RunStatus, WorkerStatus
from awf.domain.models import Job, Project, Worker, WorkflowRun
from awf.orchestrator import audit
from awf.orchestrator.jobs import platform_matches, requirements_met
from awf.workflow_engine import WorkflowSpec

RUN = "run"
STAGE = "stage"


class PlacementError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


# ---------------------------------------------------------------------------- run state (context_json.placement)
def placement_of(run: WorkflowRun) -> dict[str, Any]:
    """{mode, worker (pinned name or None), wait (why no machine is assigned yet)}; {} for runs from before ADR-008."""
    return dict((run.context_json or {}).get("placement") or {})


def is_run_placed(run: WorkflowRun) -> bool:
    return placement_of(run).get("mode") == RUN


def _set_placement(run: WorkflowRun, **changes: Any) -> bool:
    current = placement_of(run)
    updated = {**current, **changes}
    if updated == current:
        return False
    run.context_json = {**(run.context_json or {}), "placement": updated}
    return True


# ---------------------------------------------------------------------------- requirements
def merge_platform(a: str | None, b: str | None) -> str | None:
    """'linux' + 'linux-arm64' -> 'linux-arm64'; 'linux' + 'windows' -> conflict."""
    if not a or a == "any":
        return b
    if not b or b == "any":
        return a
    if platform_matches(a, b):
        return a
    if platform_matches(b, a):
        return b
    raise PlacementError(f"platform '{a}' conflicts with '{b}'")


def run_requirements(spec: WorkflowSpec, project: Project, repository: dict[str, Any] | None,
                     default_platform: str, extra_capabilities: set[str] | None = None) -> dict[str, Any]:
    """What the one machine of a run must offer: the union over the project and every stage."""
    if not (project.labels or {}).get("platform"):
        raise PlacementError(f"project '{project.slug}' does not say which platform it runs on; set labels.platform "
                             f"(windows, linux, linux-arm64, ... or 'any') with PATCH /api/v1/projects/{project.id} "
                             f"or on the project page")
    platform: str | None = None
    sources = [("project", (project.labels or {}).get("platform"))]
    sources += [(f"stage '{s.id}'", s.worker.platform) for s in spec.stages if s.worker and s.worker.platform]
    seen: list[str] = []
    for source, value in sources:
        if not value:
            continue
        try:
            platform = merge_platform(platform, value)
        except PlacementError:
            raise PlacementError(
                f"{source} needs platform '{value}' but {', '.join(seen)} already need '{platform}'; "
                f"one machine cannot run this workflow (use placement: stage in the workflow)") from None
        seen.append(source)
    needs_git = "repository" in spec.inputs or bool((repository or {}).get("url") or (project.repository or {}).get("url"))
    caps = {"git"} if needs_git else set()
    caps.update((repository or {}).get("required_capabilities") or [])
    caps.update((project.repository or {}).get("required_capabilities") or [])
    for stage in spec.stages:
        if stage.worker:
            caps.update(stage.worker.capabilities)
    caps.update(extra_capabilities or ())
    return {"platform": platform or default_platform, "capabilities": sorted(caps)}


def project_requirements(project: Project) -> dict[str, Any]:
    """What any machine running this project needs, before a workflow adds its stage requirements."""
    caps = ({"git"} if (project.repository or {}).get("url") else set()) \
        | set((project.repository or {}).get("required_capabilities") or [])
    return {"platform": (project.labels or {}).get("platform"), "capabilities": sorted(caps)}


def describe(requirements: dict[str, Any]) -> str:
    caps = ", ".join(requirements.get("capabilities") or []) or "none"
    return f"platform {requirements.get('platform') or 'any'}, capabilities [{caps}]"


# ---------------------------------------------------------------------------- choosing a machine
def is_fresh(worker: Worker, heartbeat_timeout_seconds: int, now: datetime | None = None) -> bool:
    last = _aware(worker.last_heartbeat)
    return last is not None and ((now or utcnow()) - last).total_seconds() <= heartbeat_timeout_seconds


def worker_load(session: Session, worker: Worker) -> tuple[int, int]:
    """(queued + running jobs on the machine, runs it holds)."""
    jobs = session.scalar(select(func.count(Job.id)).where(
        Job.state.in_([JobState.QUEUED, *ACTIVE_JOB_STATES]),
        or_(Job.assigned_worker_id == worker.id, Job.worker_id == worker.id))) or 0
    runs = session.scalar(select(func.count(WorkflowRun.id)).where(
        WorkflowRun.assigned_worker_id == worker.id, WorkflowRun.status == RunStatus.RUNNING)) or 0
    return int(jobs), int(runs)


def _preferred(worker: Worker, project: Project, run: WorkflowRun) -> bool:
    raw = (worker.labels or {}).get("preferred_for") or ""
    names = {n.strip() for n in (raw if isinstance(raw, list) else str(raw).split(",")) if str(n).strip()}
    return bool(names & {project.slug, run.workflow_id})


@dataclass
class Choice:
    worker: Worker | None
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)


def choose_worker(session: Session, run: WorkflowRun, project: Project, heartbeat_timeout_seconds: int) -> Choice:
    requirements = run.requirements or {}
    pinned = placement_of(run).get("worker")
    query = select(Worker).order_by(Worker.name)
    if pinned:
        query = query.where(Worker.name == pinned)
    workers = session.scalars(query).all()
    if pinned and not workers:
        return Choice(None, f"chosen worker '{pinned}' is not registered")
    now = utcnow()
    scored: list[tuple[tuple, Worker, dict[str, Any]]] = []
    for w in workers:
        if not requirements_met(w, requirements):
            continue
        if w.status != WorkerStatus.ONLINE or not is_fresh(w, heartbeat_timeout_seconds, now):
            continue
        jobs, runs = worker_load(session, w)
        slots = max(1, w.max_jobs or 1)
        preferred = _preferred(w, project, run)
        info = {"name": w.name, "jobs": jobs, "slots": slots, "runs": runs, "preferred": preferred}
        scored.append(((jobs / slots, runs, not preferred, w.name), w, info))
    if not scored:
        if pinned:
            w = workers[0]
            why = (f"does not meet {describe(requirements)}" if not requirements_met(w, requirements)
                   else f"is {w.status if w.status != WorkerStatus.ONLINE else 'not sending heartbeats'}")
            return Choice(None, f"waiting for chosen worker '{pinned}', which {why}")
        return Choice(None, f"waiting for an online worker with {describe(requirements)}")
    scored.sort(key=lambda item: item[0])
    _, best, _ = scored[0]
    reason = "chosen by user" if pinned else "least loaded matching worker"
    return Choice(best, reason, [info for _, _, info in scored])


# ---------------------------------------------------------------------------- assignment
def assign_run(session: Session, run: WorkflowRun, choice: Choice, actor: str = "orchestrator") -> None:
    worker = choice.worker
    assert worker is not None
    run.assigned_worker_id = worker.id
    run.assigned_at = utcnow()
    _set_placement(run, wait=None)
    session.execute(update(Job).where(Job.workflow_run_id == run.id, Job.pinned.is_(True),
                                      Job.state == JobState.QUEUED)
                    .values(assigned_worker_id=worker.id))
    audit.record(session, EventType.RUN_ASSIGNED, actor_type="human" if actor != "orchestrator" else "system",
                 actor_id=actor, project_id=run.project_id, workflow_run_id=run.id,
                 payload={"worker_id": worker.id, "worker": worker.name, "reason": choice.reason,
                          "candidates": choice.candidates[:10]})


def release_run(session: Session, run: WorkflowRun, reason: str, actor: str = "orchestrator") -> None:
    """Take the run off its machine; its queued jobs cannot be claimed until placement picks another one."""
    previous = run.assigned_worker_id
    run.assigned_worker_id = None
    run.assigned_at = None
    _set_placement(run, wait=reason)
    session.execute(update(Job).where(Job.workflow_run_id == run.id, Job.pinned.is_(True),
                                      Job.state == JobState.QUEUED)
                    .values(assigned_worker_id=None))
    audit.record(session, EventType.RUN_UNASSIGNED, actor_type="human" if actor != "orchestrator" else "system",
                 actor_id=actor, project_id=run.project_id, workflow_run_id=run.id,
                 payload={"worker_id": previous, "reason": reason})


def set_wait(run: WorkflowRun, reason: str | None) -> bool:
    return _set_placement(run, wait=reason)


def set_pinned_worker(run: WorkflowRun, name: str | None) -> None:
    _set_placement(run, worker=name)


def active_jobs_on(session: Session, run: WorkflowRun, worker_id: str) -> int:
    return session.scalar(select(func.count(Job.id)).where(
        Job.workflow_run_id == run.id, Job.worker_id == worker_id,
        Job.state.in_(list(ACTIVE_JOB_STATES)))) or 0


def offline_for(worker: Worker, now: datetime | None = None) -> timedelta | None:
    last = _aware(worker.last_heartbeat)
    return None if last is None else (now or utcnow()) - last


__all__ = ["PlacementError", "Choice", "RUN", "STAGE", "run_requirements", "merge_platform", "describe",
           "choose_worker", "assign_run", "release_run", "placement_of", "is_run_placed", "set_wait",
           "set_pinned_worker", "worker_load", "is_fresh", "active_jobs_on", "offline_for"]
