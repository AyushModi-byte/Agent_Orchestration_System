"""When must a human be involved? Pure functions over graph state and config/policies.yaml.

Triggers are grouped by the stage at which the graph can check them, so the graph asks only the
relevant question at each point (e.g. a production label matters when the plan is approved, not
again after every retry).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from conductor.config import PoliciesConfig
from conductor.hitl.levels import Level

_MERGE = re.compile(r"\bmerge\b", re.IGNORECASE)


class Trigger(BaseModel):
    name: str
    level: Level
    reason: str
    subtask_id: str | None = None


def _level(policy: PoliciesConfig, name: str, default: Level) -> Level:
    return Level(policy.triggers.get(name, default.value))


def is_sensitive(policy: PoliciesConfig, action: dict[str, Any], request: dict[str, Any]) -> bool:
    tool = str(action.get("tool", ""))
    s = policy.sensitive
    if tool in s.tools or any(p in tool.lower() for p in s.name_patterns):
        return True
    labels = request.get("labels") or []
    return tool == "post_review_comment" and s.production_label in labels


def evaluate(
    state: dict[str, Any],
    policy: PoliciesConfig,
    stage: str,
    action: dict[str, Any] | None = None,
) -> list[Trigger]:
    """Triggers firing at `stage`: intake | plan | work | action."""
    esc = policy.escalation
    req = state.get("request", {})
    out: list[Trigger] = []

    if stage == "intake":
        if req.get("merge") or _MERGE.search(str(req.get("request", ""))):
            out.append(
                Trigger(
                    name="merge_request",
                    level=_level(policy, "merge_request", Level.TAKE_OVER),
                    reason="a merge was requested; merging is human-only",
                )
            )

    elif stage == "plan":
        plan = state.get("plan")
        if plan is None:
            out.append(
                Trigger(
                    name="invalid_plan",
                    level=_level(policy, "invalid_plan", Level.APPROVE_PLAN),
                    reason=str(state.get("plan_error") or "no valid plan"),
                )
            )
        elif plan["confidence"] < esc.min_plan_confidence:
            out.append(
                Trigger(
                    name="low_plan_confidence",
                    level=_level(policy, "low_plan_confidence", Level.APPROVE_PLAN),
                    reason=f"plan confidence {plan['confidence']:.2f} < {esc.min_plan_confidence}",
                )
            )
        if req.get("review_required"):
            out.append(
                Trigger(
                    name="user_requested",
                    level=_level(policy, "user_requested", Level.APPROVE_PLAN),
                    reason="the requester asked to review before work starts",
                )
            )
        for label in req.get("labels") or []:
            if label in policy.repo_labels:
                out.append(
                    Trigger(
                        name="production_repo",
                        level=Level(policy.repo_labels[label]),
                        reason=f"repository is labelled {label!r}",
                    )
                )

    elif stage == "work":
        for sid, n in state.get("fail_count", {}).items():
            if n >= esc.repeated_failure_threshold:
                out.append(
                    Trigger(
                        name="repeated_failure",
                        level=_level(policy, "repeated_failure", Level.TAKE_OVER),
                        reason=f"subtask {sid} failed {n} times",
                        subtask_id=sid,
                    )
                )
        for sid, v in state.get("verdicts", {}).items():
            if not v["approved"] and v["score"] < esc.min_reviewer_score:
                out.append(
                    Trigger(
                        name="low_reviewer_score",
                        level=_level(policy, "low_reviewer_score", Level.APPROVE_ACTION),
                        reason=(
                            f"reviewer score {v['score']:.2f} < {esc.min_reviewer_score} on {sid}"
                        ),
                        subtask_id=sid,
                    )
                )
        for sid, n in state.get("reject_count", {}).items():
            if n > esc.max_reviewer_rejections:
                out.append(
                    Trigger(
                        name="retries_exhausted",
                        level=_level(policy, "retries_exhausted", Level.APPROVE_ACTION),
                        reason=f"subtask {sid} rejected {n} times by the reviewer",
                        subtask_id=sid,
                    )
                )

    elif stage == "action" and action is not None and is_sensitive(policy, action, req):
        out.append(
            Trigger(
                name="sensitive_operation",
                level=_level(policy, "sensitive_operation", Level.APPROVE_ACTION),
                reason=f"{action.get('tool')} is a sensitive operation",
            )
        )
    return out
