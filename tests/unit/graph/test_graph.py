"""Graph behaviour with FakeLLM and fake tools; no network, DB or Redis server."""

from __future__ import annotations

from typing import Any

import fakeredis
import pytest
from langgraph.checkpoint.memory import MemorySaver
from tests.helpers import Behavior, fake_llm, make_registry, plan_json, step

from conductor.agents.reviewer import Reviewer
from conductor.agents.specialists import build_specialists
from conductor.agents.supervisor import Supervisor
from conductor.config import load_agents_config, load_policies
from conductor.graph.builder import GraphDeps, build_graph
from conductor.memory.working import WorkingMemory


def run_graph(b: Behavior, barrier: int = 0, wm: WorkingMemory | None = None) -> Any:
    llm = fake_llm(b)
    policy = load_policies().escalation
    deps = GraphDeps(
        supervisor=Supervisor(llm),
        specialists=build_specialists(llm, make_registry(barrier), load_agents_config()),
        reviewer=Reviewer(llm, policy.min_reviewer_score),
        policy=policy,
        wm=wm,
    )
    graph = build_graph(deps, MemorySaver())
    return graph.ainvoke(
        {"task_id": "t1", "request": {"request": "review"}},
        config={"configurable": {"thread_id": "t1"}},
    )


PLAN = plan_json(
    [
        step("s1", "code_analysis"),
        step("s2", "security"),
        step("s3", "test_execution", ["s1", "s2"]),
    ]
)


async def test_happy_path() -> None:
    out = await run_graph(Behavior(PLAN))
    assert out["status"] == "completed" and not out.get("needs_human")
    assert out["report"]["summary"] == "all good"
    assert {f["file"] for f in out["report"]["findings"]} == {"a.py", "b.py"}
    assert all(v["approved"] for v in out["verdicts"].values())


async def test_specialist_fails_then_succeeds_with_hint() -> None:
    b = Behavior(PLAN, fail_first={"s1"})
    out = await run_graph(b)
    assert out["status"] == "completed"
    assert out["fail_count"] == {"s1": 1}
    assert "different approach" in out["hints"]["s1"]
    assert b.attempts["s1"] == 2


async def test_reviewer_rejects_once_then_approves_with_feedback() -> None:
    b = Behavior(PLAN, reject_first={"s2"})
    out = await run_graph(b)
    assert out["status"] == "completed"
    assert out["reject_count"] == {"s2": 1}
    assert out["feedback"]["s2"] == "add detail"
    assert b.attempts["s2"] == 2


async def test_unsupported_citation_is_rejected_until_escalation() -> None:
    out = await run_graph(Behavior(PLAN, bad_citation={"s1"}))
    assert out["status"] == "needs_human"
    assert out["reject_count"]["s1"] == 3  # initial + 2 retries exhausted the limit
    assert "report" not in out or out["report"] is None


async def test_cyclic_plan_is_rejected_and_escalated() -> None:
    cyc = plan_json([step("s1", "code_analysis", ["s2"]), step("s2", "security", ["s1"])])
    out = await run_graph(Behavior(cyc))
    assert out["needs_human"] and "cycle" in out["escalation_reason"]
    assert out["status"] == "needs_human"


async def test_low_confidence_plan_escalates() -> None:
    out = await run_graph(Behavior(plan_json([step("s1", "code_analysis")], confidence=0.2)))
    assert out["status"] == "needs_human" and "confidence" in out["escalation_reason"]


async def test_independent_subtasks_run_concurrently() -> None:
    # Each fake tool blocks until both callers are inside; sequential execution would time out.
    plan = plan_json([step("s1", "code_analysis"), step("s2", "security")])
    out = await run_graph(Behavior(plan), barrier=2)
    assert out["status"] == "completed"


async def test_dependent_subtask_waits_for_upstream() -> None:
    plan = plan_json([step("s1", "code_analysis"), step("s2", "security", ["s1"])])
    b = Behavior(plan)
    out = await run_graph(b)
    first_wave = [e for e in out["events"] if e["type"] == "subtask_done"]
    assert [e["subtask"] for e in first_wave] == ["s1", "s2"]


async def test_working_memory_cleared_on_completion() -> None:
    wm = WorkingMemory(fakeredis.FakeRedis(), ttl_seconds=60)
    r = wm._r  # noqa: SLF001
    out = await run_graph(Behavior(PLAN), wm=wm)
    assert out["status"] == "completed"
    assert r.keys("wm:*") == []


async def test_working_memory_holds_state_mid_run() -> None:
    wm = WorkingMemory(fakeredis.FakeRedis(), ttl_seconds=60)
    wm.set_plan("t9", {"x": 1})
    wm.put_output("t9", "s1", {"a": 1})
    wm.log_error("t9", "boom")
    assert wm.get_plan("t9") == {"x": 1}
    assert wm.outputs("t9") == {"s1": {"a": 1}} and wm.errors("t9") == ["boom"]
    assert wm._r.ttl("wm:t9:plan") > 0  # noqa: SLF001
    wm.clear("t9")
    assert wm.get_plan("t9") is None and wm.outputs("t9") == {}


@pytest.mark.parametrize("bad", ["ghost", "self"])
async def test_invalid_dependency_ids_rejected(bad: str) -> None:
    dep = "zzz" if bad == "ghost" else "s1"
    out = await run_graph(Behavior(plan_json([step("s1", "code_analysis", [dep])])))
    assert out["status"] == "needs_human"
