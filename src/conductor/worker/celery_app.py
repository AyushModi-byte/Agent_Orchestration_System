"""Celery entry point: one Celery task runs one whole graph execution (ADR-004)."""

from __future__ import annotations

import asyncio
import os

import redis
from celery import Celery

from conductor.db.session import init_db, make_engine, make_session_factory
from conductor.llm.gateway import LLMGateway
from conductor.memory.working import WorkingMemory
from conductor.tools import deps, github, linters  # noqa: F401  (import registers tools)
from conductor.tools.registry import default_registry
from conductor.worker.runner import Runtime, execute_task

celery = Celery(
    "conductor",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
)

_runtime: Runtime | None = None


def set_runtime(rt: Runtime | None) -> None:
    """Tests inject a FakeLLM runtime here; production builds one lazily from env."""
    global _runtime
    _runtime = rt


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        engine = make_engine()
        init_db(engine)
        _runtime = Runtime(
            session_factory=make_session_factory(engine),
            llm=LLMGateway(),
            registry=default_registry,
            wm=WorkingMemory(
                redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/1"))
            ),
        )
    return _runtime


@celery.task(name="conductor.run_task")  # type: ignore[untyped-decorator]
def run_task(task_id: str) -> None:
    asyncio.run(execute_task(task_id, get_runtime()))
