"""Cost and usage metrics."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.orm import Session, sessionmaker

from conductor.obs.cost import CostReport, TaskCost, cost_report, task_cost


def make_router(sf: sessionmaker[Session]) -> APIRouter:
    router = APIRouter()

    @router.get("/metrics/cost")
    def cost(days: int = 30) -> CostReport:
        return cost_report(sf, days)

    @router.get("/metrics/tasks/{task_id}")
    def per_task(task_id: str) -> TaskCost:
        return task_cost(sf, task_id)

    return router
