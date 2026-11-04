"""ChromaDB-backed long-term memory (local embeddings, no API quota; ADR-002).

Chroma holds the searchable text and metadata; the memory_meta table mirrors every item so the
dashboard, provenance of merged memories and hard deletes work with plain SQL.
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from conductor.config import MemoryConfig, load_memory_config
from conductor.db.models import MemoryMeta

COLLECTIONS = ("task_memories", "repo_facts", "user_prefs")


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryItem(BaseModel):
    id: str
    collection: str
    text: str
    user_id: str
    repo: str = ""
    type: str
    created_at: datetime
    last_accessed: datetime
    importance: float
    access_count: int = 0
    feedback: float = 0.0  # -1 human rejected .. +1 human accepted
    sources: list[str] = []


def importance_score(item: MemoryItem, now: datetime, cfg: MemoryConfig) -> float:
    """Blend of how often it is used, how recently, and what humans said about it."""
    access = 1 - math.exp(-item.access_count / cfg.access_scale)
    age_days = max((now - item.last_accessed).total_seconds() / 86400, 0.0)
    recency = 0.5 ** (age_days / cfg.half_life_days)
    feedback = (item.feedback + 1) / 2
    return float(0.4 * access + 0.35 * recency + 0.25 * feedback)


def make_chroma_client() -> ClientAPI:
    host = os.environ.get("CHROMA_HOST")
    if host:
        return chromadb.HttpClient(host=host, port=int(os.environ.get("CHROMA_PORT", "8000")))
    return chromadb.PersistentClient(path=os.environ.get("CHROMA_PATH", ".cache/chroma"))


def _where(**conds: str | None) -> dict[str, Any] | None:
    clauses = [{k: v} for k, v in conds.items() if v is not None]
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class LongTermStore:
    def __init__(
        self,
        client: ClientAPI,
        session_factory: sessionmaker[Session],
        embedding_function: Any = None,
        config: MemoryConfig | None = None,
        prefix: str = "",
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.cfg = config or load_memory_config()
        self.now = now
        self._sf = session_factory
        kwargs: dict[str, Any] = {"metadata": {"hnsw:space": "cosine"}}
        if embedding_function is not None:
            kwargs["embedding_function"] = embedding_function
        self._cols = {
            c: client.get_or_create_collection(f"{prefix}{c}", **kwargs) for c in COLLECTIONS
        }

    # -- writes ---------------------------------------------------------------------------
    def add(
        self,
        collection: str,
        text: str,
        *,
        user_id: str,
        type: str,
        repo: str = "",
        feedback: float = 0.0,
        sources: list[str] | None = None,
        access_count: int = 0,
        importance: float | None = None,
    ) -> str:
        now = self.now()
        item = MemoryItem(
            id=uuid.uuid4().hex,
            collection=collection,
            text=text,
            user_id=user_id,
            repo=repo,
            type=type,
            created_at=now,
            last_accessed=now,
            importance=0.0,
            access_count=access_count,
            feedback=feedback,
            sources=sources or [],
        )
        item.importance = (
            importance if importance is not None else importance_score(item, now, self.cfg)
        )
        self._cols[collection].add(
            ids=[item.id], documents=[text], metadatas=[self._chroma_meta(item)]
        )
        with self._sf() as s:
            s.add(
                MemoryMeta(
                    id=item.id,
                    collection=collection,
                    user_id=user_id,
                    repo=repo,
                    type=type,
                    text=text,
                    importance=item.importance,
                    access_count=access_count,
                    feedback=feedback,
                    sources=item.sources,
                    created_at=now,
                    last_accessed=now,
                )
            )
            s.commit()
        return item.id

    def touch(self, items: list[MemoryItem]) -> None:
        """Record a use: bump access_count/last_accessed and refresh importance."""
        now = self.now()
        for it in items:
            it.access_count += 1
            it.last_accessed = now
            it.importance = importance_score(it, now, self.cfg)
            self._write_meta(it)

    def get_item(self, collection: str, id_: str) -> MemoryItem | None:
        res = self._cols[collection].get(ids=[id_], include=["documents", "metadatas"])
        if not res["ids"]:
            return None
        return self._item(
            collection, id_, (res["documents"] or [""])[0], (res["metadatas"] or [{}])[0]
        )

    def apply_feedback(self, collection: str, id_: str, delta: float) -> None:
        """Nudge human-feedback weight (clamped to [-1, 1]) and refresh importance."""
        it = self.get_item(collection, id_)
        if it is None:
            return
        it.feedback = max(-1.0, min(1.0, it.feedback + delta))
        it.importance = importance_score(it, self.now(), self.cfg)
        self._write_meta(it)
        with self._sf() as s:
            row = s.get(MemoryMeta, id_)
            if row is not None:
                row.feedback = it.feedback
                s.commit()

    def set_importance(self, item: MemoryItem, value: float) -> None:
        item.importance = value
        self._write_meta(item)

    def delete(self, collection: str, ids: list[str], merged_into: str | None = None) -> None:
        """Remove from search. Merged-away rows stay in SQL (archived) as provenance."""
        if not ids:
            return
        self._cols[collection].delete(ids=ids)
        with self._sf() as s:
            for row in s.query(MemoryMeta).filter(MemoryMeta.id.in_(ids)):
                if merged_into:
                    row.archived = True
                    row.merged_into = merged_into
                else:
                    s.delete(row)
            s.commit()

    def delete_user(self, user_id: str) -> int:
        """Hard delete everything for a user, archived provenance included."""
        n = 0
        for col in self._cols.values():
            ids = col.get(where={"user_id": user_id}, include=[])["ids"]
            if ids:
                col.delete(ids=ids)
                n += len(ids)
        with self._sf() as s:
            s.query(MemoryMeta).filter(MemoryMeta.user_id == user_id).delete()
            s.commit()
        return n

    # -- reads ----------------------------------------------------------------------------
    def query(
        self,
        collection: str,
        text: str,
        *,
        k: int,
        user_id: str | None = None,
        repo: str | None = None,
        type: str | None = None,
        max_distance: float | None = None,
    ) -> list[tuple[MemoryItem, float]]:
        col = self._cols[collection]
        if col.count() == 0:
            return []
        res = self._search(col, text, k, _where(user_id=user_id, repo=repo, type=type))
        if res is None:
            return []
        out = []
        for i, doc, meta, dist in zip(
            res["ids"][0],
            (res["documents"] or [[]])[0],
            (res["metadatas"] or [[]])[0],
            (res["distances"] or [[]])[0],
            strict=True,
        ):
            if max_distance is None or dist <= max_distance:
                out.append((self._item(collection, i, doc, meta), dist))
        return out

    @staticmethod
    def _search(col: Any, text: str, k: int, where: dict[str, Any] | None) -> Any:
        """Chroma's index can transiently raise "Error finding id" right after writes. Recall is
        advisory, so retry once and then answer "nothing remembered" rather than fail the run."""
        for attempt in range(2):
            try:
                return col.query(
                    query_texts=[text],
                    n_results=min(k, col.count()),
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception:
                if attempt == 1:
                    return None
                time.sleep(0.05)

    def list_items(self, collection: str, user_id: str | None = None) -> list[MemoryItem]:
        res = self._cols[collection].get(
            where=_where(user_id=user_id), include=["documents", "metadatas"]
        )
        return [
            self._item(collection, i, d, m)
            for i, d, m in zip(
                res["ids"], res["documents"] or [], res["metadatas"] or [], strict=True
            )
        ]

    def list_user(self, user_id: str) -> list[MemoryItem]:
        return [it for c in COLLECTIONS for it in self.list_items(c, user_id)]

    def count(self, collection: str) -> int:
        return self._cols[collection].count()

    # -- internals ------------------------------------------------------------------------
    def _chroma_meta(self, it: MemoryItem) -> dict[str, str | int | float | bool]:
        return {
            "user_id": it.user_id,
            "repo": it.repo,
            "type": it.type,
            "created_at": it.created_at.isoformat(),
            "importance": it.importance,
            "access_count": it.access_count,
            "last_accessed": it.last_accessed.isoformat(),
            "feedback": it.feedback,
            "sources": json.dumps(it.sources),
        }

    def _write_meta(self, it: MemoryItem) -> None:
        self._cols[it.collection].update(ids=[it.id], metadatas=[self._chroma_meta(it)])
        with self._sf() as s:
            row = s.get(MemoryMeta, it.id)
            if row is not None:
                row.importance = it.importance
                row.access_count = it.access_count
                row.last_accessed = it.last_accessed
                s.commit()

    @staticmethod
    def _item(collection: str, id_: str, doc: str, m: Any) -> MemoryItem:
        return MemoryItem(
            id=id_,
            collection=collection,
            text=doc,
            user_id=str(m["user_id"]),
            repo=str(m.get("repo", "")),
            type=str(m["type"]),
            created_at=datetime.fromisoformat(str(m["created_at"])),
            last_accessed=datetime.fromisoformat(str(m["last_accessed"])),
            importance=float(m["importance"]),
            access_count=int(m["access_count"]),
            feedback=float(m.get("feedback", 0.0)),
            sources=json.loads(str(m.get("sources", "[]"))),
        )
