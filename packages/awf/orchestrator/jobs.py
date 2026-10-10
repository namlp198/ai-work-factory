"""Worker-facing job service: registration, heartbeat, claim, progress, logs, completion (Design §7, §11)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from awf.domain.enums import ACTIVE_JOB_STATES, AgentRunStatus, EventType, JobState, TaskState, WorkerStatus
from awf.domain.models import AgentRun, Job, JobLog, Task, Worker
from awf.domain.state_machine import InvalidTransition, check_job_transition, check_task_transition
from awf.orchestrator import audit
from awf.security import hash_token, new_token, redact


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobConflict(Exception):
    pass


def platform_matches(worker_platform: str, required: str | None) -> bool:
    if not required or required == "any":
        return True
    return worker_platform == required or worker_platform.startswith(required + "-")


def requirements_met(worker: Worker, requirements: dict[str, Any]) -> bool:
    if not platform_matches(worker.platform, requirements.get("platform")):
        return False
    return set(requirements.get("capabilities") or []) <= set(worker.capabilities or [])


def register_worker(session: Session, data: dict[str, Any]) -> tuple[Worker, str]:
    token = new_token()
    worker = session.scalar(select(Worker).where(Worker.name == data["name"]))
    if worker is None:
        worker = Worker(name=data["name"])
        session.add(worker)
    worker.platform = data["platform"]
    worker.os_version = data.get("os_version", "")
    worker.capabilities = sorted(set(data.get("capabilities") or []))
    worker.labels = data.get("labels") or {}
    worker.status = WorkerStatus.ONLINE
    worker.token_hash = hash_token(token)
    worker.last_heartbeat = utcnow()
    session.flush()
    audit.record(session, EventType.WORKER_REGISTERED, actor_type="worker", actor_id=worker.id,
                 payload={"name": worker.name, "platform": worker.platform, "capabilities": worker.capabilities})
    return worker, token


def heartbeat(session: Session, worker: Worker, status: dict[str, Any]) -> None:
    worker.last_heartbeat = utcnow()
    worker.load_json = {k: v for k, v in status.items() if k != "status"}
    if worker.status == WorkerStatus.OFFLINE or status.get("status") in (WorkerStatus.ONLINE, WorkerStatus.DRAINING):
        worker.status = status.get("status") or WorkerStatus.ONLINE


def claim_job(session: Session, worker: Worker) -> Job | None:
    if worker.status != WorkerStatus.ONLINE:
        return None
    candidates = session.scalars(select(Job).where(Job.state == JobState.QUEUED).order_by(Job.created_at)).all()
    for job in candidates:
        if not requirements_met(worker, job.requirements or {}):
            continue
        # Atomic claim: only one worker can move QUEUED -> CLAIMED.
        claimed = session.execute(
            update(Job).where(Job.id == job.id, Job.state == JobState.QUEUED)
            .values(state=JobState.CLAIMED, worker_id=worker.id, started_at=utcnow())
        ).rowcount
        if not claimed:
            continue
        session.refresh(job)
        task = session.get(Task, job.task_id)
        if task and task.state == TaskState.WAITING:
            check_task_transition(task.state, TaskState.RUNNING)
            task.state = TaskState.RUNNING
        if job.agent_run_id:
            run = session.get(AgentRun, job.agent_run_id)
            if run:
                run.status = AgentRunStatus.RUNNING
                run.worker_id = worker.id
                run.started_at = utcnow()
        audit.record(session, EventType.JOB_CLAIMED, actor_type="worker", actor_id=worker.id,
                     workflow_run_id=job.workflow_run_id, task_id=job.task_id,
                     payload={"job_id": job.id, "attempt": job.attempt})
        if job.agent_run_id:
            audit.record(session, EventType.AGENT_STARTED, actor_type="worker", actor_id=worker.id,
                         workflow_run_id=job.workflow_run_id, task_id=job.task_id,
                         payload={"agent_run_id": job.agent_run_id, "role": task.assigned_role if task else None})
        return job
    return None


def _owned_active_job(session: Session, worker: Worker, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.worker_id != worker.id:
        raise JobConflict("job not found or not assigned to this worker")
    if job.state not in ACTIVE_JOB_STATES:
        raise JobConflict(f"job is {job.state}; result already recorded or job cancelled")
    return job


def progress(session: Session, worker: Worker, job_id: str, state: str) -> Job:
    job = _owned_active_job(session, worker, job_id)
    if state not in (JobState.PREPARING, JobState.RUNNING, JobState.UPLOADING_RESULT):
        raise JobConflict(f"invalid progress state {state}")
    if state != job.state:
        try:
            job.state = check_job_transition(job.state, state)
        except InvalidTransition as exc:
            raise JobConflict(str(exc)) from exc
    return job


def append_logs(session: Session, worker: Worker, job_id: str, lines: list[dict[str, str]]) -> int:
    job = session.get(Job, job_id)
    if job is None or job.worker_id != worker.id:
        raise JobConflict("job not found or not assigned to this worker")
    for entry in lines[:500]:
        session.add(JobLog(job_id=job_id, stream=entry.get("stream", "stdout")[:16],
                           line=redact(str(entry.get("line", "")))[:4000]))
    return len(lines[:500])


def complete_job(session: Session, worker: Worker, job_id: str, result: dict[str, Any]) -> Job:
    job = _owned_active_job(session, worker, job_id)
    job.state = JobState.SUCCESS
    job.result = result
    job.finished_at = utcnow()
    job.processed = False
    return job


def fail_job(session: Session, worker: Worker, job_id: str, error: str, retryable: bool,
             result: dict[str, Any] | None = None) -> Job:
    job = _owned_active_job(session, worker, job_id)
    job.state = JobState.FAILED
    job.error = redact(error)[:8000]
    job.retryable = retryable
    job.result = result or {}
    job.finished_at = utcnow()
    job.processed = False
    return job
