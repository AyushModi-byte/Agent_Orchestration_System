"""Approval queue API. A decision records feedback, then resumes the paused graph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from conductor.hitl.feedback import record_decision
from conductor.hitl.queue import ApprovalQueue, Decision
from conductor.llm.types import LLM
from conductor.memory.longterm import LongTermStore


class ApprovalOut(BaseModel):
    id: str
    task_id: str
    kind: str
    level: str
    status: str
    context: dict[str, Any]
    decision: dict[str, Any] | None


class ChatIn(BaseModel):
    question: str


class ChatOut(BaseModel):
    answer: str
    history: list[dict[str, str]]


def make_router(
    queue: ApprovalQueue,
    enqueue_resume: Callable[[str, str], None],
    memory_store: LongTermStore | None,
    llm: LLM | None,
) -> APIRouter:
    router = APIRouter()

    def out(a: Any) -> ApprovalOut:
        return ApprovalOut(
            id=a.id,
            task_id=a.task_id,
            kind=a.kind,
            level=a.level,
            status=a.status,
            context=a.context,
            decision=a.decision,
        )

    @router.get("/approvals")
    def list_approvals(status: str | None = None) -> list[ApprovalOut]:
        return [out(a) for a in queue.list_approvals(status)]

    @router.get("/approvals/{approval_id}")
    def get_approval(approval_id: str) -> ApprovalOut:
        a = queue.get(approval_id)
        if a is None:
            raise HTTPException(404, "approval not found")
        return out(a)

    @router.post("/approvals/{approval_id}/decision", status_code=202)
    def decide(approval_id: str, body: dict[str, Any]) -> dict[str, str]:
        try:
            decision = Decision.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
        try:
            row = queue.decide(approval_id, decision)
        except LookupError:
            raise HTTPException(404, "approval not found") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(422, str(exc)) from exc
        if memory_store is not None:
            record_decision(memory_store, row.context, decision)
        enqueue_resume(row.task_id, approval_id)
        return {"status": "resuming"}

    @router.post("/approvals/{approval_id}/chat")
    async def chat(approval_id: str, body: ChatIn) -> ChatOut:
        if llm is None:
            raise HTTPException(503, "chat is not configured")
        try:
            answer = await queue.chat(approval_id, body.question, llm)
        except LookupError:
            raise HTTPException(404, "approval not found") from None
        return ChatOut(
            answer=answer,
            history=[{"role": h.role, "content": h.content} for h in queue.history(approval_id)],
        )

    return router
