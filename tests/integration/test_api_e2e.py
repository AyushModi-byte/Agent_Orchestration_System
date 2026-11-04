"""End-to-end: API -> Celery (eager) -> graph with FakeLLM -> DB, then read back over HTTP."""

from __future__ import annotations

import fakeredis
import pytest
from fastapi.testclient import TestClient
from tests.helpers import Behavior, fake_llm, make_registry, plan_json, step

from conductor.api.app import create_app
from conductor.db.models import LLMCall, TaskEvent
from conductor.db.session import init_db, make_engine, make_session_factory
from conductor.memory.working import WorkingMemory
from conductor.worker.celery_app import celery, run_task, set_runtime
from conductor.worker.runner import Runtime


@pytest.fixture
def env():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    sf = make_session_factory(engine)
    wm = WorkingMemory(fakeredis.FakeRedis(), ttl_seconds=60)
    plan = plan_json(
        [
            step("s1", "code_analysis"),
            step("s2", "security"),
            step("s3", "test_execution", ["s1", "s2"]),
        ]
    )
    set_runtime(
        Runtime(session_factory=sf, llm=fake_llm(Behavior(plan)), registry=make_registry(), wm=wm)
    )
    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = True
    yield sf, wm
    set_runtime(None)
    celery.conf.task_always_eager = False


def test_task_runs_end_to_end_and_persists(env) -> None:  # type: ignore[no-untyped-def]
    sf, wm = env
    client = TestClient(create_app(sf, lambda tid: run_task.delay(tid)))
    r = client.post("/tasks", json={"request": "Review PR 1", "repo": "o/r", "pr_number": 1})
    assert r.status_code == 202
    tid = r.json()["id"]

    body = client.get(f"/tasks/{tid}").json()
    assert body["status"] == "completed"
    assert body["report"]["summary"] == "all good"
    assert {s["id"]: s["status"] for s in body["steps"]} == {
        "s1": "approved",
        "s2": "approved",
        "s3": "approved",
    }

    with client.stream("GET", f"/tasks/{tid}/events") as sse:
        text = "".join(sse.iter_text())
    assert "event: plan" in text and "event: delivered" in text and "event: end" in text

    with sf() as s:
        assert s.query(TaskEvent).filter_by(task_id=tid).count() >= 5
        calls = s.query(LLMCall).filter_by(task_id=tid).all()
        assert calls and {c.role for c in calls} == {"planner", "specialist", "reviewer"}
    assert wm._r.keys("wm:*") == []  # noqa: SLF001


def test_unknown_task_is_404(env) -> None:  # type: ignore[no-untyped-def]
    sf, _ = env
    client = TestClient(create_app(sf, lambda tid: None))
    assert client.get("/tasks/nope").status_code == 404
