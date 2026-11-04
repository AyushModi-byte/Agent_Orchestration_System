"""Second similar task gets the first task's lessons injected into the planning prompt."""

from __future__ import annotations

import json

import fakeredis
from fastapi.testclient import TestClient
from tests.helpers import Behavior, make_registry, make_store, plan_json, step

from conductor.api.app import create_app
from conductor.db.models import TaskEvent
from conductor.llm.fake import FakeLLM
from conductor.memory.working import WorkingMemory
from conductor.worker.celery_app import celery, run_task, set_runtime
from conductor.worker.runner import Runtime

EXTRACTION = json.dumps(
    {
        "what_asked": "review auth module for sql injection",
        "plan_summary": "run bandit then ruff on auth",
        "worked": ["bandit flagged the raw query quickly"],
        "failed": ["ruff alone missed the injection"],
        "tools_used": ["run_bandit"],
        "repo_facts": ["auth module builds raw sql strings"],
        "preferences": ["wants terse comments"],
        "fix_feedback": [{"fix": "parametrize query", "accepted": True, "reason": "clean"}],
    }
)


def test_second_task_receives_first_tasks_lessons() -> None:
    store = make_store()
    plan = plan_json([step("s1", "code_analysis"), step("s2", "security")])
    llm = FakeLLM({"cheap": [EXTRACTION, EXTRACTION]}, responder=Behavior(plan))
    sf = store._sf  # noqa: SLF001
    set_runtime(
        Runtime(
            session_factory=sf,
            llm=llm,
            registry=make_registry(),
            wm=WorkingMemory(fakeredis.FakeRedis(), 60),
            memory_store=store,
        )
    )
    celery.conf.task_always_eager = True
    celery.conf.task_eager_propagates = True
    try:
        client = TestClient(create_app(sf, lambda t: run_task.delay(t)))
        body = {"request": "review auth module for sql injection", "repo": "o/r", "user_id": "u1"}
        t1 = client.post("/tasks", json=body).json()["id"]
        assert store.list_user("u1"), "task 1 should have stored lessons"
        before = len([c for c in llm.calls if c[0] == "planner"])
        second = {**body, "request": "please review the auth module sql injection"}
        t2 = client.post("/tasks", json=second).json()["id"]
    finally:
        set_runtime(None)
        celery.conf.task_always_eager = False

    planner_calls = [c for c in llm.calls if c[0] == "planner"]
    second_prompt = planner_calls[before][1][1]["content"]
    assert "Lessons from past runs" in second_prompt
    assert "bandit flagged the raw query quickly" in second_prompt
    assert "terse comments" in second_prompt
    assert "Lessons from past runs" not in planner_calls[0][1][1]["content"]

    with sf() as s:
        types = {e.type for e in s.query(TaskEvent).filter_by(task_id=t1)}
        assert "memory_saved" in types
    assert client.get(f"/tasks/{t2}").json()["status"] == "completed"
