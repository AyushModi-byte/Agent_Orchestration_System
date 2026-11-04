"""Human gates as graph nodes.

Each gate node calls interrupt() exactly once and has no side effects before it, because LangGraph
re-runs the node from the top when the human's decision resumes it. Outward actions are therefore
executed one per node run (see act), never several around an interrupt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from conductor.agents.schemas import ExecutionPlan
from conductor.agents.supervisor import PlanValidationError, validate_plan
from conductor.hitl.levels import ALL_DECISIONS, Level, allowed_decisions, highest, pauses
from conductor.hitl.queue import Decision, ask_human, package_context
from conductor.hitl.triggers import Trigger, evaluate

if TYPE_CHECKING:
    from conductor.graph.builder import GraphDeps

RESET: dict[str, Any] = {"__reset__": True}


def _ev(type_: str, **data: Any) -> dict[str, Any]:
    return {"type": type_, **data}


def plan_actions(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Outward actions implied by the finished review: one comment, plus any fix with content."""
    req, report = state["request"], state.get("report") or {}
    repo, number = req.get("repo"), req.get("pr_number")
    if not repo or not number:
        return []
    actions: list[dict[str, Any]] = [
        {
            "tool": "post_review_comment",
            "agent": "supervisor",
            "summary": f"post review on {repo}#{number}",
            "reasoning": "Share the verified findings with the author.",
            "args": {
                "repo": repo,
                "number": number,
                "body": report.get("summary", ""),
                "comments": [
                    {"path": f["file"], "line": f["line"], "body": f"{f['rule']}: {f['evidence']}"}
                    for f in report.get("findings", [])
                ],
            },
        }
    ]
    for fx in report.get("fixes", []):
        path, branch = fx.get("path") or fx.get("file"), fx.get("branch") or req.get("head_ref")
        if path and branch and fx.get("content") is not None:
            actions.append(
                {
                    "tool": "push_fix_commit",
                    "agent": "fix_writer",
                    "summary": f"push fix to {path} on {branch}",
                    "reasoning": fx.get("reasoning", "Reviewer-approved fix with a passing test."),
                    "args": {
                        "repo": repo,
                        "branch": branch,
                        "path": path,
                        "content": fx["content"],
                        "message": fx.get("message", f"fix: {path}"),
                    },
                }
            )
    return actions


