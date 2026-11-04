"""LangGraph state machine.

intake -> plan -> [gate_plan] -> dispatch (Send) -> review -> synthesize -> act -> deliver,
with `escalate` for stuck work. Gates pause via interrupt() and resume from the checkpoint.

State holds plain dicts (not Pydantic objects) so any checkpointer, including Postgres, can
serialise it. Thresholds and levels come from config/policies.yaml.
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
from conductor.config import PoliciesConfig
from conductor.graph.gates import Gates
from conductor.hitl.triggers import evaluate
from conductor.memory.longterm import LongTermStore
from conductor.memory.retrieval import Retriever
from conductor.memory.working import WorkingMemory
from conductor.tools.registry import ToolRegistry


def _merge(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """Merge dict updates from parallel branches; {"__reset__": True, ...} replaces instead."""
    if b and b.get("__reset__"):
        return {k: v for k, v in b.items() if k != "__reset__"}
    return {**(a or {}), **(b or {})}


def _add_counts(a: dict[str, int] | None, b: dict[str, int] | None) -> dict[str, int]:
    if b and b.get("__reset__"):
        return {k: v for k, v in b.items() if k != "__reset__"}
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = out.get(k, 0) + v
    return out


class GraphState(TypedDict, total=False):
    task_id: str
    request: dict[str, Any]
    memories: list[str]
    memory_refs: list[list[str]]
    plan: dict[str, Any] | None
    plan_error: str | None
    plan_feedback: Annotated[list[str], operator.add]
    results: Annotated[dict[str, Any], _merge]
    verdicts: Annotated[dict[str, Any], _merge]
    fail_count: Annotated[dict[str, int], _add_counts]
    reject_count: Annotated[dict[str, int], _add_counts]
    hints: Annotated[dict[str, str], _merge]
    feedback: Annotated[dict[str, str], _merge]
    events: Annotated[list[dict[str, Any]], operator.add]
    human_feedback: Annotated[list[dict[str, Any]], operator.add]
    human_rejections: int
    actions: list[dict[str, Any]] | None
    action_idx: int
    next_step: str
    taken_over: bool
    final_status: str | None
    status: str
    needs_human: bool
    escalation_reason: str | None
    report: dict[str, Any] | None


@dataclass
class GraphDeps:
    supervisor: Supervisor
    specialists: dict[str, Specialist]
    reviewer: Reviewer
    policy_config: PoliciesConfig
    wm: WorkingMemory | None = None
    retriever: Retriever | None = None
    registry: ToolRegistry | None = None  # for outward actions (post comment, push fix)
    memory_store: LongTermStore | None = None  # for "similar past decisions" in approvals


def _ev(type_: str, **data: Any) -> dict[str, Any]:
    return {"type": type_, **data}


def _approved(state: GraphState, sid: str) -> bool:
    v = state.get("verdicts", {}).get(sid)
    r = state.get("results", {}).get(sid)
    return bool(v and r and v["approved"] and v["run_id"] == r["run_id"])


def build_graph(deps: GraphDeps, checkpointer: Any = None) -> Any:
    pol = deps.policy_config.escalation
    gates = Gates(deps)

    async def intake(state: GraphState) -> dict[str, Any]:
        memories = state.get("memories", [])
        refs: list[list[str]] = []
        if deps.retriever is not None:
            req = state["request"]
            got = deps.retriever.retrieve(
                req.get("request", ""), req.get("repo") or "", req.get("user_id", "default")
            )
            section = got.format()
            memories = [section] if section else []
            refs = [[it.collection, it.id] for it in got.all_items()]
        return {
            "memories": memories,
            "memory_refs": refs,
            "status": "running",
            "events": [_ev("intake", task_id=state["task_id"], memories=len(memories))],
        }

    def route_after_intake(state: GraphState) -> str:
        # a merge request goes straight to a human; the system never merges
        return "escalate" if evaluate(dict(state), deps.policy_config, "intake") else "plan"

    async def plan(state: GraphState) -> dict[str, Any]:
        try:
            p = await deps.supervisor.plan(
                state["request"], state.get("memories", []), state.get("plan_feedback") or []
            )
        except PlanValidationError as exc:
            return {
                "plan": None,
                "plan_error": str(exc),
                "events": [_ev("plan_failed", error=str(exc))],
            }
        if deps.wm:
            deps.wm.set_plan(state["task_id"], p.model_dump())
        return {
            "plan": p.model_dump(),
            "plan_error": None,
            "events": [_ev("plan", steps=len(p.steps))],
        }

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
                state.get("fail_count", {}).get(s.id, 0) >= pol.repeated_failure_threshold
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
        if evaluate(dict(state), deps.policy_config, "plan"):
            return "gate_plan"
        return route_work(state)

    def route_next(state: GraphState) -> list[Send] | str:
        """After any gate: where did the human's decision send us?"""
        step = state.get("next_step", "dispatch")
        if step == "dispatch":
            return route_work(state)
        return step

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

    async def synthesize(state: GraphState) -> dict[str, Any]:
        results = {k: SpecialistResult.model_validate(v) for k, v in state["results"].items()}
        verdicts = {k: ReviewVerdict.model_validate(v) for k, v in state["verdicts"].items()}
        report = await deps.supervisor.synthesize(results, verdicts)
        return {"report": report.model_dump(), "events": [_ev("synthesized")]}

    async def deliver(state: GraphState) -> dict[str, Any]:
        if deps.wm:
            deps.wm.clear(state["task_id"])
        status = state.get("final_status") or "completed"
        return {"status": status, "events": [_ev("delivered", status=status)]}

    g = StateGraph(GraphState)
    g.add_node("intake", intake)
    g.add_node("plan", plan)
    g.add_node("gate_plan", gates.gate_plan)  # type: ignore[type-var]
    g.add_node("run_subtask", run_subtask)  # type: ignore[arg-type]
    g.add_node("review", review)
    g.add_node("escalate", gates.escalate)  # type: ignore[type-var]
    g.add_node("synthesize", synthesize)
    g.add_node("act", gates.act)  # type: ignore[type-var]
    g.add_node("deliver", deliver)
    targets = ["run_subtask", "escalate", "synthesize", "gate_plan", "plan", "deliver", "act"]
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_after_intake, ["escalate", "plan"])
    g.add_conditional_edges("plan", route_after_plan, targets)
    g.add_conditional_edges("gate_plan", route_next, targets)
    g.add_edge("run_subtask", "review")
    g.add_conditional_edges("review", route_work, ["run_subtask", "escalate", "synthesize"])
    g.add_conditional_edges("escalate", route_next, targets)
    g.add_edge("synthesize", "act")
    g.add_conditional_edges("act", route_next, targets)
    g.add_edge("deliver", END)
    return g.compile(checkpointer=checkpointer)
