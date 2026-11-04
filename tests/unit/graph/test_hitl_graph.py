"""Human gates inside the graph: pause, decide, resume from the checkpoint."""

from __future__ import annotations

import json
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from tests.helpers import Behavior, make_graph, plan_json, step

PLAN = plan_json([step("s1", "code_analysis"), step("s2", "security")])
CFG = {"configurable": {"thread_id": "t1"}}
FIX = {"path": "a.py", "content": "x = 1\n", "message": "fix a"}
PR = {"request": "review PR 5", "repo": "o/r", "pr_number": 5, "head_ref": "feat"}


async def start(graph: Any, request: dict[str, Any]) -> dict[str, Any]:
    return await graph.ainvoke({"task_id": "t1", "request": request}, CFG)  # type: ignore[no-any-return]


async def decide(graph: Any, **decision: Any) -> dict[str, Any]:
    return await graph.ainvoke(Command(resume=decision), CFG)  # type: ignore[no-any-return]


def ctx(out: dict[str, Any]) -> dict[str, Any]:
    return out["__interrupt__"][0].value  # type: ignore[no-any-return]


async def test_approve_plan_pauses_before_any_work() -> None:
    b = Behavior(PLAN)
    g = make_graph(b)
    out = await start(g, {"request": "r", "labels": ["production"]})
    c = ctx(out)
    assert c["kind"] == "plan" and c["level"] == "approve_plan"
    assert c["plan"]["steps"][0]["id"] == "s1" and c["proposed_action"]["plan"] == c["plan"]
    assert b.attempts == {}  # no specialist has run yet
    done = await decide(g, type="approve")
    assert done["status"] == "completed" and set(b.attempts) == {"s1", "s2"}


async def test_user_requested_review_gates_the_plan() -> None:
    out = await start(make_graph(Behavior(PLAN)), {"request": "r", "review_required": True})
    assert ctx(out)["triggers"][0]["name"] == "user_requested"


async def test_modify_replaces_the_plan() -> None:
    b = Behavior(plan_json([step("s1", "code_analysis")], confidence=0.2))
    g = make_graph(b)
    c = ctx(await start(g, {"request": "r"}))
    assert c["triggers"][0]["name"] == "low_plan_confidence"
    edited = json.loads(plan_json([step("x1", "security")]))
    done = await decide(g, type="modify", edited=edited)
    assert done["status"] == "completed"
    assert set(done["results"]) == {"x1"} and set(b.attempts) == {"x1"}


async def test_modify_with_invalid_plan_asks_again() -> None:
    g = make_graph(Behavior(plan_json([step("s1", "code_analysis")], confidence=0.2)))
    await start(g, {"request": "r"})
    cyc = json.loads(plan_json([step("a", "security", ["b"]), step("b", "security", ["a"])]))
    again = await decide(g, type="modify", edited=cyc)
    assert ctx(again)["kind"] == "plan"  # still waiting for a valid plan


async def test_reject_replans_with_the_humans_feedback() -> None:
    b = Behavior(PLAN)
    g = make_graph(b)
    await start(g, {"request": "r", "review_required": True})
    again = await decide(g, type="reject", reason="also check the migrations")
    assert again["human_rejections"] == 1 and ctx(again)["kind"] == "plan"
    planner = [json.loads(m[1]["content"]) for r, m in g.llm.calls if r == "planner"]
    assert planner[0]["human_feedback"] == []
    assert planner[1]["human_feedback"] == ["also check the migrations"]
    assert b.attempts == {}  # nothing ran on the rejected plan
    assert (await decide(g, type="approve"))["status"] == "completed"


async def test_too_many_rejections_end_as_needs_human() -> None:
    g = make_graph(Behavior(PLAN))
    await start(g, {"request": "r", "review_required": True})
    out: dict[str, Any] = {}
    for _ in range(4):  # max_human_rejections is 3
        out = await decide(g, type="reject", reason="no")
    assert out["status"] == "needs_human" and "__interrupt__" not in out


async def test_take_over_at_repeated_failure_stands_agents_down() -> None:
    b = Behavior(PLAN, always_fail={"s1"})
    g = make_graph(b)
    out = await start(g, {"request": "r"})
    c = ctx(out)
    assert c["kind"] == "failure" and c["level"] == "take_over"
    assert c["allowed_decisions"] == ["take_over", "reject"]
    assert c["current_step"]["id"] == "s1"
    assert c["completed_steps"][0]["id"] == "s2"
    ran = dict(b.attempts)
    done = await decide(g, type="take_over", output={"summary": "I fixed it by hand"})
    assert done["status"] == "taken_over" and done["taken_over"]
    assert done["report"]["human_output"]["summary"] == "I fixed it by hand"
    assert b.attempts == ran  # no agent work after the hand-over
    assert done["human_feedback"][-1]["decision"] == "take_over"


