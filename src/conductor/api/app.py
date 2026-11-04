"""REST + SSE API. Creating a task only records it and enqueues work; the worker does the rest."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from conductor.api.routes.approvals import make_router as make_approvals_router
from conductor.api.routes.memory import make_router as make_memory_router
from conductor.api.routes.metrics import make_router as make_metrics_router
from conductor.api.routes.replays import make_router as make_replays_router
from conductor.api.routes.traces import make_router as make_traces_router
from conductor.db.models import PlanStep, Task, TaskEvent
from conductor.hitl.queue import ApprovalQueue
from conductor.llm.types import LLM
from conductor.memory.longterm import LongTermStore
from conductor.obs.replay import ReplayService

TERMINAL = {"completed", "needs_human", "failed", "taken_over"}


class TaskCreate(BaseModel):
    request: str
    repo: str | None = None
    pr_number: int | None = None
    snapshot_dir: str | None = None
    user_id: str = "default"
    labels: list[str] = []
    head_ref: str | None = None
    review_required: bool = False


class StepOut(BaseModel):
    id: str
    description: str
    specialist: str
    depends_on: list[str]
    status: str


class TaskOut(BaseModel):
    id: str
    status: str
    needs_human: bool
    escalation_reason: str | None
    report: dict[str, Any] | None
    error: str | None
    steps: list[StepOut]


def create_app(
    session_factory: sessionmaker[Session],
    enqueue: Callable[[str], None],
    poll_interval: float = 0.2,
    memory_store: LongTermStore | None = None,
    enqueue_resume: Callable[[str, str], None] | None = None,
    llm: LLM | None = None,
    replay_service: ReplayService | None = None,
) -> FastAPI:
    app = FastAPI(title="Conductor")
    if memory_store is not None:
        app.include_router(make_memory_router(memory_store))
    app.include_router(make_traces_router(session_factory))
    app.include_router(make_metrics_router(session_factory))
    if replay_service is not None:
        app.include_router(make_replays_router(replay_service))
    if enqueue_resume is not None:
        app.include_router(
            make_approvals_router(ApprovalQueue(session_factory), enqueue_resume, memory_store, llm)
        )

    @app.post("/tasks", status_code=202)
    def create_task(body: TaskCreate) -> dict[str, str]:
        tid = uuid.uuid4().hex
        with session_factory() as s:
            s.add(Task(id=tid, request=body.model_dump(), status="queued"))
            s.commit()
        enqueue(tid)
        return {"id": tid, "status": "queued"}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> TaskOut:
        with session_factory() as s:
            t = s.get(Task, task_id)
            if t is None:
                raise HTTPException(404, "task not found")
            steps = s.query(PlanStep).filter(PlanStep.task_id == task_id).order_by(PlanStep.pk)
            return TaskOut(
                id=t.id,
                status=t.status,
                needs_human=t.needs_human,
                escalation_reason=t.escalation_reason,
                report=t.report,
                error=t.error,
                steps=[
                    StepOut(
                        id=r.step_id,
                        description=r.description,
                        specialist=r.specialist,
                        depends_on=r.depends_on,
                        status=r.status,
                    )
                    for r in steps
                ],
            )

    @app.get("/tasks/{task_id}/events")
    async def task_events(task_id: str) -> StreamingResponse:
        with session_factory() as s:
            if s.get(Task, task_id) is None:
                raise HTTPException(404, "task not found")

        async def stream() -> AsyncIterator[str]:
            last = 0
            while True:
                with session_factory() as s:
                    rows = (
                        s.query(TaskEvent)
                        .filter(TaskEvent.task_id == task_id, TaskEvent.id > last)
                        .order_by(TaskEvent.id)
                        .all()
                    )
                    t = s.get(Task, task_id)
                    status = t.status if t else "failed"
                for r in rows:
                    last = r.id
                    yield f"event: {r.type}\ndata: {json.dumps(r.payload)}\n\n"
                if status in TERMINAL and not rows:
                    yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                    return
                await asyncio.sleep(poll_interval)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
