"""Approvals over HTTP: pause, restart the worker, decide, resume; chat and memory feedback."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import fakeredis
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from tests.helpers import Behavior, make_registry, make_store, plan_json, step

from conductor.api.app import create_app
from conductor.db.models import Approval, ApprovalChat, Task, TaskEvent
from conductor.llm.fake import FakeLLM
from conductor.memory.working import WorkingMemory
from conductor.worker.celery_app import celery, resume_task, run_task, set_runtime
from conductor.worker.runner import Runtime

PLAN = plan_json([step("s1", "code_analysis"), step("s2", "security")])
SAVER = MemorySaver()  # durable across "worker restarts", like the Postgres checkpointer


@asynccontextmanager
async def shared_checkpointer() -> Any:
    yield SAVER


def new_runtime(store: Any, behavior: Behavior | None = None) -> Runtime:
    """A fresh worker: new LLM, registry and working memory, same DB and checkpoints."""
    llm = FakeLLM({"cheap": ["{}"] * 4}, responder=behavior or Behavior(PLAN))
    return Runtime(
        session_factory=store._sf,  # noqa: SLF001
        llm=llm,
        registry=make_registry(),
        wm=WorkingMemory(fakeredis.FakeRedis(), 60),
        memory_store=store,
        checkpointer=shared_checkpointer,
    )


@pytest.fixture
def env():  # type: ignore[no-untyped-def]
    store = make_store()
    set_runtime(new_runtime(store))
    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = True
    api_llm = FakeLLM({"cheap": ["The plan only touches code analysis and security."]})
    app = create_app(
        store._sf,  # noqa: SLF001
        lambda t: run_task.delay(t),
        memory_store=store,
        enqueue_resume=lambda t, a: resume_task.delay(t, a),
        llm=api_llm,
    )
    yield TestClient(app), store
    set_runtime(None)
    celery.conf.task_always_eager = False


def submit(client: TestClient, **extra: Any) -> str:
    body = {"request": "review auth module for sql injection", "repo": "o/r", "user_id": "u1"}
    return str(client.post("/tasks", json={**body, **extra}).json()["id"])


def pending(client: TestClient) -> list[dict[str, Any]]:
    return client.get("/approvals", params={"status": "pending"}).json()  # type: ignore[no-any-return]


def test_pause_restart_approve_resume(env) -> None:  # type: ignore[no-untyped-def]
    client, store = env
    tid = submit(client, review_required=True)
    assert client.get(f"/tasks/{tid}").json()["status"] == "awaiting_approval"
    (appr,) = pending(client)
    assert appr["kind"] == "plan" and appr["task_id"] == tid
    assert appr["context"]["plan"]["steps"] and "approve" in appr["context"]["allowed_decisions"]
    with store._sf() as s:  # noqa: SLF001
        types = [e.type for e in s.query(TaskEvent).filter_by(task_id=tid)]
        assert "approval_requested" in types

    set_runtime(new_runtime(store))  # the worker restarts; only the DB + checkpoints survive
    r = client.post(f"/approvals/{appr['id']}/decision", json={"type": "approve"})
    assert r.status_code == 202
    body = client.get(f"/tasks/{tid}").json()
    assert body["status"] == "completed" and body["report"]["summary"] == "all good"
    assert {s["status"] for s in body["steps"]} == {"approved"}
    assert pending(client) == []
    assert client.get(f"/approvals/{appr['id']}").json()["status"] == "resolved"
    again = client.post(f"/approvals/{appr['id']}/decision", json={"type": "approve"})
    assert again.status_code == 409
    with client.stream("GET", f"/tasks/{tid}/events") as sse:
        assert "event: approval_requested" in "".join(sse.iter_text())


def test_reject_replans_and_a_new_approval_appears(env) -> None:  # type: ignore[no-untyped-def]
    client, _ = env
    tid = submit(client, review_required=True)
    (first,) = pending(client)
    r = client.post(
        f"/approvals/{first['id']}/decision", json={"type": "reject", "reason": "include tests"}
    )
    assert r.status_code == 202
    (second,) = pending(client)
    assert second["id"] != first["id"]
    assert client.get(f"/tasks/{tid}").json()["status"] == "awaiting_approval"


def test_invalid_decisions_are_refused(env) -> None:  # type: ignore[no-untyped-def]
    client, _ = env
    tid = submit(client, request="please merge PR 9")
    (appr,) = pending(client)
    assert appr["kind"] == "takeover"
    post = lambda body: client.post(f"/approvals/{appr['id']}/decision", json=body)  # noqa: E731
    assert post({"type": "approve"}).status_code == 422  # take_over level: agents stand down
    assert post({"type": "take_over"}).status_code == 422  # output required
    assert post({"type": "bogus"}).status_code == 422
    assert client.post("/approvals/nope/decision", json={"type": "approve"}).status_code == 404
    assert post({"type": "take_over", "output": {"summary": "I merged it"}}).status_code == 202
    body = client.get(f"/tasks/{tid}").json()
    assert body["status"] == "taken_over" and body["report"]["human_output"]["summary"]


def test_chat_answers_without_changing_state(env) -> None:  # type: ignore[no-untyped-def]
    client, store = env
    tid = submit(client, review_required=True)
    (appr,) = pending(client)

    def snapshot() -> tuple[Any, ...]:
        with store._sf() as s:  # noqa: SLF001
            t, a = s.get(Task, tid), s.get(Approval, appr["id"])
            events = s.query(TaskEvent).filter_by(task_id=tid).count()
            return (t.status, t.report, a.status, a.decision, events)

    before = snapshot()
    r = client.post(f"/approvals/{appr['id']}/chat", json={"question": "what does the plan touch?"})
    assert r.status_code == 200
    assert "code analysis" in r.json()["answer"]
    assert [h["role"] for h in r.json()["history"]] == ["human", "assistant"]
    assert snapshot() == before  # task, approval and event log untouched
    assert pending(client)[0]["id"] == appr["id"]  # still waiting on the human
    with store._sf() as s:  # noqa: SLF001
        assert s.query(ApprovalChat).filter_by(approval_id=appr["id"]).count() == 2
    assert client.post("/approvals/nope/chat", json={"question": "hi"}).status_code == 404
    # the graph is still paused at the same checkpoint: deciding afterwards resumes normally
    client.post(f"/approvals/{appr['id']}/decision", json={"type": "approve"})
    assert client.get(f"/tasks/{tid}").json()["status"] == "completed"


def memory_feedback(store: Any, id_: str) -> float:
    return float(store.get_item("task_memories", id_).feedback)


@pytest.mark.parametrize(
    ("decision", "direction"),
    [({"type": "approve"}, 1), ({"type": "reject", "reason": "wrong approach"}, -1)],
)
def test_every_decision_becomes_memory_feedback(env, decision, direction) -> None:  # type: ignore[no-untyped-def]
    client, store = env
    used = store.add(
        "task_memories",
        "review auth module for sql injection. Worked: bandit first",
        user_id="u1",
        repo="o/r",
        type="worked",
    )
    submit(client, review_required=True)
    (appr,) = pending(client)
    assert ["task_memories", used] in appr["context"]["memory_refs"]
    imp_before = store.get_item("task_memories", used).importance
    client.post(f"/approvals/{appr['id']}/decision", json=decision)
    # the memory that informed the plan moves with the human's verdict ...
    assert (memory_feedback(store, used)) * direction > 0
    assert (store.get_item("task_memories", used).importance - imp_before) * direction > 0
    # ... and the decision itself is stored so future gates can cite it
    stored = [i for i in store.list_user("u1") if i.type == "decision"]
    assert len(stored) == 1 and stored[0].feedback * direction > 0
    assert decision["type"] in stored[0].text


def test_similar_past_decisions_appear_in_the_next_approval(env) -> None:  # type: ignore[no-untyped-def]
    client, _ = env
    submit(client, review_required=True)
    (first,) = pending(client)
    client.post(
        f"/approvals/{first['id']}/decision",
        json={"type": "reject", "reason": "scan the migrations too"},
    )
    (second,) = pending(client)
    texts = " ".join(second["context"]["similar_decisions"])
    assert "reject" in texts and "scan the migrations too" in texts
    json.dumps(second["context"])  # the packaged context is plain JSON
