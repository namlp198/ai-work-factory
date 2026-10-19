"""Engine / session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from awf.domain.models import Base


class Database:
    def __init__(self, url: str, echo: bool = False):
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False, "timeout": 30}
        self.url = url
        options = {}
        if url.startswith("postgresql"):
            # A 24/7 control plane outlives server restarts and idle-connection drops: test connections before use.
            # API threads + orchestrator + one connection per concurrent worker request need some headroom.
            options = {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20, "pool_recycle": 1800}
        self.engine: Engine = create_engine(url, echo=echo, future=True, connect_args=connect_args, **options)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        """Used by tests and the `--create-schema` dev shortcut. Production uses Alembic."""
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._factory()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
