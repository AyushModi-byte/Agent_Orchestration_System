"""Span tree for one task: the explorer's data source."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from conductor.db.models import SpanRow


class TraceNode(BaseModel):
    span_id: str
    name: str
    agent: str | None
    role: str | None
    model: str | None
    status: str
    error: str | None
    duration_ms: float
    tokens_in: int
    tokens_out: int
    cost_usd: float  # this span only
    subtree_cost_usd: float  # this span plus everything under it
    attributes: dict[str, Any]
    children: list[TraceNode] = []


class TraceTree(BaseModel):
    task_id: str
    span_count: int
    status: str
    total_cost_usd: float
    roots: list[TraceNode]


def build_tree(rows: list[SpanRow]) -> list[TraceNode]:
    nodes = {
        r.span_id: TraceNode(
            span_id=r.span_id,
            name=r.name,
            agent=r.agent,
            role=r.role,
            model=r.model,
            status=r.status,
            error=r.error,
            duration_ms=r.duration_ms,
            tokens_in=r.tokens_in,
            tokens_out=r.tokens_out,
            cost_usd=r.cost_usd,
            subtree_cost_usd=r.cost_usd,
            attributes=r.attributes,
        )
        for r in rows
    }
    roots: list[TraceNode] = []
    for r in rows:  # rows are ordered by start time, so children stay in execution order
        node = nodes[r.span_id]
        parent = nodes.get(r.parent_span_id) if r.parent_span_id else None
        (parent.children if parent else roots).append(node)

    def total(n: TraceNode) -> float:
        n.subtree_cost_usd = n.cost_usd + sum(total(c) for c in n.children)
        return n.subtree_cost_usd

    for root in roots:
        total(root)
    return roots


def make_router(sf: sessionmaker[Session]) -> APIRouter:
    router = APIRouter()

    @router.get("/traces/{task_id}")
    def trace(task_id: str) -> TraceTree:
        with sf() as s:
            rows = (
                s.query(SpanRow)
                .filter(SpanRow.task_id == task_id)
                .order_by(SpanRow.start_time, SpanRow.pk)
                .all()
            )
        if not rows:
            raise HTTPException(404, "no spans recorded for this task")
        roots = build_tree(rows)
        return TraceTree(
            task_id=task_id,
            span_count=len(rows),
            status="error" if any(r.status == "error" for r in rows) else "ok",
            total_cost_usd=sum(r.subtree_cost_usd for r in roots),
            roots=roots,
        )

    return router
