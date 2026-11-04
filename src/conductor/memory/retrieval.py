"""Retrieval before planning: what do we already know that is relevant to this task?"""

from __future__ import annotations

from pydantic import BaseModel

from conductor.memory.longterm import LongTermStore, MemoryItem
from conductor.obs.tracing import agent_span


class RetrievedMemories(BaseModel):
    similar_plans: list[MemoryItem] = []
    worked: list[MemoryItem] = []
    failed: list[MemoryItem] = []
    preferences: list[MemoryItem] = []
    facts: list[MemoryItem] = []

    def all_items(self) -> list[MemoryItem]:
        return [
            *self.similar_plans,
            *self.worked,
            *self.failed,
            *self.preferences,
            *self.facts,
        ]

    def format(self) -> str:
        """Prompt section for the planner; empty string when nothing relevant exists."""
        groups = [
            ("Similar past plans", self.similar_plans),
            ("What worked before", self.worked),
            ("What failed before", self.failed),
            ("User preferences", self.preferences),
            ("Repository facts", self.facts),
        ]
        lines = []
        for title, items in groups:
            if items:
                lines.append(f"### {title}")
                lines.extend(f"- {it.text}" for it in items)
        if not lines:
            return ""
        return "## Lessons from past runs\n" + "\n".join(lines)


class Retriever:
    def __init__(self, store: LongTermStore) -> None:
        self._store = store

    def retrieve(self, task: str, repo: str, user_id: str) -> RetrievedMemories:
        with agent_span("memory.retrieve", "memory") as sp:
            got = self._retrieve(task, repo, user_id)
            items = got.all_items()
            sp.set_attribute("memory_ids", [it.id for it in items])
            sp.set_attribute("count", len(items))
            return got

    def _retrieve(self, task: str, repo: str, user_id: str) -> RetrievedMemories:
        s, cfg = self._store, self._store.cfg

        def q(coll: str, cutoff: bool = True, **filters: str | None) -> list[MemoryItem]:
            dist = cfg.max_distance if cutoff else None
            return [it for it, _ in s.query(coll, task, k=cfg.top_k, max_distance=dist, **filters)]

        got = RetrievedMemories(
            similar_plans=q("task_memories", user_id=user_id, type="plan"),
            worked=q("task_memories", user_id=user_id, type="worked"),
            failed=q("task_memories", user_id=user_id, type="failed"),
            # Preferences apply to any task ("terse comments"), so no topical distance cutoff.
            preferences=q("user_prefs", cutoff=False, user_id=user_id),
            facts=q("repo_facts", repo=repo) if repo else [],
        )
        s.touch(got.all_items())
        return got
