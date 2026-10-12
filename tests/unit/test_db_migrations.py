"""`awf db upgrade` on fresh, already-migrated and legacy (create_all, unversioned) databases."""

from __future__ import annotations

import argparse
import dataclasses
import sqlite3
from pathlib import Path

import pytest

from awf import cli
from awf.domain.db import Database
from awf.settings import build_settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'awf.db').as_posix()}"
    settings = dataclasses.replace(build_settings(ROOT), database_url=url)
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    return url, tmp_path / "awf.db"


def _revision(path: Path) -> str | None:
    with sqlite3.connect(path) as conn:
        rows = conn.execute("select version_num from alembic_version").fetchall()
    return rows[0][0] if rows else None


def _upgrade() -> int:
    return cli.cmd_db(argparse.Namespace(action="upgrade"))


def test_fresh_and_repeated_upgrade(db_url):
    _, path = db_url
    assert _upgrade() == 0 and _revision(path) == "0001"
    assert _upgrade() == 0 and _revision(path) == "0001"  # idempotent


def test_legacy_create_all_database_is_adopted(db_url):
    url, path = db_url
    Database(url).create_all()  # what `awf demo` used to do: tables, no alembic_version
    with sqlite3.connect(path) as conn:
        conn.execute("insert into projects (id, name, slug, description, status, repository, confidentiality, "
                     "labels, created_at, updated_at) values ('p1', 'kept', 'kept', '', 'ACTIVE', '{}', "
                     "'internal', '{}', '2026-10-10', '2026-10-10')")
    assert _upgrade() == 0 and _revision(path) == "0001"
    with sqlite3.connect(path) as conn:
        assert conn.execute("select name from projects").fetchall() == [("kept",)]  # data preserved


def test_legacy_database_after_a_failed_upgrade_is_adopted(db_url):
    url, path = db_url
    Database(url).create_all()
    with sqlite3.connect(path) as conn:  # a failed upgrade leaves an empty alembic_version behind
        conn.execute("create table alembic_version (version_num varchar(32) not null primary key)")
    assert _upgrade() == 0 and _revision(path) == "0001"


def test_legacy_database_with_different_schema_is_refused(db_url):
    url, path = db_url
    Database(url).create_all()
    with sqlite3.connect(path) as conn:
        conn.execute("alter table projects add column legacy_column text")
    with pytest.raises(SystemExit, match="no migration history"):
        _upgrade()


def test_db_create_records_head(db_url):
    _, path = db_url
    assert cli.cmd_db(argparse.Namespace(action="create")) == 0
    assert _revision(path) == "0001"
    assert _upgrade() == 0
