"""Runs one task through the graph and persists progress as it goes.

DB writes are small synchronous calls; the graph is the only long-running part.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from conductor.agents.reviewer import Reviewer
from conductor.agents.specialists import build_specialists
from conductor.agents.supervisor import Supervisor
from conductor.config import Price, load_policies, load_prices
from conductor.db.models import LLMCall, PlanStep, Task, TaskEvent
from conductor.graph.builder import GraphDeps, build_graph
from conductor.llm.recording import RecordingLLM
from conductor.llm.types import LLM, LLMResult
from conductor.memory.working import WorkingMemory
from conductor.tools.registry import ToolRegistry


@asynccontextmanager
async def memory_checkpointer() -> Any:
    from langgraph.checkpoint.memory import MemorySaver

    yield MemorySaver()


def postgres_checkpointer(url: str) -> Callable[[], AbstractAsyncContextManager[Any]]:
    """Durable checkpoints so a run can resume after a worker restart."""

    @asynccontextmanager
    async def ctx() -> Any:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            await saver.setup()
            yield saver

    return ctx


def default_checkpointer() -> Callable[[], AbstractAsyncContextManager[Any]]:
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres"):
        return postgres_checkpointer(url.replace("+psycopg", ""))
    return memory_checkpointer


@dataclass
class Runtime:
    session_factory: sessionmaker[Session]
    llm: LLM
    registry: ToolRegistry
    wm: WorkingMemory | None = None
    checkpointer: Callable[[], AbstractAsyncContextManager[Any]] = field(
        default_factory=default_checkpointer
    )
    prices: dict[str, Price] = field(default_factory=load_prices)


def _step_status(state: dict[str, Any], sid: str) -> str:
    v = state.get("verdicts", {}).get(sid)
    r = state.get("results", {}).get(sid)
    if v and r and v["run_id"] == r["run_id"]:
        return "approved" if v["approved"] else "rejected"
    if state.get("fail_count", {}).get(sid):
        return "retrying"
    return "pending"


def _sync_steps(s: Session, task_id: str, state: dict[str, Any]) -> None:
    plan = state.get("plan")
    if not plan:
        return
    rows = {r.step_id: r for r in s.query(PlanStep).filter(PlanStep.task_id == task_id).all()}
    for step in plan["steps"]:
        row = rows.get(step["id"])
        if row is None:
            row = PlanStep(
                task_id=task_id,
                step_id=step["id"],
                description=step["description"],
                specialist=step["specialist"],
                depends_on=step["depends_on"],
            )
            s.add(row)
        row.status = _step_status(state, step["id"])


async def execute_task(task_id: str, rt: Runtime) -> None:
    sf = rt.session_factory
    with sf() as s:
        task = s.get(Task, task_id)
        assert task is not None
        request = dict(task.request)
        task.status = "running"
        s.commit()

    def sink(role: str, res: LLMResult, cost: float) -> None:
        with sf() as s2:
            u = res.usage
            s2.add(
                LLMCall(
                    task_id=task_id,
                    role=role,
                    model=u.model,
                    tokens_in=u.tokens_in,
                    tokens_out=u.tokens_out,
                    latency_ms=u.latency_ms,
                    cache_hit=u.cache_hit,
                    cost_usd=cost,
                )
            )
            s2.commit()

    llm = RecordingLLM(rt.llm, sink, rt.prices)
    policy = load_policies().escalation
    deps = GraphDeps(
        supervisor=Supervisor(llm),
        specialists=build_specialists(llm, rt.registry),
        reviewer=Reviewer(llm, policy.min_reviewer_score),
        policy=policy,
        wm=rt.wm,
    )
    final: dict[str, Any] = {}
    persisted = 0
    try:
        async with rt.checkpointer() as cp:
            graph = build_graph(deps, cp)
            async for state in graph.astream(
                {"task_id": task_id, "request": request},
                config={"configurable": {"thread_id": task_id}},
                stream_mode="values",
            ):
                final = state
                with sf() as s:
                    for ev in state.get("events", [])[persisted:]:
                        s.add(TaskEvent(task_id=task_id, type=ev["type"], payload=ev))
                    persisted = len(state.get("events", []))
                    _sync_steps(s, task_id, state)
                    s.commit()
        with sf() as s:
            t = s.get(Task, task_id)
            assert t is not None
            t.status = final.get("status", "completed")
            t.needs_human = bool(final.get("needs_human"))
            t.escalation_reason = final.get("escalation_reason")
            t.report = final.get("report")
            s.commit()
    except Exception as exc:
        if rt.wm:
            rt.wm.clear(task_id)
        with sf() as s:
            t = s.get(Task, task_id)
            assert t is not None
            t.status = "failed"
            t.error = f"{type(exc).__name__}: {exc}"
            s.add(TaskEvent(task_id=task_id, type="failed", payload={"error": t.error}))
            s.commit()
        raise