async def test_approve_at_failure_gate_grants_another_round() -> None:
    b = Behavior(PLAN, bad_citation={"s1"})
    g = make_graph(b)
    first = ctx(await start(g, {"request": "r"}))
    assert first["kind"] == "failure" and first["level"] == "approve_action"
    assert "approve" in first["allowed_decisions"]
    tried = b.attempts["s1"]
    second = await decide(g, type="approve")
    assert b.attempts["s1"] > tried  # the failing subtask got a fresh round
    assert ctx(second)["kind"] == "failure"


async def test_merge_request_goes_straight_to_a_human() -> None:
    b = Behavior(PLAN)
    g = make_graph(b)
    out = await start(g, {"request": "please merge PR 5"})
    c = ctx(out)
    assert c["kind"] == "takeover" and c["level"] == "take_over"
    assert not [1 for r, _ in g.llm.calls if r == "planner"] and b.attempts == {}
    done = await decide(g, type="take_over", output={"summary": "merged by me"})
    assert done["status"] == "taken_over" and b.attempts == {}


async def test_notify_proceeds_but_sensitive_push_waits_for_approval() -> None:
    b = Behavior(plan_json([step("s1", "code_analysis")]), fixes=[FIX])
    g = make_graph(b)
    out = await start(g, PR)
    c = ctx(out)
    assert c["kind"] == "action" and c["level"] == "approve_action"
    assert c["proposed_action"]["tool"] == "push_fix_commit" and c["proposed_action"]["reasoning"]
    # the comment (notify) already went out; the push has not
    assert [n for n, _ in g.registry.calls] == ["post_review_comment"]
    assert any(e["type"] == "notified" for e in out["events"])
    done = await decide(g, type="approve")
    assert [n for n, _ in g.registry.calls] == ["post_review_comment", "push_fix_commit"]
    assert done["status"] == "completed"


async def test_modify_applies_the_edited_action() -> None:
    g = make_graph(Behavior(plan_json([step("s1", "code_analysis")]), fixes=[FIX]))
    await start(g, PR)
    await decide(g, type="modify", edited={"content": "y = 2\n"})
    name, args = g.registry.calls[-1]
    assert name == "push_fix_commit" and args["content"] == "y = 2\n" and args["path"] == "a.py"


async def test_reject_action_replans_and_does_not_push() -> None:
    g = make_graph(Behavior(plan_json([step("s1", "code_analysis")]), fixes=[FIX]))
    await start(g, PR)
    out = await decide(g, type="reject", reason="do not touch a.py")
    assert "push_fix_commit" not in [n for n, _ in g.registry.calls]
    assert out["human_rejections"] == 1 and out["plan_feedback"] == ["do not touch a.py"]


async def test_take_over_at_action_gate_skips_the_push() -> None:
    g = make_graph(Behavior(plan_json([step("s1", "code_analysis")]), fixes=[FIX]))
    await start(g, PR)
    done = await decide(g, type="take_over", output={"summary": "I will push it"})
    assert done["status"] == "taken_over"
    assert "push_fix_commit" not in [n for n, _ in g.registry.calls]


async def test_production_repo_comment_is_gated_after_plan_approval() -> None:
    g = make_graph(Behavior(plan_json([step("s1", "code_analysis")])))
    request = {**PR, "labels": ["production"]}
    assert ctx(await start(g, request))["kind"] == "plan"
    out = await decide(g, type="approve")
    c = ctx(out)
    assert c["kind"] == "action" and c["proposed_action"]["tool"] == "post_review_comment"
    assert g.registry.calls == []


async def test_approval_resumes_after_worker_restart() -> None:
    saver = MemorySaver()  # stands in for the Postgres checkpointer: outlives any worker
    first = make_graph(Behavior(PLAN), saver=saver)
    await start(first, {"request": "r", "review_required": True})
    del first  # the worker that asked is gone

    b2 = Behavior(PLAN)
    second = make_graph(b2, saver=saver)  # brand-new graph, deps and LLM; same checkpoints
    done = await decide(second, type="approve")
    assert done["status"] == "completed" and set(b2.attempts) == {"s1", "s2"}
    assert done["report"]["summary"] == "all good"
