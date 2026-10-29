"""Which audit events become notifications, and what the message says.

`enqueue_for_event` runs inside audit.record, in the same transaction as the event: a notification exists if and
only if its event was committed (transactional outbox). Delivery happens later in awf.notify.service."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from awf.domain.enums import NotificationState
from awf.domain.models import Approval, AuditEvent, Notification, Project, Worker, WorkflowRun
from awf.notify.config import NotifyConfig

EVENT_KINDS = {
    "APPROVAL_REQUESTED": "approval",
    "APPROVAL_GRANTED": "approval_resolved",
    "APPROVAL_REJECTED": "approval_resolved",
    "WORKFLOW_FAILED": "failed",
    "WORKFLOW_COMPLETED": "completed",
    "WORKER_OFFLINE": "worker_offline",
    "RUN_UNASSIGNED": "run_moved",
}

ACTION_LABELS = {
    "approve_architecture": "Duyệt kiến trúc",
    "approve_merge": "Duyệt merge vào nhánh chính",
    "approve_publish": "Duyệt xuất bản",
    "approve_effort": "Duyệt dùng effort cao",
    "resolve_conflict": "Cần bạn giải conflict git",
}


def _short(text: str | None, limit: int = 300) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _run_line(run: WorkflowRun | None) -> str:
    if run is None:
        return ""
    return f"Run {run.id[:8]} · {run.workflow_id}: {_short((run.input_json or {}).get('objective'), 200)}"


def render(session: Session, event: AuditEvent, kind: str, public_url: str) -> dict[str, Any] | None:
    payload = event.payload or {}
    project = session.get(Project, event.project_id) if event.project_id else None
    run = session.get(WorkflowRun, event.workflow_run_id) if event.workflow_run_id else None
    project_line = f"Project: {project.name}" if project else ""
    run_link = f"{public_url}/#/runs/{run.id}" if public_url and run else None

    if kind == "approval":
        approval = session.get(Approval, payload.get("approval_id"))
        if approval is None:
            return None
        label = ACTION_LABELS.get(approval.action, approval.action)
        description = (approval.scope or {}).get("description") or ""
        return {"title": f"🔔 Cần phê duyệt: {label}", "approval_id": approval.id,
                "body": "\n".join(x for x in (project_line, _run_line(run), f"Mức rủi ro: {approval.risk_level}",
                                              _short(description)) if x),
                "link": f"{public_url}/#/approvals/{approval.id}" if public_url else None}
    if kind == "approval_resolved":
        approval = session.get(Approval, payload.get("approval_id"))
        if approval is None:
            return None
        ok = event.event_type == "APPROVAL_GRANTED"
        verb = "đã được duyệt" if ok else "đã bị từ chối"
        comment = f"Ghi chú: {_short(payload.get('comment'), 200)}" if payload.get("comment") else ""
        return {"title": f"{'✅' if ok else '❌'} {ACTION_LABELS.get(approval.action, approval.action)} {verb}",
                "approval_id": approval.id,
                "body": "\n".join(x for x in (f"Bởi: {event.actor_id}", project_line, _run_line(run), comment) if x),
                "link": run_link}
    if kind == "failed":
        return {"title": "❌ Run thất bại",
                "body": "\n".join(x for x in (project_line, _run_line(run),
                                              f"Lý do: {_short(payload.get('reason'), 500)}") if x),
                "link": run_link}
    if kind == "completed":
        branch = (run.context_json or {}).get("integration_branch") if run else None
        return {"title": "✅ Run hoàn tất",
                "body": "\n".join(x for x in (project_line, _run_line(run), f"Nhánh: {branch}" if branch else "") if x),
                "link": run_link}
    if kind == "worker_offline":
        return {"title": f"⚠️ Máy {payload.get('name', '?')} mất kết nối",
                "body": f"Heartbeat cuối: {payload.get('last_heartbeat')}. Job đang chạy được giao lại; run sẽ chuyển "
                        f"máy nếu máy không quay lại.",
                "link": f"{public_url}/#/workers" if public_url else None}
    if kind == "run_moved":
        worker = session.get(Worker, payload.get("worker_id")) if payload.get("worker_id") else None
        return {"title": "↪️ Run được chuyển khỏi máy" + (f" {worker.name}" if worker else ""),
                "body": "\n".join(x for x in (project_line, _run_line(run), f"Lý do: {_short(payload.get('reason'))}")
                                  if x),
                "link": run_link}
    return None


def enqueue_for_event(session: Session, event: AuditEvent, config: NotifyConfig | None = None) -> Notification | None:
    kind = EVENT_KINDS.get(event.event_type)
    if kind is None:
        return None
    config = config or NotifyConfig.from_env()
    if not config.enabled or kind not in config.kinds:
        return None
    if kind == "run_moved" and event.actor_type == "human":
        return None  # someone moved it by hand: they already know
    message = render(session, event, kind, config.public_url)
    if message is None:
        return None
    note = Notification(kind=kind, event_type=event.event_type, project_id=event.project_id,
                        workflow_run_id=event.workflow_run_id, approval_id=message.get("approval_id"),
                        title=message["title"][:300], body=message.get("body") or "", link=message.get("link"),
                        state=NotificationState.PENDING, deliveries={})
    session.add(note)
    return note
