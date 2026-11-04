"""Cost and usage reporting. Prices are notional (free tiers bill nothing): each llm_calls row
already carries its cost, computed from config/prices.yaml when the call was made."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from conductor.db.models import Approval, LLMCall, SpanRow, Task, ToolCall


class Bucket(BaseModel):
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def add(self, c: LLMCall) -> None:
        self.calls += 1
        self.tokens_in += c.tokens_in
        self.tokens_out += c.tokens_out
        self.cost_usd += c.cost_usd


class TaskCost(BaseModel):
    task_id: str
    llm_calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    by_agent: dict[str, Bucket]
    by_model: dict[str, Bucket]
    tool_calls: int
    tool_calls_by_tool: dict[str, int]
    wall_clock_seconds: float  # agent time: human waiting is excluded, reported separately
    human_review_seconds: float


def task_type(request: dict[str, object]) -> str:
    explicit = request.get("task_type")
    if isinstance(explicit, str) and explicit:
        return explicit
    return "pr_review" if request.get("pr_number") else "general"


def task_cost(sf: sessionmaker[Session], task_id: str) -> TaskCost:
    with sf() as s:
        calls = s.query(LLMCall).filter(LLMCall.task_id == task_id).all()
        tools = s.query(ToolCall).filter(ToolCall.task_id == task_id).all()
        roots = (
            s.query(SpanRow)
            .filter(SpanRow.task_id == task_id, SpanRow.parent_span_id.is_(None))
            .all()
        )
        approvals = s.query(Approval).filter(Approval.task_id == task_id).all()
    by_agent: dict[str, Bucket] = defaultdict(Bucket)
    by_model: dict[str, Bucket] = defaultdict(Bucket)
    for c in calls:
        by_agent[c.agent or "unknown"].add(c)
        by_model[c.model].add(c)
    per_tool: dict[str, int] = defaultdict(int)
    for t in tools:
        per_tool[t.tool] += 1
    return TaskCost(
        task_id=task_id,
        llm_calls=len(calls),
        tokens_in=sum(c.tokens_in for c in calls),
        tokens_out=sum(c.tokens_out for c in calls),
        cost_usd=sum(c.cost_usd for c in calls),
        by_agent=dict(by_agent),
        by_model=dict(by_model),
        tool_calls=len(tools),
        tool_calls_by_tool=dict(per_tool),
        wall_clock_seconds=sum(r.duration_ms for r in roots) / 1000,
        human_review_seconds=sum(
            (a.decided_at - a.created_at).total_seconds() for a in approvals if a.decided_at
        ),
    )


class TypeCost(BaseModel):
    task_type: str
    tasks: int
    cost_usd: float
    avg_cost_usd: float
    tokens: int


class AgentCost(BaseModel):
    agent: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


class ToolUsage(BaseModel):
    tool: str
    calls: int
    errors: int
    avg_latency_ms: float


class EscalationDay(BaseModel):
    day: str
    tasks: int
    escalated: int
    rate: float


class CostReport(BaseModel):
    per_task_type: list[TypeCost]
    top_agents: list[AgentCost]
    tool_usage: list[ToolUsage]
    escalation_trend: list[EscalationDay]


def cost_report(
    sf: sessionmaker[Session], days: int = 30, now: datetime | None = None
) -> CostReport:
    since = ((now or datetime.now(UTC)) - timedelta(days=days)).replace(tzinfo=None)
    with sf() as s:
        tasks = s.query(Task).filter(Task.created_at >= since).all()
        ids = {t.id for t in tasks}
        calls = [c for c in s.query(LLMCall).all() if c.task_id in ids]
        tools = [t for t in s.query(ToolCall).all() if t.task_id in ids]
        escalated = {a.task_id for a in s.query(Approval).all()}

    types: dict[str, list[Task]] = defaultdict(list)
    for t in tasks:
        types[task_type(t.request)].append(t)
    cost_by_task: dict[str, float] = defaultdict(float)
    tokens_by_task: dict[str, int] = defaultdict(int)
    agents: dict[str, AgentCost] = {}
    for c in calls:
        assert c.task_id is not None
        cost_by_task[c.task_id] += c.cost_usd
        tokens_by_task[c.task_id] += c.tokens_in + c.tokens_out
        a = agents.setdefault(
            c.agent or "unknown",
            AgentCost(agent=c.agent or "unknown", calls=0, tokens_in=0, tokens_out=0, cost_usd=0.0),
        )
        a.calls += 1
        a.tokens_in += c.tokens_in
        a.tokens_out += c.tokens_out
        a.cost_usd += c.cost_usd

    per_type = []
    for name, ts in types.items():
        total = sum(cost_by_task[t.id] for t in ts)
        per_type.append(
            TypeCost(
                task_type=name,
                tasks=len(ts),
                cost_usd=total,
                avg_cost_usd=total / len(ts),
                tokens=sum(tokens_by_task[t.id] for t in ts),
            )
        )

    usage: dict[str, list[ToolCall]] = defaultdict(list)
    for tc in tools:
        usage[tc.tool].append(tc)

    per_day: dict[str, list[Task]] = defaultdict(list)
    for t in tasks:
        per_day[t.created_at.date().isoformat()].append(t)
    trend = [
        EscalationDay(
            day=day,
            tasks=len(ts),
            escalated=sum(1 for t in ts if t.id in escalated),
            rate=sum(1 for t in ts if t.id in escalated) / len(ts),
        )
        for day, ts in sorted(per_day.items())
    ]
    return CostReport(
        per_task_type=sorted(per_type, key=lambda x: -x.cost_usd),
        top_agents=sorted(agents.values(), key=lambda x: -x.cost_usd),
        tool_usage=sorted(
            (
                ToolUsage(
                    tool=name,
                    calls=len(rows),
                    errors=sum(1 for r in rows if not r.success),
                    avg_latency_ms=sum(r.latency_ms for r in rows) / len(rows),
                )
                for name, rows in usage.items()
            ),
            key=lambda x: -x.calls,
        ),
        escalation_trend=trend,
    )
