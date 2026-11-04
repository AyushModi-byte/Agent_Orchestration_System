"""End-to-end behaviour of the whole system on FakeLLM: real graph, real DB, real tracing, real
memory store; only the model and the tools' outside world are scripted."""

from __future__ import annotations

import json
from typing import Any

import pytest
from demo.runtime import build_demo_runtime
from demo.scenario import approve_all, run_scenario

from conductor.db.models import Approval, PlanStep, SpanRow, Task, ToolCall
from conductor.hitl.queue import ApprovalQueue, Decision
from conductor.llm.fake import FakeLLM
from conductor.llm.types import Message
from conductor.tools.registry import PermissionDenied
from conductor.worker.runner import resume_task
from tests.helpers import Behavior, plan_json, step
from tests.obs_helpers import PLAN, Env, make_env, run_task, teardown


@pytest.fixture
def env():  # type: ignore[no-untyped-def]
    e = make_env()
    yield e
    teardown()


def status(env: Env, tid: str) -> str:
    with env.sf() as s:
        return s.get(Task, tid).status  # type: ignore[union-attr]


def approvals(env: Env, tid: str) -> list[Approval]:
    with env.sf() as s:
        return list(s.query(Approval).filter_by(task_id=tid))


def spans(env: Env, tid: str, name: str) -> list[SpanRow]:
    with env.sf() as s:
        return list(s.query(SpanRow).filter_by(task_id=tid, name=name))


# -- plans are valid ---------------------------------------------------------------------------
async def test_plans_are_valid_dags_with_known_specialists() -> None:
    rt = build_demo_runtime(seed=False)
    result = await run_scenario(rt, repo="acme/shop", user="demo")
    for pr in (result.pr1, result.pr2):
        with rt.session_factory() as s:
            steps = list(s.query(PlanStep).filter_by(task_id=pr.task_id))
        ids = {x.step_id for x in steps}
        assert ids and all(set(x.depends_on) <= ids for x in steps)  # no dangling dependency
        assert all(
            x.specialist in {"code_analysis", "security", "test_execution", "fix_writer"}
            for x in steps
        )
        done: set[str] = set()
        remaining = list(steps)
        while remaining:  # a topological order exists iff the plan is acyclic
            ready = [x for x in remaining if set(x.depends_on) <= done]
            assert ready, "plan has a cycle"
            done |= {x.step_id for x in ready}
            remaining = [x for x in remaining if x not in ready]
        assert all(x.status == "approved" for x in steps)


async def test_an_invalid_plan_is_never_executed(env: Env) -> None:
    cyclic = plan_json([step("s1", "code_analysis", ["s2"]), step("s2", "security", ["s1"])])
    env.runtime.llm = FakeLLM(responder=Behavior(cyclic))
    tid = await run_task(env)
    assert status(env, tid) == "awaiting_approval"  # a human is asked instead of running it
    (appr,) = approvals(env, tid)
    assert appr.kind == "plan" and appr.context["plan_error"]
    with env.sf() as s:
        assert s.query(ToolCall).filter_by(task_id=tid).count() == 0


# -- specialists use their tools correctly -----------------------------------------------------
ALLOWED = {
    "code_analysis": {"list_pr_files", "get_file", "get_pr_diff", "run_ruff"},
    "security": {"get_file", "run_bandit", "audit_requirements"},
    "test_execution": {"run_tests"},
    "fix_writer": {"get_file", "run_ruff", "run_tests", "push_fix_commit"},
    "supervisor": {"post_review_comment"},
}


async def test_specialists_only_call_their_own_tools_with_valid_results() -> None:
    rt = build_demo_runtime(seed=False)
    result = await run_scenario(rt, repo="acme/shop", user="demo")
    with rt.session_factory() as s:
        calls = list(s.query(ToolCall).filter_by(task_id=result.pr2.task_id))
    assert {c.agent for c in calls} - {"supervisor"} == {
        "code_analysis",
        "security",
        "test_execution",
        "fix_writer",
    }
    for c in calls:
        assert c.tool in ALLOWED[c.agent], (c.agent, c.tool)
        assert c.success and c.outputs and json.loads(c.outputs)
    # the registry itself refuses a tool outside an agent's permission, whatever the model asks
    with pytest.raises(PermissionDenied):
        await rt.registry.invoke("security", "run_tests", {})


# -- the reviewer catches deliberately bad output ---------------------------------------------
async def test_reviewer_rejects_a_finding_that_no_tool_reported() -> None:
    e = make_env(Behavior(PLAN, bad_citation={"s1"}))  # s1 cites line 999, which ruff never said
    try:
        tid = await run_task(e)
        verdicts = [
            s for s in spans(e, tid, "reviewer.evaluate") if s.attributes.get("approved") is False
        ]
        assert verdicts, "the fabricated citation went unnoticed"
        with e.sf() as s:
            report = s.get(Task, tid).report or {}  # type: ignore[union-attr]
        assert all(f["line"] != 999 for f in report.get("findings", []))  # never delivered
    finally:
        teardown()


