"""Tracing and cost: the span tree mirrors the executed graph; totals match the llm_calls rows."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.obs_helpers import PLAN, make_env, run_task, teardown

from conductor.api.app import create_app
from conductor.db.models import Approval, LLMCall, SpanRow, ToolCall
from conductor.hitl.queue import ApprovalQueue, Decision
from conductor.obs.cost import task_cost
from conductor.obs.exporter import DbSpanExporter
from conductor.obs.tracing import bind, get_tracer
from conductor.tools.registry import PermissionDenied
from conductor.worker.runner import resume_task


@pytest.fixture
def env():  # type: ignore[no-untyped-def]
    e = make_env()
    e.store.add(
        "task_memories",
        "review auth module. Worked: scan with bandit first",
        user_id="u1",
        repo="o/r",
        type="worked",
    )
    yield e
    teardown()


def flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for n in nodes:
        out.append(n)
        out.extend(flatten(n["children"]))
    return out


def names(nodes: list[dict[str, Any]]) -> list[str]:
    return [n["name"] for n in nodes]


async def test_trace_tree_matches_executed_graph(env) -> None:  # type: ignore[no-untyped-def]
    tid = await run_task(env)
    client = TestClient(create_app(env.sf, lambda t: None))
    tree = client.get(f"/traces/{tid}").json()
    assert tree["status"] == "ok" and tree["span_count"] > 10

    (root,) = tree["roots"]
    assert root["name"] == "task.run" and root["attributes"]["task_status"] == "completed"
    top = names(root["children"])
    # intake -> plan -> three specialists and their reviews -> synthesize -> memory extraction
    assert top[:2] == ["memory.retrieve", "supervisor.plan"]
    assert top.count("specialist.run") == 3 and top.count("reviewer.evaluate") == 3
    assert "supervisor.synthesize" in top and "memory.extract" in top

    by_task = {
        n["attributes"]["subtask_id"]: n for n in root["children"] if n["name"] == "specialist.run"
    }
    assert {k: v["agent"] for k, v in by_task.items()} == {
        "s1": "code_analysis",
        "s2": "security",
        "s3": "test_execution",
    }
    expected_tool = {"s1": "run_ruff", "s2": "run_bandit", "s3": "run_tests"}
    for sid, node in by_task.items():
        kids = names(node["children"])
        assert kids.count("llm.call") == 2  # chose a tool, then answered
        (tool,) = [c for c in node["children"] if c["name"] == "tool.call"]
        assert tool["attributes"]["tool"] == expected_tool[sid]
        assert tool["agent"] == node["agent"]
    for n in root["children"]:
        if n["name"] in ("reviewer.evaluate", "supervisor.plan", "supervisor.synthesize"):
            assert names(n["children"]) == ["llm.call"], n["name"]

    # s3 depends on s1 and s2, so its work must start after both have been reviewed
    order = [(n["name"], n["attributes"].get("subtask_id")) for n in root["children"]]
    last_review_of_deps = max(order.index(("reviewer.evaluate", s)) for s in ("s1", "s2"))
    assert order.index(("specialist.run", "s3")) > last_review_of_deps


async def test_spans_carry_required_attributes(env) -> None:  # type: ignore[no-untyped-def]
    tid = await run_task(env)
    with env.sf() as s:
        spans = s.query(SpanRow).filter_by(task_id=tid).all()
        llm_rows = {r.id: r for r in s.query(LLMCall).filter_by(task_id=tid)}
    assert spans and all(sp.task_id == tid and sp.status == "ok" for sp in spans)
    assert all(sp.duration_ms >= 0 for sp in spans)

    llm_spans = [sp for sp in spans if sp.name == "llm.call"]
    assert llm_spans
    for sp in llm_spans:
        a = sp.attributes
        assert sp.agent and sp.role and sp.model.startswith("fake/")
        assert a["tokens_in"] > 0 and a["tokens_out"] > 0 and a["cost_usd"] > 0
        assert a["latency_ms"] >= 0 and a["status"] == "ok"
        row = llm_rows[sp.llm_call_id]  # the span points at the full prompt/response row
        assert row.agent == sp.agent and row.model == sp.model and row.response
        assert row.request and "prompt" not in a  # spans stay small: no prompt text in them

    for sp in (x for x in spans if x.name == "tool.call"):
        assert sp.agent and sp.attributes["tool"] and sp.attributes["latency_ms"] >= 0

    (mem,) = [sp for sp in spans if sp.name == "memory.retrieve"]
    assert len(mem.attributes["memory_ids"]) == 1 and mem.attributes["count"] == 1
    for sp in (x for x in spans if x.name == "reviewer.evaluate"):
        assert sp.agent == "reviewer" and "score" in sp.attributes and sp.attributes["approved"]


async def test_cost_totals_match_llm_calls(env) -> None:  # type: ignore[no-untyped-def]
    tid = await run_task(env)
    with env.sf() as s:
        rows = s.query(LLMCall).filter_by(task_id=tid).all()
        tools = s.query(ToolCall).filter_by(task_id=tid).count()
        span_cost = sum(
            r.cost_usd for r in s.query(SpanRow).filter_by(task_id=tid, name="llm.call")
        )
    c = task_cost(env.sf, tid)
    assert c.llm_calls == len(rows) and c.tool_calls == tools == 3
    assert c.tokens_in == sum(r.tokens_in for r in rows) > 0
    assert c.tokens_out == sum(r.tokens_out for r in rows)
    assert c.cost_usd > 0
    assert c.cost_usd == pytest.approx(sum(r.cost_usd for r in rows))
    assert c.cost_usd == pytest.approx(span_cost)  # spans and rows agree
    assert sum(b.cost_usd for b in c.by_agent.values()) == pytest.approx(c.cost_usd)
    assert sum(b.cost_usd for b in c.by_model.values()) == pytest.approx(c.cost_usd)
    assert {"supervisor", "code_analysis", "security", "test_execution", "reviewer"} <= set(
        c.by_agent
    )
    assert c.by_agent["supervisor"].calls == 2  # plan + synthesize
    assert c.tool_calls_by_tool == {"run_ruff": 1, "run_bandit": 1, "run_tests": 1}
    assert c.wall_clock_seconds > 0 and c.human_review_seconds == 0

    tree = TestClient(create_app(env.sf, lambda t: None)).get(f"/traces/{tid}").json()
    assert tree["total_cost_usd"] == pytest.approx(c.cost_usd)


async def test_metrics_endpoint_aggregates(env) -> None:  # type: ignore[no-untyped-def]
    await run_task(env)
    await run_task(env, pr_number=7)
    await run_task(env, review_required=True)  # pauses at a human gate
    m = TestClient(create_app(env.sf, lambda t: None)).get("/metrics/cost").json()
    types = {t["task_type"]: t for t in m["per_task_type"]}
    assert types["general"]["tasks"] == 2 and types["pr_review"]["tasks"] == 1
    assert types["pr_review"]["cost_usd"] > 0
    agents = [a["agent"] for a in m["top_agents"]]
    costs = [a["cost_usd"] for a in m["top_agents"]]
    assert costs == sorted(costs, reverse=True) and "reviewer" in agents
    usage = {u["tool"]: u for u in m["tool_usage"]}
    assert usage["run_ruff"]["calls"] >= 2 and usage["run_ruff"]["errors"] == 0
    (day,) = m["escalation_trend"]
    assert day["tasks"] == 3 and day["escalated"] == 1 and day["rate"] == pytest.approx(1 / 3)


async def test_human_review_time_and_escalation_spans(env) -> None:  # type: ignore[no-untyped-def]
    tid = await run_task(env, review_required=True)
    with env.sf() as s:
        (appr,) = s.query(Approval).filter_by(task_id=tid).all()
        approval_id = appr.id
    ApprovalQueue(env.sf).decide(approval_id, Decision(type="approve"))
    await resume_task(tid, approval_id, env.runtime)
    with env.sf() as s:
        esc = s.query(SpanRow).filter_by(task_id=tid, name="hitl.escalation").one()
        res = s.query(SpanRow).filter_by(task_id=tid, name="hitl.resolution").one()
        roots = [r.name for r in s.query(SpanRow).filter_by(task_id=tid, parent_span_id=None)]
    assert esc.attributes["kind"] == "plan" and esc.attributes["approval_id"] == approval_id
    assert esc.attributes["triggers"] == ["user_requested"]
    assert res.attributes["decision"] == "approve" and res.attributes["review_seconds"] > 0
    assert sorted(roots) == ["hitl.resolution", "task.resume", "task.run"]  # two run segments
    c = task_cost(env.sf, tid)
    assert c.human_review_seconds > 0 and c.wall_clock_seconds > 0
    assert c.by_agent["supervisor"].calls == 2


async def test_errors_are_recorded_on_the_span(env) -> None:  # type: ignore[no-untyped-def]
    with bind(task_id="t-err"):
        with pytest.raises(PermissionDenied):
            await env.registry.invoke("intruder", "run_ruff", {})
        with pytest.raises(RuntimeError), get_tracer().span("custom"):
            raise RuntimeError("boom")
    with env.sf() as s:
        tool = s.query(SpanRow).filter_by(task_id="t-err", name="tool.call").one()
        custom = s.query(SpanRow).filter_by(task_id="t-err", name="custom").one()
        row = s.query(ToolCall).filter_by(task_id="t-err").one()
    assert tool.status == "error" and "PermissionDenied" in (tool.error or "")
    assert custom.status == "error" and custom.error == "RuntimeError: boom"
    assert not row.success  # the tool_calls row agrees with the span


def test_exporter_never_raises_into_the_task() -> None:
    from opentelemetry.sdk.trace.export import SpanExportResult

    class Broken:
        def __call__(self) -> Any:
            raise RuntimeError("db down")

    assert DbSpanExporter(Broken()).export([]) is SpanExportResult.FAILURE  # type: ignore[arg-type]


def test_plan_fixture_is_the_expected_shape() -> None:
    assert '"s3"' in PLAN
