"""Worker protocol endpoints (Design §7 Workers / Worker Jobs, §11)."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from awf.orchestrator import jobs as job_service
from awf_api import serializers as ser
from awf_api.deps import AppState, get_session, get_state, require_job_worker, require_worker
from awf.domain.models import Worker

router = APIRouter(prefix="/api/v1")


class RegistrationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=64)
    os_version: str = ""
    capabilities: list[str] = Field(default_factory=list)
    labels: dict[str, Any] = Field(default_factory=dict)
    machine_id: str | None = Field(default=None, max_length=64)


@router.post("/workers/register", status_code=201)
def register(body: RegistrationIn, state: AppState = Depends(get_state), session: Session = Depends(get_session, scope="function"),
             x_registration_token: str | None = Header(default=None)):
    expected = state.platform.settings.worker_registration_token
    if expected:
        if not hmac.compare_digest(x_registration_token or "", expected):
            raise HTTPException(401, "invalid registration token")
    elif state.platform.settings.environment != "development":
        raise HTTPException(503, "AWF_WORKER_REGISTRATION_TOKEN is not configured")
    try:
        worker, token = job_service.register_worker(
            session, body.model_dump(), state.platform.settings.worker_heartbeat_timeout_seconds)
    except job_service.WorkerRejected as exc:
        raise HTTPException(409, str(exc)) from None
    return {**ser.worker(worker), "worker_token": token}


class HeartbeatIn(BaseModel):
    status: str = "online"
    cpu_load: float | None = None
    cpu_count: int | None = None
    memory_free_mb: int | None = None
    running_jobs: int = 0


@router.post("/workers/{worker_id}/heartbeat")
def heartbeat(body: HeartbeatIn, worker: Worker = Depends(require_worker)):
    job_service.heartbeat(object_session(worker), worker, body.model_dump(exclude_none=True))
    return {"status": worker.status}


@router.post("/workers/{worker_id}/claim")
def claim(worker: Worker = Depends(require_worker)):
    session = object_session(worker)
    job_service.heartbeat(session, worker, {"status": worker.status})
    job = job_service.claim_job(session, worker)
    if job is None:
        return {"job": None}
    return {"job": {**ser.job(job), "payload": job.payload}}


class ProgressIn(BaseModel):
    state: str


@router.post("/jobs/{job_id}/progress")
def progress(job_id: str, body: ProgressIn, worker: Worker = Depends(require_job_worker)):
    try:
        job = job_service.progress(object_session(worker), worker, job_id, body.state)
    except job_service.JobConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {"state": job.state}


class LogIn(BaseModel):
    lines: list[dict[str, str]] = Field(max_length=500)


@router.post("/jobs/{job_id}/log")
def log(job_id: str, body: LogIn, worker: Worker = Depends(require_job_worker)):
    try:
        count = job_service.append_logs(object_session(worker), worker, job_id, body.lines)
    except job_service.JobConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {"accepted": count}


@router.post("/jobs/{job_id}/complete")
def complete(job_id: str, result: dict[str, Any], worker: Worker = Depends(require_job_worker)):
    try:
        job = job_service.complete_job(object_session(worker), worker, job_id, result)
    except job_service.JobConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {"state": job.state}


class FailIn(BaseModel):
    error: str
    retryable: bool = True
    result: dict[str, Any] = Field(default_factory=dict)


@router.post("/jobs/{job_id}/fail")
def fail(job_id: str, body: FailIn, worker: Worker = Depends(require_job_worker)):
    try:
        job = job_service.fail_job(object_session(worker), worker, job_id, body.error, body.retryable, body.result)
    except job_service.JobConflict as exc:
        raise HTTPException(409, str(exc)) from None
    return {"state": job.state}


def object_session(obj) -> Session:
    from sqlalchemy.orm import object_session as _os

    return _os(obj)