class Gates:
    def __init__(self, deps: GraphDeps) -> None:
        self.deps = deps
        self.policy = deps.policy_config

    # -- shared pieces ------------------------------------------------------------------
    def _similar(self, state: dict[str, Any]) -> list[str]:
        store = self.deps.memory_store
        if store is None:
            return []
        req = state["request"]
        hits = store.query(
            "task_memories",
            str(req.get("request", "")),
            k=3,
            user_id=str(req.get("user_id", "default")),
            type="decision",
            max_distance=store.cfg.max_distance,
        )
        return [it.text for it, _ in hits]

    def _ask(
        self,
        state: dict[str, Any],
        kind: str,
        level: Level,
        triggers: list[Trigger],
        kind_allowed: tuple[str, ...],
        proposal: dict[str, Any] | None,
    ) -> Decision:
        ctx = package_context(
            state,
            kind=kind,
            level=level,
            triggers=triggers,
            allowed=allowed_decisions(level, kind_allowed),
            proposal=proposal,
            similar_decisions=self._similar(state),
        )
        return ask_human(ctx)

    def _common(
        self, state: dict[str, Any], d: Decision, kind: str, level: Level
    ) -> dict[str, Any] | None:
        """Outcomes shared by every gate: reject re-plans, take_over stands the agents down."""
        fb = {"gate": kind, "level": level.value, "decision": d.type, "reason": d.reason}
        base: dict[str, Any] = {
            "human_feedback": [fb],
            "events": [_ev("decision", gate=kind, decision=d.type)],
        }
        if d.type == "take_over":
            out = d.output or {}
            return {
                **base,
                "taken_over": True,
                "final_status": "taken_over",
                "report": {
                    "summary": str(out.get("summary", "")),
                    "findings": [],
                    "fixes": [],
                    "human_output": out,
                },
                "next_step": "deliver",
            }
        if d.type == "reject":
            n = state.get("human_rejections", 0) + 1
            if n > self.policy.escalation.max_human_rejections:
                return {
                    **base,
                    "human_rejections": n,
                    "final_status": "needs_human",
                    "needs_human": True,
                    "escalation_reason": f"rejected {n} times; a human must handle this task",
                    "next_step": "deliver",
                }
            return {
                **base,
                "human_rejections": n,
                "plan_feedback": [d.reason],
                "results": RESET,
                "verdicts": RESET,
                "fail_count": RESET,
                "reject_count": RESET,
                "hints": RESET,
                "feedback": RESET,
                "plan": None,
                "plan_error": None,
                "report": None,
                "actions": None,
                "action_idx": 0,
                "next_step": "plan",
            }
        return None

    # -- nodes --------------------------------------------------------------------------
    async def gate_plan(self, state: dict[str, Any]) -> dict[str, Any]:
        triggers = evaluate(state, self.policy, "plan")
        level = highest([t.level for t in triggers])
        if not pauses(level):
            return {"events": [_ev("notified", gate="plan")], "next_step": "dispatch"}
        has_plan = state.get("plan") is not None
        kind_allowed = ALL_DECISIONS if has_plan else ("modify", "reject", "take_over")
        d = self._ask(
            state,
            "plan",
            level,
            triggers,
            kind_allowed,
            {"summary": "execute this plan", "plan": state.get("plan")},
        )
        if (common := self._common(state, d, "plan", level)) is not None:
            return common
        base = {
            "human_feedback": [{"gate": "plan", "level": level.value, "decision": d.type}],
            "events": [_ev("decision", gate="plan", decision=d.type)],
        }
        if d.type == "approve":
            return {**base, "next_step": "dispatch"}
        try:  # modify: the human supplies a replacement plan
            plan = ExecutionPlan.model_validate(d.edited)
            validate_plan(plan)
        except (PlanValidationError, ValueError) as exc:
            return {
                **base,
                "events": [_ev("modify_invalid", error=str(exc))],
                "next_step": "gate_plan",
            }
        return {**base, "plan": plan.model_dump(), "plan_error": None, "next_step": "dispatch"}

    async def escalate(self, state: dict[str, Any]) -> dict[str, Any]:
        kind_allowed: tuple[str, ...]
        proposal: dict[str, Any]
        if state.get("plan") is None:
            kind = "takeover"
            triggers = evaluate(state, self.policy, "intake")
            kind_allowed = ("take_over", "reject")
            proposal = {"summary": "hand this task to a human; agents stand down"}
            level = highest([t.level for t in triggers] or [Level.TAKE_OVER])
        else:
            kind = "failure"
            triggers = evaluate(state, self.policy, "work") or [
                Trigger(
                    name="retries_exhausted",
                    level=Level.APPROVE_ACTION,
                    reason="no subtask can make progress",
                )
            ]
            kind_allowed = ("approve", "reject", "take_over")
            stuck = [t.subtask_id for t in triggers if t.subtask_id]
            proposal = {"summary": "grant another round of retries", "subtasks": stuck}
            # a stuck run must never continue silently, even if policy says notify
            level = highest([*[t.level for t in triggers], Level.APPROVE_ACTION])
        d = self._ask(state, kind, level, triggers, kind_allowed, proposal)
        if (common := self._common(state, d, kind, level)) is not None:
            return common
        # approve on a failure gate: reset counters so the stuck subtasks get a fresh round
        return {
            "human_feedback": [{"gate": kind, "level": level.value, "decision": "approve"}],
            "events": [_ev("decision", gate=kind, decision="approve")],
            "fail_count": RESET,
            "reject_count": RESET,
            "next_step": "dispatch",
        }

    async def act(self, state: dict[str, Any]) -> dict[str, Any]:
        """Perform one outward action per run, gating it by its level."""
        actions = state.get("actions")
        if actions is None:
            actions = plan_actions(state) if self.deps.registry is not None else []
        idx = state.get("action_idx", 0)
        if idx >= len(actions):
            return {"actions": actions, "next_step": "deliver"}
        action = actions[idx]
        triggers = evaluate(state, self.policy, "action", action)
        base_level = Level(self.policy.actions.get(action["tool"], "notify"))
        level = highest([base_level, *[t.level for t in triggers]])
        args = action["args"]
        events: list[dict[str, Any]] = []
        feedback: list[dict[str, Any]] = []
        if pauses(level):
            d = self._ask(state, "action", level, triggers, ALL_DECISIONS, action)
            if (common := self._common(state, d, "action", level)) is not None:
                return {**common, "actions": actions}
            feedback.append({"gate": "action", "level": level.value, "decision": d.type})
            events.append(_ev("decision", gate="action", decision=d.type, tool=action["tool"]))
            if d.type == "modify":
                args = {**args, **(d.edited or {})}
        else:
            events.append(_ev("notified", gate="action", tool=action["tool"]))
        assert self.deps.registry is not None
        try:
            out = await self.deps.registry.invoke(action["agent"], action["tool"], args)
            events.append(_ev("action_done", tool=action["tool"], result=out.model_dump()))
        except Exception as exc:  # a failed action is reported, not fatal to the review
            events.append(_ev("action_failed", tool=action["tool"], error=str(exc)))
        return {
            "actions": actions,
            "action_idx": idx + 1,
            "human_feedback": feedback,
            "events": events,
            "next_step": "act" if idx + 1 < len(actions) else "deliver",
        }
