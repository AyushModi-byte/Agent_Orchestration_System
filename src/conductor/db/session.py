"""Engine/session helpers. DATABASE_URL points at Postgres in the stack; tests use SQLite."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from conductor.db.models import Base

DEFAULT_URL = "sqlite:///conductor.db"


def make_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get("DATABASE_URL", DEFAULT_URL)
    if url.startswith("sqlite") and ":memory:" in url:
        # one shared connection so threads (API + worker) see the same in-memory database
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
