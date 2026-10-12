"""Dependency wiring and authentication for the control API."""

from __future__ import annotations

import hmac
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from awf.domain.models import Worker
from awf.orchestrator.orchestrator import Orchestrator
from awf.platform import Platform
from awf.security import hash_token, token_matches


@dataclass
class AppState:
    platform: Platform
    orchestrator: Orchestrator


def get_state(request: Request) -> AppState:
    return request.app.state.awf


def get_session(state: AppState = Depends(get_state)) -> Iterator[Session]:
    session = state.platform.db.session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


def require_admin(state: AppState = Depends(get_state), authorization: str | None = Header(default=None),
                  x_actor: str | None = Header(default=None)) -> str:
    """Admin API token. Without a configured token only a development environment is open."""
    expected = state.platform.settings.api_token
    if not expected:
        if state.platform.settings.environment == "development":
            return (x_actor or "admin")[:120]
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AWF_API_TOKEN is not configured")
    if not hmac.compare_digest(_bearer(authorization), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API token")
    return (x_actor or "admin")[:120]


def require_worker(worker_id: str, session: Session = Depends(get_session, scope="function"),
                   authorization: str | None = Header(default=None)) -> Worker:
    worker = session.get(Worker, worker_id)
    if worker is None or not token_matches(_bearer(authorization), worker.token_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid worker credentials")
    return worker


def require_job_worker(session: Session = Depends(get_session, scope="function"),
                       authorization: str | None = Header(default=None)) -> Worker:
    """Job endpoints: the worker is identified by its token."""
    token = _bearer(authorization)
    worker = session.scalar(select(Worker).where(Worker.token_hash == hash_token(token))) if token else None
    if worker is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid worker credentials")
    return worker
