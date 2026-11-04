"""Relational records for tasks, plan steps, LLM calls and the event feed behind SSE."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="queued")
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class PlanStep(Base):
    __tablename__ = "plan_steps"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_id: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    specialist: Mapped[str] = mapped_column(String)
    depends_on: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="pending")


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    role: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)  # notional, see prices.yaml
    created_at: Mapped[datetime] = mapped_column(default=_now)


class MemoryMeta(Base):
    """SQL mirror of every long-term memory: powers the dashboard and keeps provenance for
    memories that were merged away (archived rows point at the summary via merged_into)."""

    __tablename__ = "memory_meta"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    collection: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(String, index=True)
    repo: Mapped[str] = mapped_column(String, default="")
    type: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    feedback: Mapped[float] = mapped_column(Float, default=0.0)
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    merged_into: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_accessed: Mapped[datetime] = mapped_column(default=_now)


class Approval(Base):
    """One human gate. id is the LangGraph interrupt id, so re-reading a stream is idempotent."""

    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|decided|resolved
    context: Mapped[dict[str, Any]] = mapped_column(JSON)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ApprovalChat(Base):
    __tablename__ = "approval_chat"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), index=True)
    role: Mapped[str] = mapped_column(String)  # human | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class TaskEvent(Base):
    __tablename__ = "task_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_now)
