"""CONDUCTOR_FAKE_LLM=1: the scripted scenario runs end to end through the real HTTP API."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conductor.obs.tracing import NoopTracer, set_tracer


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from demo.runtime import build_demo_app

    yield TestClient(build_demo_app())
    set_tracer(NoopTracer())


def wait_for(client: TestClient, tid: str, statuses: set[str]) -> dict[str, Any]:
    for _ in range(100):
        t = client.get(f"/tasks/{tid}").json()
        if t["status"] in statuses:
            return dict(t)
        time.sleep(0.1)
    raise AssertionError(f"task stuck at {t['status']}")


def test_demo_walkthrough(client: TestClient) -> None:
    body = {
        "request": "Review PR #2 on acme/shop",
        "repo": "acme/shop",
        "pr_number": 2,
        "head_ref": "fix/sql",
        "user_id": "demo",
    }
    tid = client.post("/tasks", json=body).json()["id"]
    paused = wait_for(client, tid, {"awaiting_approval", "failed", "completed"})
    assert paused["status"] == "awaiting_approval", paused

    (appr,) = client.get("/approvals", params={"status": "pending"}).json()
    ctx = appr["context"]
    assert ctx["proposed_action"]["tool"] == "push_fix_commit"
    assert ctx["memories"] and ctx["similar_decisions"]  # memory recalled PR 1
    assert len(ctx["completed_steps"]) == 4

    chat = client.post(f"/approvals/{appr['id']}/chat", json={"question": "why this fix?"}).json()
    assert "Parameterised" in chat["answer"]

    assert (
        client.post(f"/approvals/{appr['id']}/decision", json={"type": "approve"}).status_code
        == 202
    )
    done = wait_for(client, tid, {"completed", "failed"})
    assert done["status"] == "completed", done

    tree = client.get(f"/traces/{tid}").json()
    flat: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            flat.append(n)
            walk(n["children"])

    walk(tree["roots"])
    reviews = [n for n in flat if n["name"] == "reviewer.evaluate"]
    assert any(not n["attributes"]["approved"] for n in reviews)  # the first fix was rejected
    assert tree["total_cost_usd"] > 0
    assert client.get("/memory", params={"user_id": "demo"}).json()["counts"]["worked"] >= 2
    assert client.get("/metrics/cost").json()["per_task_type"]

    # the explorer's drawer: full prompt/response for model calls, input/output for tools
    llm_node = next(n for n in flat if n["name"] == "llm.call")
    d = client.get(f"/traces/{tid}/spans/{llm_node['span_id']}").json()
    assert d["llm"]["messages"] and d["llm"]["response"]
    tool_node = next(n for n in flat if n["name"] == "tool.call")
    t = client.get(f"/traces/{tid}/spans/{tool_node['span_id']}").json()
    assert t["tool"]["tool"] == tool_node["attributes"]["tool"] and t["tool"]["outputs"]
    listed = client.get("/tasks").json()
    assert listed[0]["id"] == tid and listed[0]["request"]["repo"] == "acme/shop"
