"""Long-term memory: retrieval, importance, consolidation, expiry, hard delete. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.helpers import make_store

from conductor.api.routes.memory import make_router
from conductor.db.models import MemoryMeta
from conductor.llm.fake import FakeLLM
from conductor.memory.manager import MemoryManager
from conductor.memory.retrieval import Retriever

T0 = datetime(2026, 1, 1, tzinfo=UTC)
Q = "review auth sql injection"


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t


def test_retrieval_groups_formats_and_ignores_other_users() -> None:
    s = make_store()
    s.add("task_memories", f"{Q}. Plan: scan then fix", user_id="u1", type="plan")
    s.add("task_memories", f"{Q}. Worked: bandit found it", user_id="u1", type="worked")
    s.add("task_memories", f"{Q}. Failed: ruff missed it", user_id="u1", type="failed")
    s.add("user_prefs", "prefers terse review comments about auth", user_id="u1", type="preference")
    s.add(
        "repo_facts", "auth module uses sqlalchemy raw sql", user_id="u1", repo="o/r", type="fact"
    )
    s.add("task_memories", f"{Q}. Worked: secret of u2", user_id="u2", type="worked")
    got = Retriever(s).retrieve("review auth module for sql injection", "o/r", "u1")
    assert len(got.similar_plans) == len(got.worked) == len(got.failed) == 1
    assert got.preferences and got.facts
    text = got.format()
    assert "What worked before" in text and "bandit found it" in text
    assert "secret of u2" not in text


def test_unrelated_task_retrieves_nothing() -> None:
    s = make_store()
    s.add("task_memories", "alpha beta gamma", user_id="u1", type="plan")
    assert Retriever(s).retrieve("zebra yak xylophone", "", "u1").format() == ""


def test_importance_rises_with_access() -> None:
    s = make_store(Clock())
    s.add("task_memories", Q, user_id="u1", type="worked")
    before = s.list_user("u1")[0]
    r = Retriever(s)
    for _ in range(5):
        r.retrieve(Q, "", "u1")
    after = s.list_user("u1")[0]
    assert after.access_count == 5 and after.importance > before.importance
    with s._sf() as db:  # noqa: SLF001  the SQL mirror stays in sync
        row = db.get(MemoryMeta, after.id)
        assert row is not None and row.access_count == 5


def test_human_feedback_weights_importance() -> None:
    s = make_store()
    a = s.add("task_memories", "x one", user_id="u", type="worked", feedback=1.0)
    b = s.add("task_memories", "x two", user_id="u", type="failed", feedback=-1.0)
    imp = {i.id: i.importance for i in s.list_user("u")}
    assert imp[a] > imp[b]


async def test_consolidation_merges_duplicates_and_keeps_provenance() -> None:
    s = make_store()
    ids = [
        s.add("task_memories", "auth sql injection fix", user_id="u1", type="worked")
        for _ in range(3)
    ]
    keep = s.add(
        "task_memories", "completely different docker compose topic", user_id="u1", type="worked"
    )
    llm = FakeLLM({"cheap": [json.dumps({"text": "merged auth sql injection fix"})]})
    removed = await MemoryManager(s, llm).consolidate()
    assert removed == 2 and s.count("task_memories") == 2
    live = {i.id: i for i in s.list_user("u1")}
    merged = next(i for i in live.values() if i.text.startswith("merged"))
    assert set(merged.sources) == set(ids) and keep in live
    with s._sf() as db:  # noqa: SLF001
        rows = db.query(MemoryMeta).filter(MemoryMeta.id.in_(ids)).all()
        assert len(rows) == 3
        assert all(r.archived and r.merged_into == merged.id for r in rows)


def test_expiry_removes_stale_keeps_used_and_fresh() -> None:
    clock = Clock()
    s = make_store(clock)
    stale = s.add("task_memories", "old unused lesson", user_id="u", type="worked")
    used = s.add(
        "task_memories", "old heavily used lesson", user_id="u", type="worked", access_count=30
    )
    clock.t = T0 + timedelta(days=365)
    fresh = s.add("task_memories", "brand new lesson", user_id="u", type="worked")
    deleted = MemoryManager(s, FakeLLM()).expire()
    ids = {i.id for i in s.list_user("u")}
    assert deleted == 1 and stale not in ids and used in ids and fresh in ids


def test_delete_endpoint_leaves_nothing_for_user() -> None:
    s = make_store()
    for coll, t in (
        ("task_memories", "worked"),
        ("user_prefs", "preference"),
        ("repo_facts", "fact"),
    ):
        s.add(coll, "something about auth", user_id="gone", type=t, repo="o/r")
    s.add("task_memories", "other user note", user_id="stay", type="worked")
    app = FastAPI()
    app.include_router(make_router(s))
    c = TestClient(app)
    dash = c.get("/memory", params={"user_id": "gone"}).json()
    assert dash["counts"]["worked"] == 1 and dash["counts"]["preferences"] == 1
    assert c.delete("/memory/gone").json() == {"deleted": 3}
    assert s.list_user("gone") == []
    assert s.query("task_memories", "auth", k=5, user_id="gone") == []
    with s._sf() as db:  # noqa: SLF001
        assert db.query(MemoryMeta).filter_by(user_id="gone").count() == 0
        assert db.query(MemoryMeta).filter_by(user_id="stay").count() == 1
    assert len(s.list_user("stay")) == 1


def test_dashboard_requires_user_id() -> None:
    app = FastAPI()
    app.include_router(make_router(make_store()))
    assert TestClient(app).get("/memory").status_code == 422
