"""Memory upkeep: consolidate near-duplicates, decay importance, expire stale items.

Run on a Celery beat schedule so memory stays small and relevant without any request-time cost.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from pydantic import BaseModel

from conductor.agents.prompts_loader import load_prompt
from conductor.llm.types import LLM
from conductor.memory.extraction import extract_and_store
from conductor.memory.longterm import COLLECTIONS, LongTermStore, MemoryItem, importance_score


class MergedMemory(BaseModel):
    text: str


class MemoryManager:
    def __init__(self, store: LongTermStore, llm: LLM) -> None:
        self._store = store
        self._llm = llm

    async def extract_and_store(self, state: dict[str, Any], user_id: str, repo: str) -> int:
        return await extract_and_store(self._llm, self._store, state, user_id, repo)

    async def consolidate(self, user_id: str | None = None) -> int:
        """Merge near-duplicates into one summary each; returns how many items were removed."""
        removed = 0
        cfg = self._store.cfg
        for coll in COLLECTIONS:
            groups: dict[tuple[str, str, str], list[MemoryItem]] = defaultdict(list)
            for it in self._store.list_items(coll, user_id):
                groups[(it.user_id, it.repo, it.type)].append(it)
            for (uid, repo, type_), items in groups.items():
                for cluster in self._clusters(coll, items, uid, repo, type_):
                    if len(cluster) < 2:
                        continue
                    res = await self._llm.complete(
                        "cheap",
                        [
                            {"role": "system", "content": load_prompt("memory_merge")},
                            {
                                "role": "user",
                                "content": json.dumps({"memories": [c.text for c in cluster]}),
                            },
                        ],
                        schema=MergedMemory,
                    )
                    assert isinstance(res.parsed, MergedMemory)
                    new_id = self._store.add(
                        coll,
                        res.parsed.text,
                        user_id=uid,
                        repo=repo,
                        type=type_,
                        feedback=sum(c.feedback for c in cluster) / len(cluster),
                        sources=[c.id for c in cluster],
                        access_count=sum(c.access_count for c in cluster),
                        importance=max(c.importance for c in cluster),
                    )
                    self._store.delete(coll, [c.id for c in cluster], merged_into=new_id)
                    removed += len(cluster) - 1
        _ = cfg
        return removed

    def _clusters(
        self, coll: str, items: list[MemoryItem], uid: str, repo: str, type_: str
    ) -> list[list[MemoryItem]]:
        """Union-find over pairs whose cosine similarity clears the configured threshold."""
        max_dist = 1 - self._store.cfg.consolidation_similarity
        by_id = {it.id: it for it in items}
        parent = {i: i for i in by_id}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for it in items:
            hits = self._store.query(
                coll,
                it.text,
                k=len(items),
                user_id=uid,
                repo=repo or None,
                type=type_,
                max_distance=max_dist,
            )
            for other, _ in hits:
                if other.id in by_id and other.id != it.id:
                    parent[find(other.id)] = find(it.id)
        clusters: dict[str, list[MemoryItem]] = defaultdict(list)
        for i, it in by_id.items():
            clusters[find(i)].append(it)
        return list(clusters.values())

    def expire(self) -> int:
        """Re-score every item with time decay and delete those below the threshold."""
        now, cfg = self._store.now(), self._store.cfg
        deleted = 0
        for coll in COLLECTIONS:
            for it in self._store.list_items(coll):
                score = importance_score(it, now, cfg)
                if score < cfg.expiry_threshold:
                    self._store.delete(coll, [it.id])
                    deleted += 1
                else:
                    self._store.set_importance(it, score)
        return deleted
