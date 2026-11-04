"""Celery entry point: one Celery task runs one whole graph execution (ADR-004)."""

from __future__ import annotations

import asyncio
import os

import redis
from celery import Celery

from conductor.db.session import init_db, make_engine, make_session_factory
from conductor.llm.gateway import LLMGateway
from conductor.memory.longterm import LongTermStore, make_chroma_client
from conductor.memory.manager import MemoryManager
from conductor.memory.working import WorkingMemory
from conductor.tools import deps, github, linters  # noqa: F401  (import registers tools)
from conductor.tools.registry import default_registry
from conductor.worker.runner import Runtime, execute_task
from conductor.worker.runner import resume_task as resume_execution

celery = Celery(
    "conductor",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
)

celery.conf.beat_schedule = {
    "memory-maintenance": {"task": "conductor.maintain_memory", "schedule": 86400.0},
}

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
        sf = make_session_factory(engine)
        _runtime = Runtime(
            session_factory=sf,
            memory_store=LongTermStore(make_chroma_client(), sf),
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


@celery.task(name="conductor.resume_task")  # type: ignore[untyped-decorator]
def resume_task(task_id: str, approval_id: str) -> None:
    """Continue a paused run after a human decision; works from any worker via the checkpoint."""
    asyncio.run(resume_execution(task_id, approval_id, get_runtime()))


@celery.task(name="conductor.maintain_memory")  # type: ignore[untyped-decorator]
def maintain_memory() -> dict[str, int]:
    """Daily beat job: merge near-duplicates, then decay and expire stale memories."""
    rt = get_runtime()
    if rt.memory_store is None:
        return {"merged": 0, "expired": 0}
    manager = MemoryManager(rt.memory_store, rt.llm)
    merged = asyncio.run(manager.consolidate())
    return {"merged": merged, "expired": manager.expire()}
