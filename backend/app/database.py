"""SQLite engine / session factory.

The control loop runs inside the event loop but every DB touch is pushed to a
worker thread (`fastapi.concurrency.run_in_threadpool`), hence
`check_same_thread=False` plus a `StaticPool`-free default pool with WAL mode so
readers never block the 1 Hz writer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATABASE_URL

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # pragma: no cover - driver hook
    if not DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    """Create every table declared on `Base`. Safe to call on each boot."""
    from . import models  # noqa: F401  (import registers the mappers)

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a short lived session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background workers (control loop, startup)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
