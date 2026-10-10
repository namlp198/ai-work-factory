"""Audit trail: every significant action is an append-only audit event plus a structured log line."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from awf.domain.enums import EventType
from awf.domain.models import AuditEvent
from awf.logging import get_logger, log_event
from awf.security import redact_obj

logger = get_logger("audit")


def record(session: Session, event_type: EventType | str, *, actor_type: str = "system",
           actor_id: str = "orchestrator", project_id: str | None = None, workflow_run_id: str | None = None,
           task_id: str | None = None, payload: dict[str, Any] | None = None) -> AuditEvent:
    payload = redact_obj(payload or {})
    event = AuditEvent(
        event_type=str(event_type),
        actor_type=actor_type,
        actor_id=actor_id,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        task_id=task_id,
        payload=payload,
    )
    session.add(event)
    log_event(logger, str(event_type), project_id=project_id, workflow_run_id=workflow_run_id,
              task_id=task_id, actor=f"{actor_type}:{actor_id}", payload=payload)
    return event
