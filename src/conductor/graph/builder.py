"""LangGraph state machine: intake, plan, dispatch (Send), review, synthesize, deliver.

State holds plain dicts (not Pydantic objects) so any checkpointer, including Postgres, can
serialise it. Retry and escalation limits come from config/policies.yaml.
"""

from __future__ import annotations

import asyncio
import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from conductor.agents.reviewer import Reviewer
from conductor.agents.schemas import (
    ExecutionPlan,
    ReviewVerdict,
    SpecialistResult,
    Subtask,
)
from conductor.agents.specialists.base import Specialist, SpecialistError
from conductor.agents.supervisor import PlanValidationError, Supervisor
from conductor.config import Escalation
from conductor.memory.retrieval import Retriever
from conductor.memory.working import WorkingMemory


def _merge(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


def _add_counts(a: dict[str, int] | None, b: dict[str, int] | None) -> dict[str, int]:
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = out.get(k, 0) + v
    return out


class GraphState(TypedDict, total=False):
    task_id: str
    request: dict[str, Any]
    memories: list[str]
    plan: dict[str, Any] | None
    results: Annotated[dict[str, Any], _merge]
    verdicts: Annotated[dict[str, Any], _merge]
    fail_count: Annotated[dict[str, int], _add_counts]
    reject_count: Annotated[dict[str, int], _add_counts]
    hints: Annotated[dict[str, str], _merge]
    feedback: Annotated[dict[str, str], _merge]
    events: Annotated[list[dict[str, Any]], operator.add]
    status: str
    needs_human: bool
    escalation_reason: str | None
    report: dict[str, Any] | None


@dataclass
class GraphDeps:
    supervisor: Supervisor
    specialists: dict[str, Specialist]
    reviewer: Reviewer
    policy: Escalation
    wm: WorkingMemory | None = None
    retriever: Retriever | None = None


def _ev(type_: str, **data: Any) -> dict[str, Any]:
    return {"type": type_, **data}


def _approved(state: GraphState, sid: str) -> bool:
    v = state.get("verdicts", {}).get(sid)
    r = state.get("results", {}).get(sid)
    return bool(v and r and v["approved"] and v["run_id"] == r["run_id"])


def build_graph(deps: GraphDeps, checkpointer: Any = None) -> Any:
    pol = deps.policy

    async def intake(state: GraphState) -> dict[str, Any]:
        memories = state.get("memories", [])
        if deps.retriever is not None:
            req = state["request"]
            section = deps.retriever.retrieve(
                req.get("request", ""), req.get("repo") or "", req.get("user_id", "default")
            ).format()
            memories = [section] if section else []
        return {
            "memories": memories,
            "status": "running",
            "events": [_ev("intake", task_id=state["task_id"], memories=len(memories))],
        }

    async def plan(state: GraphState) -> dict[str, Any]:
        try:
            p = await deps.supervisor.plan(state["request"], state.get("memories", []))
        except PlanValidationError as exc:
            return {
                "needs_human": True,
                "escalation_reason": f"plan invalid: {exc}",
                "events": [_ev("plan_failed", error=str(exc))],
            }
        if deps.wm:
            deps.wm.set_plan(state["task_id"], p.model_dump())
        out: dict[str, Any] = {"plan": p.model_dump(), "events": [_ev("plan", steps=len(p.steps))]}
        if p.confidence < pol.min_plan_confidence:
            out["needs_human"] = True
            out["escalation_reason"] = f"low plan confidence {p.confidence:.2f}"
        return out

    def route_work(state: GraphState) -> list[Send] | str:
        p = ExecutionPlan.model_validate(state["plan"])
        done = {s.id for s in p.steps if _approved(state, s.id)}
        if len(done) == len(p.steps):
            return "synthesize"
        sends: list[Send] = []
        exhausted = False
        for s in p.steps:
            if s.id in done:
                continue
            if (
                state.get("fail_count", {}).get(s.id, 0) > pol.max_specialist_retries
                or state.get("reject_count", {}).get(s.id, 0) > pol.max_reviewer_rejections
            ):
                exhausted = True
                continue
            if any(d not in done for d in s.depends_on):
                continue
            upstream = {d: state["results"][d]["output"] for d in s.depends_on}
            sends.append(
                Send(
                    "run_subtask",
                    {
                        "task_id": state["task_id"],
                        "request": state["request"],
                        "subtask": s.model_dump(),
                        "upstream": upstream,
                        "hint": state.get("hints", {}).get(s.id),
                        "feedback": state.get("feedback", {}).get(s.id),
                    },
                )
            )
        if exhausted or not sends:
            return "escalate"
        return sends

    def route_after_plan(state: GraphState) -> list[Send] | str:
        if state.get("needs_human"):
            return "escalate"
        return route_work(state)

    async def run_subtask(payload: dict[str, Any]) -> dict[str, Any]:
        sub = Subtask.model_validate(payload["subtask"])
        tid = payload["task_id"]
        try:
            res = await deps.specialists[sub.specialist].run(
                sub, payload["request"], payload["upstream"], payload["hint"], payload["feedback"]
            )
        except SpecialistError as exc:
            if deps.wm:
                deps.wm.log_error(tid, f"{sub.id}: {exc}")
            return {
                "fail_count": {sub.id: 1},
                "hints": {
                    sub.id: f"Your previous attempt failed ({exc}). "
                    "Take a different approach: other tools, a narrower scope, simpler output."
                },
                "events": [_ev("subtask_failed", subtask=sub.id, error=str(exc))],
            }
        if deps.wm:
            deps.wm.put_output(tid, sub.id, res.output)
        return {
            "results": {sub.id: res.model_dump()},
            "events": [_ev("subtask_done", subtask=sub.id)],
        }

    async def review(state: GraphState) -> dict[str, Any]:
        p = ExecutionPlan.model_validate(state["plan"])
        by_id = {s.id: s for s in p.steps}
        pending = [
            r
            for sid, r in state.get("results", {}).items()
            if state.get("verdicts", {}).get(sid, {}).get("run_id") != r["run_id"]
        ]
        verdicts: list[ReviewVerdict] = await asyncio.gather(
            *[
                deps.reviewer.review(by_id[r["subtask_id"]], SpecialistResult.model_validate(r))
                for r in pending
            ]
        )
        out: dict[str, Any] = {"verdicts": {}, "reject_count": {}, "feedback": {}, "events": []}
        for v in verdicts:
            out["verdicts"][v.subtask_id] = v.model_dump()
            out["events"].append(_ev("review", subtask=v.subtask_id, approved=v.approved))
            if not v.approved:
                out["reject_count"][v.subtask_id] = 1
                out["feedback"][v.subtask_id] = v.feedback
        return out

    async def escalate(state: GraphState) -> dict[str, Any]:
        # Stub: real human-in-the-loop interrupt arrives in the HITL phase.
        reason = state.get("escalation_reason") or "retries exhausted"
        return {
            "needs_human": True,
            "escalation_reason": reason,
            "status": "needs_human",
            "events": [_ev("escalated", reason=reason)],
        }

    async def synthesize(state: GraphState) -> dict[str, Any]:
        results = {k: SpecialistResult.model_validate(v) for k, v in state["results"].items()}
        verdicts = {k: ReviewVerdict.model_validate(v) for k, v in state["verdicts"].items()}
        report = await deps.supervisor.synthesize(results, verdicts)
        return {"report": report.model_dump(), "events": [_ev("synthesized")]}

    async def deliver(state: GraphState) -> dict[str, Any]:
        if deps.wm:
            deps.wm.clear(state["task_id"])
        status = "needs_human" if state.get("needs_human") else "completed"
        return {"status": status, "events": [_ev("delivered", status=status)]}

    g = StateGraph(GraphState)
    g.add_node("intake", intake)
    g.add_node("plan", plan)
    g.add_node("run_subtask", run_subtask)  # type: ignore[arg-type]
    g.add_node("review", review)
    g.add_node("escalate", escalate)
    g.add_node("synthesize", synthesize)
    g.add_node("deliver", deliver)
    g.add_edge(START, "intake")
    g.add_edge("intake", "plan")
    g.add_conditional_edges("plan", route_after_plan, ["run_subtask", "escalate", "synthesize"])
    g.add_edge("run_subtask", "review")
    g.add_conditional_edges("review", route_work, ["run_subtask", "escalate", "synthesize"])
    g.add_edge("escalate", "deliver")
    g.add_edge("synthesize", "deliver")
    g.add_edge("deliver", END)
    return g.compile(checkpointer=checkpointer)
