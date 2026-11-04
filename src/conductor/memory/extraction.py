"""Post-task extraction: a cheap-role model turns a finished run into storable lessons."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from conductor.agents.prompts_loader import load_prompt
from conductor.llm.types import LLM
from conductor.memory.longterm import LongTermStore


class FixFeedback(BaseModel):
    fix: str
    accepted: bool
    reason: str = ""


class ExtractedMemories(BaseModel):
    what_asked: str
    plan_summary: str = ""
    worked: list[str] = []
    failed: list[str] = []
    tools_used: list[str] = []
    repo_facts: list[str] = []
    preferences: list[str] = []
    fix_feedback: list[FixFeedback] = []


def run_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Compact view of a finished graph state; keeps the extraction prompt small."""
    plan = state.get("plan") or {}
    results = state.get("results", {})
    return {
        "request": state.get("request"),
        "status": state.get("status"),
        "escalation_reason": state.get("escalation_reason"),
        "plan": [
            {
                "id": s["id"],
                "specialist": s["specialist"],
                "description": s["description"],
                "retries": state.get("fail_count", {}).get(s["id"], 0),
                "rejections": state.get("reject_count", {}).get(s["id"], 0),
            }
            for s in plan.get("steps", [])
        ],
        "tools_used": sorted(
            {t["tool"] for r in results.values() for t in r.get("tool_summary", [])}
        ),
        "findings": sum(len(r.get("findings", [])) for r in results.values()),
        "human_feedback": state.get("human_feedback", []),
    }


async def extract_and_store(
    llm: LLM, store: LongTermStore, state: dict[str, Any], user_id: str, repo: str
) -> int:
    msgs = [
        {"role": "system", "content": load_prompt("memory_extract")},
        {"role": "user", "content": json.dumps(run_summary(state), default=str)},
    ]
    res = await llm.complete("cheap", msgs, schema=ExtractedMemories)
    ex = res.parsed
    assert isinstance(ex, ExtractedMemories)
    asked = ex.what_asked
    n = 0

    def add(coll: str, text: str, type_: str, feedback: float = 0.0) -> None:
        nonlocal n
        store.add(coll, text, user_id=user_id, repo=repo, type=type_, feedback=feedback)
        n += 1

    if ex.plan_summary:
        add("task_memories", f"{asked}. Plan: {ex.plan_summary}", "plan")
    for w in ex.worked:
        add("task_memories", f"{asked}. Worked: {w}", "worked")
    for f in ex.failed:
        add("task_memories", f"{asked}. Failed: {f}", "failed")
    for fb in ex.fix_feedback:
        verdict = "accepted" if fb.accepted else "rejected"
        add(
            "task_memories",
            f"{asked}. Human {verdict} fix: {fb.fix}. {fb.reason}".strip(),
            "worked" if fb.accepted else "failed",
            feedback=1.0 if fb.accepted else -1.0,
        )
    for fact in ex.repo_facts:
        add("repo_facts", fact, "fact")
    for pref in ex.preferences:
        add("user_prefs", pref, "preference")
    return n
