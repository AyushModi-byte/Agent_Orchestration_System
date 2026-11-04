"""Supervisor: turns a request into a validated DAG of subtasks and merges approved results."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from conductor.agents.prompts_loader import load_prompt
from conductor.agents.schemas import (
    SPECIALISTS,
    ExecutionPlan,
    ReviewReport,
    ReviewVerdict,
    SpecialistResult,
)
from conductor.llm.types import LLM, SchemaValidationError


class PlanValidationError(Exception):
    """The planner produced a plan that cannot be executed."""


def validate_plan(plan: ExecutionPlan) -> None:
    """Reject bad ids, unknown specialists and cycles before any work is dispatched."""
    ids = [s.id for s in plan.steps]
    if not ids:
        raise PlanValidationError("plan has no steps")
    if len(set(ids)) != len(ids):
        raise PlanValidationError("duplicate subtask ids")
    for s in plan.steps:
        if s.specialist not in SPECIALISTS:
            raise PlanValidationError(f"{s.id}: unknown specialist {s.specialist}")
        for d in s.depends_on:
            if d not in ids:
                raise PlanValidationError(f"{s.id}: depends on unknown id {d}")
            if d == s.id:
                raise PlanValidationError(f"{s.id}: depends on itself")
    # Kahn's algorithm: anything left unprocessed sits on a cycle.
    indeg = {s.id: len(s.depends_on) for s in plan.steps}
    ready = [i for i, n in indeg.items() if n == 0]
    seen = 0
    while ready:
        cur = ready.pop()
        seen += 1
        for s in plan.steps:
            if cur in s.depends_on:
                indeg[s.id] -= 1
                if indeg[s.id] == 0:
                    ready.append(s.id)
    if seen != len(ids):
        raise PlanValidationError("plan has a dependency cycle")


class Supervisor:
    def __init__(self, llm: LLM, max_plan_attempts: int = 2) -> None:
        self._llm = llm
        self._attempts = max_plan_attempts

    async def plan(
        self,
        task: dict[str, Any],
        retrieved_memories: list[str],
        human_feedback: list[str] | None = None,
    ) -> ExecutionPlan:
        msgs = [
            {"role": "system", "content": load_prompt("planner")},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "request": task,
                        "memories": retrieved_memories,
                        "human_feedback": human_feedback or [],
                    }
                ),
            },
        ]
        error = ""
        for _ in range(self._attempts):
            try:
                res = await self._llm.complete("planner", msgs, schema=ExecutionPlan)
                plan = res.parsed
                assert isinstance(plan, ExecutionPlan)
                validate_plan(plan)
                return plan
            except (PlanValidationError, SchemaValidationError, ValidationError) as exc:
                error = str(exc)
                msgs = [
                    *msgs,
                    {"role": "user", "content": f"That plan was rejected: {error}. Fix it."},
                ]
        raise PlanValidationError(error)

    async def synthesize(
        self, results: dict[str, SpecialistResult], verdicts: dict[str, ReviewVerdict]
    ) -> ReviewReport:
        findings = [f for r in results.values() for f in r.findings]
        fixes = [fx for r in results.values() for fx in r.output.get("fixes", [])]
        payload = {
            "results": {
                k: {"output": r.output, "findings": [f.model_dump() for f in r.findings]}
                for k, r in results.items()
            }
        }
        msgs = [
            {"role": "system", "content": load_prompt("synthesizer")},
            {"role": "user", "content": json.dumps(payload)},
        ]
        res = await self._llm.complete("planner", msgs, schema=ReviewReport)
        assert isinstance(res.parsed, ReviewReport)
        # Findings and fixes come from verified results, not from the model's summary.
        return ReviewReport(summary=res.parsed.summary, findings=findings, fixes=fixes)
