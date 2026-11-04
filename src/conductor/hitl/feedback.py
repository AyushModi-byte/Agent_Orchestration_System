"""Every human decision becomes memory feedback, feeding P3's importance scoring.

Two effects: the decision itself is stored (so similar future gates can cite it), and the
memories that informed the plan are nudged up when the human agreed and down when they did not.
"""

from __future__ import annotations

from typing import Any

from conductor.hitl.queue import Decision
from conductor.memory.longterm import LongTermStore

DECISION_WEIGHT = {"approve": 1.0, "modify": 0.5, "take_over": -0.5, "reject": -1.0}
USED_MEMORY_DELTA = {"approve": 0.3, "modify": 0.1, "take_over": -0.1, "reject": -0.3}


def record_decision(store: LongTermStore, context: dict[str, Any], decision: Decision) -> str:
    task = context.get("task") or {}
    proposal = context.get("proposed_action") or {}
    what = proposal.get("summary") or context.get("kind", "step")
    text = f"{task.get('request', '')}. Human {decision.type} on {what}. {decision.reason}".strip()
    mem_id = store.add(
        "task_memories",
        text,
        user_id=str(task.get("user_id", "default")),
        repo=str(task.get("repo") or ""),
        type="decision",
        feedback=DECISION_WEIGHT[decision.type],
    )
    for coll, id_ in context.get("memory_refs", []):
        store.apply_feedback(coll, id_, USED_MEMORY_DELTA[decision.type])
    return mem_id
