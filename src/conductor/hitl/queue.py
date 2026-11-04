"""The approval queue: context packaging for interrupt(), approvals rows and the chat helper."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.types import interrupt
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session, sessionmaker

from conductor.agents.prompts_loader import load_prompt
from conductor.db.models import Approval, ApprovalChat
from conductor.hitl.levels import Level
from conductor.hitl.triggers import Trigger
from conductor.llm.types import LLM


class Decision(BaseModel):
    type: Literal["approve", "modify", "reject", "take_over"]
    edited: dict[str, Any] | None = None  # modify: the edited plan or action
    reason: str = ""  # reject: feedback for the planner
    output: dict[str, Any] | None = None  # take_over: what the human supplies
    decided_by: str = "human"

    @model_validator(mode="after")
    def _complete(self) -> Decision:
        if self.type == "modify" and not self.edited:
            raise ValueError("modify needs `edited`")
        if self.type == "reject" and not self.reason.strip():
            raise ValueError("reject needs a `reason` so the planner can use it")
        if self.type == "take_over" and not self.output:
            raise ValueError("take_over needs `output`")
        return self


def package_context(
    state: dict[str, Any],
    *,
    kind: str,
    level: Level,
    triggers: list[Trigger],
    allowed: list[str],
    proposal: dict[str, Any] | None,
    similar_decisions: list[str],
) -> dict[str, Any]:
    """Everything a human needs to decide without opening the trace explorer."""
    plan = state.get("plan") or {}
    steps = plan.get("steps", [])
    approved = {
        sid
        for sid, v in state.get("verdicts", {}).items()
        if v["approved"] and state.get("results", {}).get(sid, {}).get("run_id") == v["run_id"]
    }
    completed = [
        {"id": s["id"], "specialist": s["specialist"], "description": s["description"]}
        for s in steps
        if s["id"] in approved
    ]
    current = [s for s in steps if s["id"] not in approved]
    return {
        "task_id": state.get("task_id"),
        "kind": kind,
        "level": level.value,
        "allowed_decisions": allowed,
        "triggers": [t.model_dump(mode="json") for t in triggers],
        "task": state.get("request"),
        "plan": plan or None,
        "plan_error": state.get("plan_error"),
        "completed_steps": completed,
        "current_step": current[0] if current else None,
        "proposed_action": proposal,
        "memories": state.get("memories", []),
        "memory_refs": state.get("memory_refs", []),
        "similar_decisions": similar_decisions,
    }


def ask_human(context: dict[str, Any]) -> Decision:
    """Pause the graph. On resume LangGraph returns the human's decision from the checkpoint."""
    return Decision.model_validate(interrupt(context))


class ApprovalQueue:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def create(self, task_id: str, approval_id: str, context: dict[str, Any]) -> bool:
        """Insert once per interrupt; returns False if it was already recorded."""
        with self._sf() as s:
            if s.get(Approval, approval_id) is not None:
                return False
            s.add(
                Approval(
                    id=approval_id,
                    task_id=task_id,
                    kind=context["kind"],
                    level=context["level"],
                    context=context,
                )
            )
            s.commit()
            return True

    def list_approvals(self, status: str | None = None) -> list[Approval]:
        with self._sf() as s:
            q = s.query(Approval).order_by(Approval.created_at)
            if status:
                q = q.filter(Approval.status == status)
            return list(q.all())

    def get(self, approval_id: str) -> Approval | None:
        with self._sf() as s:
            return s.get(Approval, approval_id)

    def decide(self, approval_id: str, decision: Decision) -> Approval:
        """Record a decision (LookupError unknown, ValueError repeated, PermissionError illegal)."""
        with self._sf() as s:
            row = s.get(Approval, approval_id)
            if row is None:
                raise LookupError(approval_id)
            if row.status != "pending":
                raise ValueError("this approval has already been decided")
            if decision.type not in row.context["allowed_decisions"]:
                raise PermissionError(
                    f"{decision.type!r} not allowed here; "
                    f"allowed: {row.context['allowed_decisions']}"
                )
            row.decision = decision.model_dump(mode="json")
            row.status = "decided"
            row.decided_at = datetime.now(UTC)
            s.commit()
            return row

    def resolve(self, approval_id: str) -> None:
        with self._sf() as s:
            row = s.get(Approval, approval_id)
            if row is not None:
                row.status = "resolved"
                s.commit()

    def history(self, approval_id: str) -> list[ApprovalChat]:
        with self._sf() as s:
            return list(
                s.query(ApprovalChat)
                .filter(ApprovalChat.approval_id == approval_id)
                .order_by(ApprovalChat.id)
                .all()
            )

    async def chat(self, approval_id: str, question: str, llm: LLM) -> str:
        """Answer a clarifying question from the approval context. Writes only chat rows: the
        task, the approval and the graph checkpoint are never touched."""
        row = self.get(approval_id)
        if row is None:
            raise LookupError(approval_id)
        msgs = [
            {"role": "system", "content": load_prompt("approval_chat")},
            {"role": "user", "content": "Approval context:\n" + json.dumps(row.context)},
        ]
        for h in self.history(approval_id):
            msgs.append(
                {"role": "user" if h.role == "human" else "assistant", "content": h.content}
            )
        msgs.append({"role": "user", "content": question})
        res = await llm.complete("cheap", msgs)
        with self._sf() as s:
            s.add(ApprovalChat(approval_id=approval_id, role="human", content=question))
            s.add(ApprovalChat(approval_id=approval_id, role="assistant", content=res.text))
            s.commit()
        return res.text
