"""Shared setup for observability and replay tests: one DB, real OTel tracer, FakeLLM."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from conductor.config import Price
from conductor.db.models import Task
from conductor.llm.fake import FakeLLM
from conductor.obs.exporter import DbSpanExporter
from conductor.obs.tracing import NoopTracer, init_tracing, set_tracer
from conductor.tools.call_log import DbToolCallStore
from conductor.worker.runner import Runtime, execute_task
from tests.helpers import Behavior, make_registry, make_store, plan_json, step

PLAN = plan_json(
    [
        step("s1", "code_analysis"),
        step("s2", "security"),
        step("s3", "test_execution", ["s1", "s2"]),
    ]
)
# fake/<role> is the model name FakeLLM reports; give each a price so costs are non-zero
PRICES = {
    f"fake/{r}": Price(input=1000.0, output=2000.0)
    for r in ("planner", "specialist", "reviewer", "cheap")
}


@dataclass
class Env:
    sf: Any
    store: Any
    runtime: Runtime
    behavior: Behavior
    registry: Any
    saver: MemorySaver


def make_env(behavior: Behavior | None = None) -> Env:
    """A fresh database, a recording tracer and a runtime. Call teardown() when done."""
    store = make_store()  # also creates the schema and the session factory
    sf = store._sf  # noqa: SLF001
    init_tracing(DbSpanExporter(sf), batch=False)
    registry = make_registry()
    registry.store = DbToolCallStore(sf)
    behavior = behavior or Behavior(PLAN)
    saver = MemorySaver()

    @asynccontextmanager
    async def checkpointer() -> Any:
        yield saver

    rt = Runtime(
        session_factory=sf,
        llm=FakeLLM({"cheap": ['{"what_asked": "x"}'] * 3}, responder=behavior),
        registry=registry,
        memory_store=store,
        checkpointer=checkpointer,
        prices=PRICES,
    )
    return Env(sf, store, rt, behavior, registry, saver)


def teardown() -> None:
    set_tracer(NoopTracer())


async def run_task(env: Env, **request: Any) -> str:
    tid = uuid.uuid4().hex
    body = {"request": "review auth module", "repo": "o/r", "user_id": "u1", **request}
    with env.sf() as s:
        s.add(Task(id=tid, request=body, status="queued"))
        s.commit()
    await execute_task(tid, env.runtime)
    return tid
