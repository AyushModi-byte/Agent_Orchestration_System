"""Replay API: inspect a task's decisions, re-run with overrides, diff against the original."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from conductor.obs.replay import Override, ReplayDiff, ReplayService, Step


class ReplayIn(BaseModel):
    task_id: str
    overrides: list[Override] = []


class ReplayOut(BaseModel):
    id: str
    source_task_id: str
    status: str
    live_calls: int
    cost_usd: float
    error: str | None
    result: dict[str, Any] | None


def make_router(service: ReplayService) -> APIRouter:
    router = APIRouter()

    def out(rid: str) -> ReplayOut:
        r = service.get(rid)
        if r is None:
            raise HTTPException(404, "replay not found")
        return ReplayOut(
            id=r.id,
            source_task_id=r.source_task_id,
            status=r.status,
            live_calls=r.live_calls,
            cost_usd=r.cost_usd,
            error=r.error,
            result=r.result,
        )

    @router.post("/replays", status_code=201)
    async def create(body: ReplayIn) -> ReplayOut:
        try:
            rid = await service.run(body.task_id, body.overrides)
        except LookupError:
            raise HTTPException(404, "task not found") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return out(rid)

    @router.get("/replays/steps")
    def steps(task_id: str) -> list[Step]:
        return service.steps(task_id)

    @router.get("/replays/{replay_id}")
    def get(replay_id: str) -> ReplayOut:
        return out(replay_id)

    @router.get("/replays/{replay_id}/diff")
    def diff(replay_id: str) -> ReplayDiff:
        try:
            return service.diff(replay_id)
        except LookupError:
            raise HTTPException(404, "replay not found") from None

    return router
