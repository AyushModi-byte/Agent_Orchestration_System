"""Reviewer gate. Deterministic evidence checks run first and cost no quota; the LLM only judges
what code cannot (do the claims follow from the evidence?)."""

from __future__ import annotations

import json

from conductor.agents.prompts_loader import load_prompt
from conductor.agents.schemas import (
    Finding,
    ReviewJudgement,
    ReviewVerdict,
    SpecialistResult,
    Subtask,
    ToolTrace,
)
from conductor.llm.types import LLM
from conductor.obs.tracing import agent_span


def _cites_real_output(f: Finding, traces: list[ToolTrace]) -> bool:
    for t in traces:
        if not t.ok:
            continue
        if (f.file, f.line) in {(a, b) for a, b in t.locations}:
            return True
        # get_file output has no per-line records; accept lines inside the file that was read
        lines = t.metrics.get("lines")
        if t.tool == "get_file" and lines and t.args.get("path") == f.file and 1 <= f.line <= lines:
            return True
    return False


def _tests_pass(traces: list[ToolTrace]) -> bool:
    runs = [t for t in traces if t.tool == "run_tests" and t.ok]
    last = runs[-1].metrics if runs else None
    return (
        bool(last)
        and last is not None
        and last.get("failed", 1) == 0
        and last.get("errors", 1) == 0
    )


def evidence_issues(subtask: Subtask, result: SpecialistResult) -> list[str]:
    issues = []
    for f in result.findings:
        if not f.evidence.strip():
            issues.append(f"finding {f.file}:{f.line} has no evidence")
        if not _cites_real_output(f, result.tool_summary):
            issues.append(f"finding {f.file}:{f.line} does not match any tool output")
    if subtask.specialist == "fix_writer" and result.output.get("fixes"):
        if not _tests_pass(result.tool_summary):
            issues.append("proposed fixes have no passing test run")
    return issues


class Reviewer:
    def __init__(self, llm: LLM, min_score: float) -> None:
        self._llm = llm
        self._min = min_score

    async def review(self, subtask: Subtask, result: SpecialistResult) -> ReviewVerdict:
        with agent_span("reviewer.evaluate", "reviewer", subtask_id=subtask.id) as sp:
            verdict = await self._review(subtask, result)
            sp.set_attribute("approved", verdict.approved)
            sp.set_attribute("score", verdict.score)
            sp.set_attribute("issues", len(verdict.issues))
            return verdict

    async def _review(self, subtask: Subtask, result: SpecialistResult) -> ReviewVerdict:
        issues = evidence_issues(subtask, result)
        if issues:  # no point spending a model call on a result that already fails the rules
            return ReviewVerdict(
                subtask_id=subtask.id,
                run_id=result.run_id,
                approved=False,
                score=0.0,
                issues=issues,
                feedback="Fix these evidence problems: " + "; ".join(issues),
            )
        msgs = [
            {"role": "system", "content": load_prompt("reviewer")},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "subtask": subtask.model_dump(),
                        # run_id is random; leaving it out keeps identical work cacheable
                        "result": result.model_dump(exclude={"run_id"}),
                    },
                    default=str,
                ),
            },
        ]
        res = await self._llm.complete("reviewer", msgs, schema=ReviewJudgement)
        j = res.parsed
        assert isinstance(j, ReviewJudgement)
        return ReviewVerdict(
            subtask_id=subtask.id,
            run_id=result.run_id,
            approved=j.approved and j.score >= self._min,
            score=j.score,
            issues=j.issues,
            feedback=j.feedback,
        )