async def test_reviewer_rejects_a_fix_whose_tests_fail_then_accepts_the_retry() -> None:
    rt = build_demo_runtime(seed=False)
    result = await run_scenario(rt, repo="acme/shop", user="demo")
    assert result.pr2.reviewer_rejections == 1 and result.pr2.status == "completed"
    assert result.pr1.reviewer_rejections == 0


# -- memory retrieval improves planning on a repeat task ---------------------------------------
class Learner:
    """Plans confidently only when memory says it has done this before."""

    def __init__(self) -> None:
        self.inner = Behavior(PLAN)

    def __call__(self, role: str, messages: list[Message]) -> str:
        if role == "cheap":
            return json.dumps(
                {
                    "what_asked": "review auth module",
                    "plan_summary": "lint, scan and test in dependency order",
                    "worked": ["scan with bandit first"],
                }
            )
        if role == "planner":
            try:
                last = json.loads(messages[-1]["content"])
            except ValueError:
                return PLAN
            if "request" in last:
                plan = json.loads(PLAN)
                plan["confidence"] = 0.9 if last.get("memories") else 0.4
                return json.dumps(plan)
        return self.inner(role, messages)


async def test_memory_makes_the_second_plan_confident_enough_to_skip_the_human(env: Env) -> None:
    env.runtime.llm = FakeLLM(responder=Learner())
    first = await run_task(env)
    assert status(env, first) == "awaiting_approval"  # no memory, low confidence: ask a human
    (appr,) = approvals(env, first)
    assert [t["name"] for t in appr.context["triggers"]] == ["low_plan_confidence"]
    ApprovalQueue(env.sf).decide(appr.id, Decision(type="approve"))
    await resume_task(first, appr.id, env.runtime)
    assert status(env, first) == "completed"

    second = await run_task(env)  # the same request again
    assert status(env, second) == "completed" and not approvals(env, second)
    (recall,) = spans(env, second, "memory.retrieve")
    assert recall.attributes["count"] >= 1
    (plan_span,) = spans(env, second, "supervisor.plan")
    assert plan_span.attributes["memories"] >= 1


# -- escalation fires at the right moments -----------------------------------------------------
async def test_a_clean_run_never_bothers_a_human(env: Env) -> None:
    tid = await run_task(env)
    assert status(env, tid) == "completed" and not approvals(env, tid)


@pytest.mark.parametrize(
    ("request_fields", "kind", "trigger"),
    [
        ({"review_required": True}, "plan", "user_requested"),
        ({"labels": ["production"]}, "plan", "production_repo"),
    ],
)
async def test_plan_gate_triggers(
    env: Env, request_fields: dict[str, Any], kind: str, trigger: str
) -> None:
    tid = await run_task(env, **request_fields)
    (appr,) = approvals(env, tid)
    assert appr.kind == kind
    assert trigger in [t["name"] for t in appr.context["triggers"]] or appr.level == "approve_plan"


async def test_a_merge_request_is_a_human_only_take_over(env: Env) -> None:
    tid = await run_task(env, request="please merge this PR to main")
    (appr,) = approvals(env, tid)
    assert appr.level == "take_over"
    assert "take_over" in appr.context["allowed_decisions"]
    assert "approve" not in appr.context["allowed_decisions"]  # agents cannot be told to merge


async def test_pushing_a_fix_waits_for_approval() -> None:
    rt = build_demo_runtime(seed=False)
    seen: list[dict[str, Any]] = []

    def decide(ctx: dict[str, Any]) -> Decision:
        seen.append(ctx)
        return approve_all(ctx)

    await run_scenario(rt, repo="acme/shop", user="demo", decide=decide)
    assert [c["kind"] for c in seen] == ["action"]
    assert seen[0]["proposed_action"]["tool"] == "push_fix_commit"
    assert seen[0]["level"] == "approve_action"
    assert seen[0]["memories"] and seen[0]["similar_decisions"] is not None


async def test_repeated_specialist_failure_escalates(env: Env) -> None:
    env.runtime.llm = FakeLLM(responder=Behavior(PLAN, always_fail={"s1"}))
    tid = await run_task(env)
    assert status(env, tid) in {"awaiting_approval", "needs_human"}
    assert approvals(env, tid) or status(env, tid) == "needs_human"


# -- the system recovers from an agent failure -------------------------------------------------
async def test_a_specialist_that_fails_once_is_retried_and_the_run_completes() -> None:
    e = make_env(Behavior(PLAN, fail_first={"s1"}))
    try:
        tid = await run_task(e)
        assert status(e, tid) == "completed" and not approvals(e, tid)
        assert e.behavior.attempts["s1"] == 2  # the junk answer, then a good one
        assert len(spans(e, tid, "specialist.run")) >= 4  # s1 ran twice
        with e.sf() as s:
            steps = {x.step_id: x.status for x in s.query(PlanStep).filter_by(task_id=tid)}
        assert set(steps.values()) == {"approved"}
    finally:
        teardown()
