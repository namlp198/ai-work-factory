"""ORM -> JSON views."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from awf.domain.models import (
    AgentRun,
    Approval,
    Artifact,
    ArtifactRelation,
    AuditEvent,
    Job,
    Project,
    Task,
    Worker,
    WorkflowRun,
)


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def project(p: Project) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "slug": p.slug, "description": p.description, "status": p.status,
            "repository": p.repository, "confidentiality": p.confidentiality, "budget_usd": _num(p.budget_usd),
            "labels": p.labels, "created_at": _ts(p.created_at), "updated_at": _ts(p.updated_at)}


def run(r: WorkflowRun, stages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ctx = r.context_json or {}
    data = {"id": r.id, "project_id": r.project_id, "workflow_id": r.workflow_id,
            "workflow_version": r.workflow_version, "status": r.status, "input": r.input_json,
            "integration_branch": ctx.get("integration_branch"), "fix_loops": ctx.get("fix_loops", {}),
            "planned_tasks": ctx.get("planned_tasks", []), "error": r.error,
            "created_at": _ts(r.created_at), "started_at": _ts(r.started_at), "finished_at": _ts(r.finished_at)}
    if stages is not None:
        data["stages"] = stages
    return data


def task(t: Task) -> dict[str, Any]:
    return {"id": t.id, "workflow_run_id": t.workflow_run_id, "stage_id": t.stage_id, "task_key": t.task_key,
            "task_type": t.task_type, "title": t.title, "description": t.description, "state": t.state,
            "assigned_role": t.assigned_role, "iteration": t.iteration, "attempt": t.attempt,
            "max_attempts": t.max_attempts, "acceptance_criteria": t.acceptance_criteria,
            "input": {k: v for k, v in (t.input_json or {}).items()}, "output": t.output_json,
            "created_at": _ts(t.created_at), "updated_at": _ts(t.updated_at)}


def agent_run(a: AgentRun) -> dict[str, Any]:
    return {"id": a.id, "task_id": a.task_id, "job_id": a.job_id, "worker_id": a.worker_id, "role": a.role,
            "profile_version": a.profile_version, "model_class": a.model_class, "model_provider": a.model_provider,
            "model_name": a.model_name, "route_reason": a.route_reason, "status": a.status,
            "input": a.input_json, "output": a.output_json, "tool_calls": a.tool_calls, "critic": a.critic_json,
            "token_input": a.token_input, "token_output": a.token_output, "cost_estimate": _num(a.cost_estimate),
            "duration_ms": a.duration_ms, "retry_count": a.retry_count, "error": a.error,
            "created_at": _ts(a.created_at), "started_at": _ts(a.started_at), "finished_at": _ts(a.finished_at)}


def job(j: Job, include_payload: bool = False) -> dict[str, Any]:
    data = {"id": j.id, "task_id": j.task_id, "workflow_run_id": j.workflow_run_id, "agent_run_id": j.agent_run_id,
            "worker_id": j.worker_id, "job_type": j.job_type, "requirements": j.requirements, "state": j.state,
            "attempt": j.attempt, "max_attempts": j.max_attempts, "error": j.error,
            "created_at": _ts(j.created_at), "started_at": _ts(j.started_at), "finished_at": _ts(j.finished_at)}
    if include_payload:
        data["payload"] = j.payload
    return data


def artifact(a: Artifact) -> dict[str, Any]:
    return {"id": a.id, "project_id": a.project_id, "workflow_run_id": a.workflow_run_id, "task_id": a.task_id,
            "agent_run_id": a.agent_run_id, "stage_id": a.stage_id, "type": a.type, "name": a.name,
            "media_type": a.media_type, "storage_backend": a.storage_backend, "uri": a.uri, "version": a.version,
            "checksum": a.checksum, "size_bytes": a.size_bytes, "metadata": a.metadata_json,
            "created_at": _ts(a.created_at)}


def relation(r: ArtifactRelation) -> dict[str, Any]:
    return {"id": r.id, "from_artifact_id": r.from_artifact_id, "to_artifact_id": r.to_artifact_id,
            "relation_type": r.relation_type, "metadata": r.metadata_json}


def approval(a: Approval) -> dict[str, Any]:
    return {"id": a.id, "project_id": a.project_id, "workflow_run_id": a.workflow_run_id, "task_id": a.task_id,
            "action": a.action, "scope": a.scope, "risk_level": a.risk_level, "state": a.state,
            "requested_by": a.requested_by, "approved_by": a.approved_by, "comment": a.comment,
            "created_at": _ts(a.created_at), "resolved_at": _ts(a.resolved_at)}


def worker(w: Worker) -> dict[str, Any]:
    return {"id": w.id, "name": w.name, "platform": w.platform, "os_version": w.os_version, "status": w.status,
            "capabilities": w.capabilities, "labels": w.labels, "load": w.load_json,
            "machine_id": (w.machine_id or "")[:8], "last_heartbeat": _ts(w.last_heartbeat),
            "created_at": _ts(w.created_at)}


def audit_event(e: AuditEvent) -> dict[str, Any]:
    return {"id": e.id, "project_id": e.project_id, "workflow_run_id": e.workflow_run_id, "task_id": e.task_id,
            "actor_type": e.actor_type, "actor_id": e.actor_id, "event_type": e.event_type, "payload": e.payload,
            "created_at": _ts(e.created_at)}
