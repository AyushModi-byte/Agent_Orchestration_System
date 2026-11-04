"""Span tree for one task: the explorer's data source."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from conductor.db.models import LLMCall, SpanRow, ToolCall


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
    llm_call_id: int | None = None
    attributes: dict[str, Any]
    children: list[TraceNode] = []


class TraceTree(BaseModel):
    task_id: str
    span_count: int
    status: str
    total_cost_usd: float
    roots: list[TraceNode]


class LLMDetail(BaseModel):
    role: str
    model: str
    schema_name: str | None
    messages: list[dict[str, str]]
    response: str | None
    cache_hit: bool


class ToolDetail(BaseModel):
    tool: str
    inputs: Any
    outputs: Any
    success: bool
    error: str | None


class SpanDetail(BaseModel):
    """What a node's drawer shows: the full prompt/response or tool input/output behind a span."""

    span_id: str
    name: str
    llm: LLMDetail | None = None
    tool: ToolDetail | None = None


def _json(text: str | None) -> Any:
    try:
        return json.loads(text) if text is not None else None
    except ValueError:
        return text


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
            llm_call_id=r.llm_call_id,
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

    @router.get("/traces/{task_id}/spans/{span_id}")
    def span_detail(task_id: str, span_id: str) -> SpanDetail:
        with sf() as s:
            sp = s.query(SpanRow).filter_by(task_id=task_id, span_id=span_id).first()
            if sp is None:
                raise HTTPException(404, "span not found")
            out = SpanDetail(span_id=span_id, name=sp.name)
            if sp.llm_call_id is not None and (row := s.get(LLMCall, sp.llm_call_id)):
                out.llm = LLMDetail(
                    role=row.role,
                    model=row.model,
                    schema_name=row.schema_name,
                    messages=row.request or [],
                    response=row.response,
                    cache_hit=row.cache_hit,
                )
            elif sp.name == "tool.call":
                tool = sp.attributes.get("tool")
                same = (
                    s.query(SpanRow)
                    .filter_by(task_id=task_id, name="tool.call", agent=sp.agent)
                    .order_by(SpanRow.start_time, SpanRow.pk)
                    .all()
                )
                same = [
                    x
                    for x in same
                    if x.attributes.get("tool") == tool
                    and x.attributes.get("subtask_id") == sp.attributes.get("subtask_id")
                ]
                calls = (
                    s.query(ToolCall)
                    .filter_by(task_id=task_id, agent=sp.agent or "", tool=tool or "")
                    .order_by(ToolCall.id)
                    .all()
                )
                calls = [c for c in calls if c.subtask_id == sp.attributes.get("subtask_id")]
                idx = next((i for i, x in enumerate(same) if x.span_id == span_id), -1)
                if 0 <= idx < len(calls):
                    c = calls[idx]
                    out.tool = ToolDetail(
                        tool=c.tool,
                        inputs=_json(c.inputs),
                        outputs=_json(c.outputs),
                        success=c.success,
                        error=c.error,
                    )
            return out

    return router
