"""Memory dashboard payload and the user-data delete endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from conductor.memory.longterm import LongTermStore, MemoryItem

GROUPS = {
    ("task_memories", "plan"): "plans",
    ("task_memories", "worked"): "worked",
    ("task_memories", "failed"): "failed",
    ("user_prefs", "preference"): "preferences",
    ("repo_facts", "fact"): "facts",
}


class MemoryDashboard(BaseModel):
    user_id: str
    counts: dict[str, int]
    groups: dict[str, list[MemoryItem]]


def make_router(store: LongTermStore) -> APIRouter:
    router = APIRouter()

    @router.get("/memory")
    def dashboard(user_id: str) -> MemoryDashboard:
        groups: dict[str, list[MemoryItem]] = {g: [] for g in GROUPS.values()}
        for it in store.list_user(user_id):
            groups.setdefault(GROUPS.get((it.collection, it.type), "other"), []).append(it)
        for items in groups.values():
            items.sort(key=lambda i: i.importance, reverse=True)
        return MemoryDashboard(
            user_id=user_id, counts={g: len(v) for g, v in groups.items()}, groups=groups
        )

    @router.delete("/memory/{user_id}")
    def delete_user_memory(user_id: str) -> dict[str, int]:
        return {"deleted": store.delete_user(user_id)}

    return router
