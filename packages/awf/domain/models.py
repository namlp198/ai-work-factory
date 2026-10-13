"""SQLAlchemy ORM models — the MVP tables of Technical Design §6 (plus the columns the engine needs)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JsonType, list[Any]: JsonType}


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    # repository: {url, base_branch, build_command[], test_command[], lint_command[]}
    repository: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    confidentiality: Mapped[str] = mapped_column(String(32), default="internal")
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    runs: Mapped[list[WorkflowRun]] = relationship(back_populates="project")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(120))
    workflow_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    # Engine bookkeeping: stage iterations, planned tasks, fix-loop feedback, integration branch.
    context_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="runs")
    tasks: Mapped[list[Task]] = relationship(back_populates="workflow_run", order_by="Task.created_at")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stage_id: Mapped[str] = mapped_column(String(120))
    task_key: Mapped[str] = mapped_column(String(160))
    task_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(32), default="NEW", index=True)
    assigned_role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    iteration: Mapped[int] = mapped_column(Integer, default=1)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    acceptance_criteria: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    # Stage spec snapshot, planned-task item, tool spec, feedback artifact ids.
    input_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    # Last critic result, decision, output artifact ids.
    output_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    workflow_run: Mapped[WorkflowRun] = relationship(back_populates="tasks")


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    depends_on_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    relation: Mapped[str] = mapped_column(String(32), default="blocks")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    role: Mapped[str] = mapped_column(String(120))
    profile_version: Mapped[str] = mapped_column(String(32), default="")
    model_class: Mapped[str] = mapped_column(String(32), default="")
    model_provider: Mapped[str] = mapped_column(String(64), default="")
    model_name: Mapped[str] = mapped_column(String(120), default="")
    route_reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    input_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    output_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    tool_calls: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    critic_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    token_input: Mapped[int] = mapped_column(BigInteger, default=0)
    token_output: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_estimate: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    duration_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("project_id", "name", "version", name="uq_artifact_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stage_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    type: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(400))
    media_type: Mapped[str] = mapped_column(String(120), default="text/markdown")
    storage_backend: Mapped[str] = mapped_column(String(32))
    uri: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    checksum: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # Provenance: input_artifact_ids, output_name, iteration, ...
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ArtifactRelation(Base):
    __tablename__ = "artifact_relations"
    __table_args__ = (Index("ix_relations_from", "from_artifact_id"), Index("ix_relations_to", "to_artifact_id"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    from_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"))
    to_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"))
    relation_type: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    platform: Mapped[str] = mapped_column(String(64))
    os_version: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="online")
    capabilities: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    labels: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    load_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    token_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Stable id generated once per machine (var/worker-id); stops two machines sharing a worker name.
    machine_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    # {platform, capabilities[]}
    requirements: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    state: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    result: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set once the orchestrator has consumed the terminal result (idempotency guard).
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A claimed job must be finished before this time, otherwise it is requeued (lost worker/network).
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobLog(Base):
    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    stream: Mapped[str] = mapped_column(String(16), default="stdout")
    line: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120))
    scope: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    risk_level: Mapped[int] = mapped_column(Integer, default=2)
    state: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    requested_by: Mapped[str] = mapped_column(String(120), default="orchestrator")
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set once the orchestrator has applied the resolution to the task.
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Append-only. On PostgreSQL a trigger rejects UPDATE/DELETE (see migration 0001)."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
