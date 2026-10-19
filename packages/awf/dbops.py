"""Database operations for `awf db`: backups (SQLite and PostgreSQL) and moving a control plane's data
from SQLite to PostgreSQL."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from sqlalchemy import DateTime, Integer, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url

BACKUP_PATTERNS = ("awf-*.db", "awf-*.dump")


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def _rotate(target_dir: Path, pattern: str, keep: int) -> int:
    backups = sorted(target_dir.glob(pattern))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink()
    return min(len(backups), keep) if keep > 0 else len(backups)


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def backup_sqlite(url: str, target_dir: Path, keep: int) -> Path:
    """Consistent online copy (safe while the control plane runs)."""
    import sqlite3

    source = Path(url[len("sqlite:///"):])
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"awf-{_stamp()}.db"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    _rotate(target_dir, "awf-*.db", keep)
    return target


def libpq_env(url: str) -> tuple[list[str], dict[str, str]]:
    """Connection arguments for pg_dump/pg_restore/psql from a SQLAlchemy URL; the password goes in the
    environment (PGPASSWORD), never on the command line where `ps` would show it."""
    u = make_url(url)
    args = ["--host", u.host or "localhost", "--port", str(u.port or 5432), "--username", u.username or "",
            "--dbname", u.database or ""]
    env = {**os.environ}
    if u.password:
        env["PGPASSWORD"] = unquote(str(u.password))
    return args, env


def pg_tool(name: str) -> str | None:
    """pg_dump/pg_restore from PATH, or from the Ubuntu layout /usr/lib/postgresql/<version>/bin."""
    found = shutil.which(name)
    if found:
        return found
    versions = sorted(Path("/usr/lib/postgresql").glob("*/bin/" + name), reverse=True)
    return str(versions[0]) if versions else None


def backup_postgres(url: str, target_dir: Path, keep: int) -> Path:
    """pg_dump in custom format (compressed, restorable with pg_restore, consistent while running)."""
    pg_dump = pg_tool("pg_dump")
    if not pg_dump:
        raise RuntimeError("pg_dump not found: sudo apt install postgresql-client")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"awf-{_stamp()}.dump"
    args, env = libpq_env(url)
    done = subprocess.run([pg_dump, "--format=custom", "--no-owner", "--file", str(target), *args],
                          env=env, capture_output=True, text=True)
    if done.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed: {done.stderr.strip()[-1000:]}")
    _rotate(target_dir, "awf-*.dump", keep)
    return target


def restore_postgres(url: str, dump: Path) -> None:
    """Replace the database's contents with a pg_dump backup. Stop the control plane first."""
    pg_restore = pg_tool("pg_restore")
    if not pg_restore:
        raise RuntimeError("pg_restore not found: sudo apt install postgresql-client")
    if not dump.is_file():
        raise ValueError(f"no backup file at {dump}")
    args, env = libpq_env(url)
    done = subprocess.run([pg_restore, "--clean", "--if-exists", "--no-owner", "--single-transaction", *args,
                           str(dump)], env=env, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"pg_restore failed: {done.stderr.strip()[-1500:]}")


# ---------------------------------------------------------------------------- SQLite -> PostgreSQL
def _utc(value: Any) -> Any:
    # SQLite hands timestamps back without a zone; they were written in UTC. PostgreSQL would otherwise read
    # them in the server's TimeZone (Asia/Ho_Chi_Minh on many Ubuntu boxes) and shift them by hours.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def import_sqlite(source_url: str, target_url: str, batch: int = 500, log=print) -> dict[str, int]:
    """Copy every row of a SQLite control-plane database into an empty, migrated PostgreSQL database.

    Both must be at the same migration revision (run `awf db upgrade` against each first). The copy is one
    transaction: on any error nothing is written. Returns rows copied per table."""
    from alembic.runtime.migration import MigrationContext

    from awf.domain.models import Base

    if not is_sqlite(source_url):
        raise ValueError(f"source must be a SQLite URL, got {source_url}")
    if not is_postgres(target_url):
        raise ValueError("target (DATABASE_URL) must be PostgreSQL; set it in .env first")
    path = Path(source_url[len("sqlite:///"):])
    if not path.is_file():
        raise ValueError(f"no SQLite database at {path}")

    source = create_engine(source_url)
    target = create_engine(target_url)
    copied: dict[str, int] = {}
    try:
        with source.connect() as src, target.begin() as dst:
            src_rev = MigrationContext.configure(src).get_current_revision()
            dst_rev = MigrationContext.configure(dst).get_current_revision()
            if src_rev != dst_rev or dst_rev is None:
                raise ValueError(f"revisions differ (SQLite {src_rev}, PostgreSQL {dst_rev}): run `awf db upgrade` "
                                 f"on both so they are at the same revision")
            existing = set(inspect(src).get_table_names())
            for table in Base.metadata.sorted_tables:  # parents before children (foreign keys)
                if dst.scalar(select(func.count()).select_from(table)):
                    raise ValueError(f"PostgreSQL table {table.name} is not empty; import only into a new database")
            for table in Base.metadata.sorted_tables:
                if table.name not in existing:
                    continue
                stamps = [c.name for c in table.columns if isinstance(c.type, DateTime)]
                count = 0
                result = src.execute(select(table))
                while rows := result.fetchmany(batch):
                    values = [{k: (_utc(v) if k in stamps else v) for k, v in row._mapping.items()} for row in rows]
                    dst.execute(table.insert(), values)
                    count += len(values)
                copied[table.name] = count
                log(f"  {table.name}: {count}")
            # Explicit ids were inserted into identity/serial columns: move their sequences past them.
            for table in Base.metadata.sorted_tables:
                for column in table.primary_key.columns:
                    if isinstance(column.type, Integer):
                        seq = dst.scalar(text("SELECT pg_get_serial_sequence(:t, :c)"),
                                         {"t": table.name, "c": column.name})
                        if seq:
                            dst.execute(text(f"SELECT setval('{seq}', COALESCE((SELECT MAX({column.name}) "
                                             f"FROM {table.name}), 0) + 1, false)"))
    finally:
        source.dispose()
        target.dispose()
    return copied
