"""PostgreSQL control plane: migrations, SQLite -> PostgreSQL import, pg_dump backup and restore.

Runs only with AWF_TEST_DATABASE_URL pointing at a disposable PostgreSQL database (see tests/conftest.py);
backup/restore also need pg_dump/pg_restore on PATH. CI runs them against a postgres:16 service."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from awf import dbops
from awf.domain.db import Database
from awf.domain.models import AuditEvent, Job, JobLog, Project, Task, Worker, WorkflowRun
from tests.conftest import TEST_DATABASE_URL, _fresh_postgres

ROOT = Path(__file__).resolve().parents[2]


def _alembic(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "infrastructure" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def pg():
    if not TEST_DATABASE_URL:
        pytest.skip("set AWF_TEST_DATABASE_URL to a disposable PostgreSQL database")
    _fresh_postgres(TEST_DATABASE_URL)
    command.upgrade(_alembic(TEST_DATABASE_URL), "head")
    return TEST_DATABASE_URL


def test_migrations_on_postgres_match_the_models(pg):
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from awf.domain.models import Base

    engine = create_engine(pg)
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
    engine.dispose()
    command.downgrade(_alembic(pg), "base")
    command.upgrade(_alembic(pg), "head")


def test_audit_trail_is_append_only_on_postgres(pg):
    db = Database(pg)
    with db.transaction() as s:
        s.add(AuditEvent(id="e1", actor_type="system", actor_id="t", event_type="X", payload={}))
    with pytest.raises(DBAPIError):
        with db.transaction() as s:
            s.execute(text("UPDATE audit_events SET event_type = 'Y'"))
    db.engine.dispose()


# ---------------------------------------------------------------------------- SQLite -> PostgreSQL
def _sqlite_with_data(tmp_path) -> str:
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    command.upgrade(_alembic(url), "head")
    db = Database(url)
    created = datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc)
    with db.transaction() as s:
        s.add(Project(id="p1", name="Robo", slug="robo-station", repository={"url": "git@x:robo.git"},
                      labels={"platform": "windows"}, created_at=created, updated_at=created))
        s.add(Worker(id="w1", name="pc-robo", platform="windows-amd64", capabilities=["dotnet", "git"], max_jobs=3,
                     last_heartbeat=created))
        s.flush()
        s.add(WorkflowRun(id="r1", project_id="p1", workflow_id="software-development", workflow_version="1.0",
                          status="RUNNING", input_json={"objective": "Tiếng Việt có dấu"}, assigned_worker_id="w1",
                          requirements={"platform": "windows", "capabilities": ["git"]}, created_at=created))
        s.flush()
        s.add(Task(id="t1", workflow_run_id="r1", stage_id="requirement", task_key="requirement", task_type="agent",
                   title="Requirement", state="DONE"))
        s.flush()
        s.add(Job(id="j1", task_id="t1", workflow_run_id="r1", job_type="agent", pinned=True, assigned_worker_id="w1",
                  payload={"a": [1, 2]}, state="SUCCESS"))
        s.flush()
        for i in range(1200):  # more than one batch
            s.add(JobLog(job_id="j1", stream="stdout", line=f"line {i}"))
        s.add(AuditEvent(id="e1", actor_type="system", actor_id="t", event_type="WORKFLOW_STARTED", payload={}))
    db.engine.dispose()
    return url


def test_import_sqlite_copies_everything(pg, tmp_path):
    source = _sqlite_with_data(tmp_path)
    copied = dbops.import_sqlite(source, pg, log=lambda line: None)
    assert copied["job_logs"] == 1200 and copied["projects"] == 1 and copied["audit_events"] == 1

    db = Database(pg)
    with db.session() as s:
        run = s.get(WorkflowRun, "r1")
        assert run.input_json == {"objective": "Tiếng Việt có dấu"} and run.assigned_worker_id == "w1"
        # Stored UTC in SQLite, still the same instant in PostgreSQL whatever the server's time zone.
        assert run.created_at == datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc)
        assert s.get(Worker, "w1").max_jobs == 3 and s.get(Job, "j1").pinned is True
    with db.transaction() as s:  # the job_logs id sequence continues after the imported rows
        s.add(JobLog(job_id="j1", stream="stdout", line="after import"))
    with db.session() as s:
        assert s.scalar(select(func.max(JobLog.id))) == 1201
    db.engine.dispose()


def test_import_refuses_a_database_that_already_has_data(pg, tmp_path):
    source = _sqlite_with_data(tmp_path)
    dbops.import_sqlite(source, pg, log=lambda line: None)
    with pytest.raises(ValueError, match="not empty"):
        dbops.import_sqlite(source, pg, log=lambda line: None)


def test_import_refuses_different_revisions(pg, tmp_path):
    url = f"sqlite:///{(tmp_path / 'older.db').as_posix()}"
    command.upgrade(_alembic(url), "0002")
    with pytest.raises(ValueError, match="revisions differ"):
        dbops.import_sqlite(url, pg, log=lambda line: None)


def test_import_rolls_back_on_error(pg, tmp_path, monkeypatch):
    source = _sqlite_with_data(tmp_path)
    real = dbops._utc
    calls = {"n": 0}

    def boom(value):
        calls["n"] += 1
        if calls["n"] > 20:
            raise ValueError("disk on fire")
        return real(value)

    monkeypatch.setattr(dbops, "_utc", boom)
    with pytest.raises(ValueError, match="disk on fire"):
        dbops.import_sqlite(source, pg, log=lambda line: None)
    db = Database(pg)
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Project)) == 0
    db.engine.dispose()


# ---------------------------------------------------------------------------- backup / restore
@pytest.mark.skipif(not (dbops.pg_tool("pg_dump") and dbops.pg_tool("pg_restore")),
                    reason="pg_dump/pg_restore not on PATH")
def test_backup_and_restore_round_trip(pg, tmp_path):
    db = Database(pg)
    with db.transaction() as s:
        s.add(Project(id="p1", name="Kept", slug="kept", labels={"platform": "linux"}))
    for _ in range(3):
        dump = dbops.backup_postgres(pg, tmp_path / "backups", keep=2)
    assert dump.suffix == ".dump" and len(list((tmp_path / "backups").glob("awf-*.dump"))) <= 2

    with db.transaction() as s:
        s.add(Project(id="p2", name="Lost", slug="lost", labels={"platform": "linux"}))
        s.get(Project, "p1").name = "Changed"
    db.engine.dispose()
    dbops.restore_postgres(pg, dump)
    db = Database(pg)
    with db.session() as s:
        assert [p.name for p in s.scalars(select(Project))] == ["Kept"]
    db.engine.dispose()


def test_password_is_passed_in_the_environment_not_argv():
    args, env = dbops.libpq_env("postgresql+psycopg://awf:s%40cret@db.local:6543/awf")
    assert "s@cret" not in " ".join(args) and env["PGPASSWORD"] == "s@cret"
    assert args == ["--host", "db.local", "--port", "6543", "--username", "awf", "--dbname", "awf"]
