"""Runs one task through the graph and persists progress as it goes.

A run can pause at a human gate (status awaiting_approval) and later resume from the LangGraph
checkpoint in a different process: execute_task starts it, resume_task continues it.
DB writes are small synchronous calls; the graph is the only long-running part.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy.orm import Session, sessionmaker

from conductor.agents.reviewer import Reviewer
from conductor.agents.specialists import build_specialists
from conductor.agents.supervisor import Supervisor
from conductor.config import Price, load_policies, load_prices
from conductor.db.models import Approval, LLMCall, PlanStep, Task, TaskEvent
from conductor.graph.builder import GraphDeps, build_graph
from conductor.hitl.queue import ApprovalQueue
from conductor.llm.recording import RecordingLLM
from conductor.llm.types import LLM, LLMResult
from conductor.memory.longterm import LongTermStore
from conductor.memory.manager import MemoryManager
from conductor.memory.retrieval import Retriever
from conductor.memory.working import WorkingMemory
from conductor.tools.registry import ToolRegistry

_PROCESS_SAVER = MemorySaver()  # dev default: survives across runs within one process


@asynccontextmanager
async def memory_checkpointer() -> Any:
    yield _PROCESS_SAVER


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
    memory_store: LongTermStore | None = None
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
    rows = {r.step_id: r for r in s.query(PlanStep).filter(PlanStep.task_id == task_id).all()}
    wanted = {st["id"] for st in plan["steps"]} if plan else set()
    for sid, row in rows.items():
        if sid not in wanted:  # the plan was replaced (human reject / modify)
            s.delete(row)
    for step in plan["steps"] if plan else []:
        found = rows.get(step["id"])
        row = found or PlanStep(
            task_id=task_id,
            step_id=step["id"],
            description=step["description"],
            specialist=step["specialist"],
            depends_on=step["depends_on"],
        )
        if found is None:
            s.add(row)
        row.status = _step_status(state, step["id"])


def _graph_event_count(s: Session, task_id: str) -> int:
    return sum(
        1
        for e in s.query(TaskEvent).filter(TaskEvent.task_id == task_id).all()
        if e.payload.get("src") == "graph"
    )


async def execute_task(task_id: str, rt: Runtime) -> None:
    with rt.session_factory() as s:
        task = s.get(Task, task_id)
        assert task is not None
        request = dict(task.request)
    await _drive(task_id, rt, {"task_id": task_id, "request": request})


async def resume_task(task_id: str, approval_id: str, rt: Runtime) -> None:
    """Continue a paused run with the human's decision, from the saved checkpoint."""
    with rt.session_factory() as s:
        row = s.get(Approval, approval_id)
        assert row is not None and row.decision is not None
        decision = dict(row.decision)
    await _drive(task_id, rt, Command(resume=decision))
    ApprovalQueue(rt.session_factory).resolve(approval_id)


async def _drive(task_id: str, rt: Runtime, graph_input: Any) -> None:
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
    policy = load_policies()
    deps = GraphDeps(
        supervisor=Supervisor(llm),
        specialists=build_specialists(llm, rt.registry),
        reviewer=Reviewer(llm, policy.escalation.min_reviewer_score),
        policy_config=policy,
        wm=rt.wm,
        retriever=Retriever(rt.memory_store) if rt.memory_store else None,
        registry=rt.registry,
        memory_store=rt.memory_store,
    )
    queue = ApprovalQueue(sf)
    final: dict[str, Any] = {}
    interrupted = False
    try:
        async with rt.checkpointer() as cp:
            graph = build_graph(deps, cp)
            with sf() as s:
                persisted = _graph_event_count(s, task_id)
            async for chunk in graph.astream(
                graph_input,
                config={"configurable": {"thread_id": task_id}},
                stream_mode="values",
            ):
                pending = chunk.get("__interrupt__")
                state = {k: v for k, v in chunk.items() if k != "__interrupt__"}
                final = state
                with sf() as s:
                    events = state.get("events", [])
                    for ev in events[persisted:]:
                        s.add(
                            TaskEvent(
                                task_id=task_id, type=ev["type"], payload={**ev, "src": "graph"}
                            )
                        )
                    persisted = len(events)
                    _sync_steps(s, task_id, state)
                    s.commit()
                for intr in pending or []:
                    interrupted = True
                    ctx = intr.value
                    if queue.create(task_id, intr.id, ctx):
                        with sf() as s:
                            s.add(
                                TaskEvent(
                                    task_id=task_id,
                                    type="approval_requested",
                                    payload={
                                        "approval_id": intr.id,
                                        "kind": ctx["kind"],
                                        "level": ctx["level"],
                                    },
                                )
                            )
                            s.commit()
        with sf() as s:
            t = s.get(Task, task_id)
            assert t is not None
            if interrupted:
                t.status = "awaiting_approval"
            else:
                t.status = final.get("status", "completed")
                t.needs_human = bool(final.get("needs_human"))
                t.escalation_reason = final.get("escalation_reason")
                t.report = final.get("report")
            s.commit()
        if not interrupted and rt.memory_store is not None:
            await _remember(sf, task_id, final, request, MemoryManager(rt.memory_store, llm))
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


async def _remember(
    sf: sessionmaker[Session],
    task_id: str,
    final: dict[str, Any],
    request: dict[str, Any],
    manager: MemoryManager,
) -> None:
    """Post-task extraction. A memory failure must never turn a finished review into a failure."""
    try:
        n = await manager.extract_and_store(
            final, request.get("user_id", "default"), request.get("repo") or ""
        )
        event = TaskEvent(task_id=task_id, type="memory_saved", payload={"items": n})
    except Exception as exc:
        event = TaskEvent(task_id=task_id, type="memory_failed", payload={"error": str(exc)})
    with sf() as s:
        s.add(event)
        s.commit()
