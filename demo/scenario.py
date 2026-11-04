"""The showcase scenario as code, shared by `make demo` and the e2e tests.

PR 1 is a plain SQL-injection fix and teaches the system a pattern. PR 2 is the main event: the
first fix breaks a test, the reviewer rejects it, memory recalls PR 1, the second fix passes and
the push waits for a human.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from conductor.db.models import SpanRow, Task
from conductor.hitl.feedback import record_decision
from conductor.hitl.queue import ApprovalQueue, Decision
from conductor.obs.cost import task_cost
from conductor.worker.runner import Runtime, execute_task, resume_task

Say = Callable[[str], None]
Decider = Callable[[dict[str, Any]], Decision]


def approve_all(_ctx: dict[str, Any]) -> Decision:
    return Decision(type="approve")


@dataclass
class Review:
    task_id: str
    status: str
    memories_recalled: int = 0
    reviewer_rejections: int = 0
    lessons_saved: int = 0
    cost_usd: float = 0.0
    approval: dict[str, Any] | None = None
    decision: str | None = None


@dataclass
class ScenarioResult:
    pr1: Review
    pr2: Review
    log: list[str] = field(default_factory=list)


def _facts(rt: Runtime, review: Review) -> None:
    with rt.session_factory() as s:
        spans = s.query(SpanRow).filter_by(task_id=review.task_id).all()
    for sp in spans:
        if sp.name == "memory.retrieve":
            review.memories_recalled += int(sp.attributes.get("count", 0))
        elif sp.name == "reviewer.evaluate" and not sp.attributes.get("approved"):
            review.reviewer_rejections += 1
        elif sp.name == "memory.extract":
            review.lessons_saved += int(sp.attributes.get("items", 0))
    review.cost_usd = task_cost(rt.session_factory, review.task_id).cost_usd


async def review_pr(
    rt: Runtime,
    pr: int,
    *,
    repo: str,
    user: str,
    head_ref: str | None,
    decide: Decider,
    say: Say,
) -> Review:
    """Run one PR review to the end, answering any human gate with `decide`."""
    tid = uuid.uuid4().hex
    body = {
        "request": f"Review PR #{pr} on {repo}",
        "repo": repo,
        "pr_number": pr,
        "user_id": user,
        "head_ref": head_ref,
    }
    with rt.session_factory() as s:
        s.add(Task(id=tid, request=body, status="queued"))
        s.commit()
    say(f"PR #{pr}: supervisor is planning...")
    await execute_task(tid, rt)
    queue = ApprovalQueue(rt.session_factory)
    review = Review(task_id=tid, status="")
    while True:
        with rt.session_factory() as s:
            status = s.get(Task, tid).status  # type: ignore[union-attr]
        if status != "awaiting_approval":
            break
        (pending,) = [a for a in queue.list_approvals("pending") if a.task_id == tid]
        ctx = dict(pending.context)
        review.approval = ctx
        say(f"PR #{pr}: paused for a human ({ctx['kind']}, {ctx['level']}).")
        d = decide(ctx)
        review.decision = d.type
        queue.decide(pending.id, d)
        if rt.memory_store is not None:
            record_decision(rt.memory_store, ctx, d)
        say(f"PR #{pr}: human said {d.type}; resuming from the checkpoint.")
        await resume_task(tid, pending.id, rt)
    with rt.session_factory() as s:
        review.status = s.get(Task, tid).status  # type: ignore[union-attr]
    _facts(rt, review)
    return review


async def run_scenario(
    rt: Runtime,
    *,
    repo: str,
    user: str,
    decide: Decider = approve_all,
    say: Say = lambda _m: None,
) -> ScenarioResult:
    pr1 = await review_pr(rt, 1, repo=repo, user=user, head_ref=None, decide=decide, say=say)
    say(
        f"PR #1 {pr1.status}: saved {pr1.lessons_saved} lessons to memory "
        f"(${pr1.cost_usd:.4f} notional)."
    )
    pr2 = await review_pr(
        rt, 2, repo=repo, user=user, head_ref="fix/sql-injection", decide=decide, say=say
    )
    say(
        f"PR #2 {pr2.status}: recalled {pr2.memories_recalled} memories, reviewer rejected "
        f"{pr2.reviewer_rejections} attempt(s), saved {pr2.lessons_saved} lessons "
        f"(${pr2.cost_usd:.4f} notional)."
    )
    return ScenarioResult(pr1=pr1, pr2=pr2)
