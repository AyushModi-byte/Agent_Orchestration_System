"""Replay: unchanged replays are free and identical; an override diverges at exactly that step."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.helpers import Behavior, make_registry, plan_json, step
from tests.obs_helpers import PLAN, PRICES, Env, make_env, run_task, teardown

from conductor.api.app import create_app
from conductor.db.models import LLMCall, Replay
from conductor.hitl.queue import ApprovalQueue, Decision
from conductor.llm.fake import FakeLLM
from conductor.obs.replay import Override, ReplayService, replay_registry
from conductor.worker.runner import resume_task


@pytest.fixture
def env():  # type: ignore[no-untyped-def]
    e = make_env()
    yield e
    teardown()


def service(env: Env, live: Behavior | None = None) -> tuple[ReplayService, FakeLLM]:
    """`live` answers calls a replay cannot serve from the recording; None means any live call
    fails the test, which is how 'zero live calls' is enforced."""
    llm = FakeLLM(responder=live) if live else FakeLLM()
    return ReplayService(env.sf, llm, make_registry(), PRICES), llm


def llm_calls(env: Env, task_id: str) -> int:
    with env.sf() as s:
        return s.query(LLMCall).filter_by(task_id=task_id).count()


async def test_replay_without_changes_is_identical_and_free(env) -> None:  # type: ignore[no-untyped-def]
    tid = await run_task(env)
    svc, inner = service(env)
    rid = await svc.run(tid)

    assert inner.calls == []  # not a single call reached a model
    rep = svc.get(rid)
    assert rep is not None and rep.status == "completed"
    assert rep.live_calls == 0 and rep.cost_usd == 0
    d = svc.diff(rid)
    assert not d.diverged and not d.changed_steps and not d.added_steps and not d.removed_steps
    assert d.unchanged_steps == len(svc.steps(tid)) > 8  # every model and tool call matched
    assert d.cost["replay"] == 0 and d.cost["original"] > 0
    assert d.status == {"before": "completed", "after": "completed"} and not d.report_changed
    # the replay is a first-class run: it has its own rows and trace
    assert llm_calls(env, rid) == llm_calls(env, tid) - 1  # minus memory extraction
    client = TestClient(create_app(env.sf, lambda t: None))
    assert client.get(f"/traces/{rid}").json()["roots"][0]["name"] == "replay.run"


async def test_replay_with_changed_tool_result_diverges_at_that_step(env) -> None:  # type: ignore[no-untyped-def]
    tid = await run_task(env)
    svc, inner = service(env, live=Behavior(PLAN))
    steps = svc.steps(tid)
    ruff = next(s for s in steps if s.kind == "tool" and s.name == "run_ruff")
    changed_output = {**ruff.output, "note": "scanner upgraded"}  # same findings, new payload

    rid = await svc.run(tid, [Override(step=ruff.index, value=changed_output)])
    d = svc.diff(rid)
    assert d.diverged and d.overridden_steps == [ruff.index]
    # exactly the overridden step changed: later answers were recomputed but came out the same
    assert [c.identity for c in d.changed_steps] == ["tool:code_analysis:run_ruff:s1:0"]
    assert d.changed_steps[0].before == ruff.output
    assert d.changed_steps[0].after == changed_output
    assert not d.added_steps and not d.removed_steps
    assert d.unchanged_steps == len(steps) - 1
    # prompts downstream of the change were no longer on tape, so they ran live; the rest did not
    assert 0 < d.live_calls < len([s for s in steps if s.kind == "llm"])
    assert len(inner.calls) == d.live_calls
    assert d.status["after"] == "completed" and not d.report_changed
    assert d.cost["replay"] < d.cost["original"]  # unchanged prompts were free


async def test_replay_with_a_different_plan_reports_the_plan_step(env) -> None:  # type: ignore[no-untyped-def]
    tid = await run_task(env)
    smaller = plan_json([step("s1", "code_analysis"), step("s2", "security")])
    svc, _ = service(env, live=Behavior(smaller))
    planner = next(s for s in svc.steps(tid) if s.kind == "llm" and s.name == "planner")
    rid = await svc.run(tid, [Override(step=planner.index, value=json.loads(smaller))])
    d = svc.diff(rid)
    assert d.diverged
    assert [c.identity for c in d.changed_steps] == ["llm:supervisor:planner::0"]
    gone = {c.identity.split(":")[3] for c in d.removed_steps}
    assert gone == {"s3"}  # the dropped subtask's specialist, tool and review disappear
    assert not d.added_steps and d.live_calls >= 1


async def test_replay_stores_the_run_and_serves_it_over_http(env) -> None:  # type: ignore[no-untyped-def]
    tid = await run_task(env)
    svc, _ = service(env)
    client = TestClient(create_app(env.sf, lambda t: None, replay_service=svc))
    steps = client.get("/replays/steps", params={"task_id": tid}).json()
    assert steps[0]["kind"] == "llm" and steps[0]["name"] == "planner"
    r = client.post("/replays", json={"task_id": tid})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "completed" and body["live_calls"] == 0 and body["cost_usd"] == 0
    with env.sf() as s:
        assert s.get(Replay, body["id"]) is not None
    diff = client.get(f"/replays/{body['id']}/diff").json()
    assert diff["diverged"] is False and diff["source_task_id"] == tid
    assert client.get(f"/replays/{body['id']}").json()["id"] == body["id"]
    assert client.post("/replays", json={"task_id": "nope"}).status_code == 404
    bad = client.post("/replays", json={"task_id": tid, "overrides": [{"step": 999, "value": 1}]})
    assert bad.status_code == 422
    assert client.get("/replays/nope/diff").status_code == 404


async def test_replay_reapplies_recorded_human_decisions(env) -> None:  # type: ignore[no-untyped-def]
    tid = await run_task(env, review_required=True)
    with env.sf() as s:
        from conductor.db.models import Approval

        (appr,) = s.query(Approval).filter_by(task_id=tid).all()
        aid = appr.id
    ApprovalQueue(env.sf).decide(aid, Decision(type="approve"))
    await resume_task(tid, aid, env.runtime)

    svc, inner = service(env)
    rid = await svc.run(tid)  # hits the same gate and replays the human's approval
    d = svc.diff(rid)
    assert svc.get(rid).status == "completed"  # type: ignore[union-attr]
    assert inner.calls == [] and not d.diverged and d.status["after"] == "completed"


async def test_replay_never_writes_to_the_outside_world(env) -> None:  # type: ignore[no-untyped-def]
    reg = make_registry()
    args = {"repo": "o/r", "branch": "feat", "path": "a.py", "content": "x", "message": "m"}
    safe = replay_registry(reg, [], {}, env.registry.store, {"push_fix_commit"})
    out = await safe.invoke("fix_writer", "push_fix_commit", args)
    assert out.model_dump() == {"dry_run": True} and reg.calls == []  # type: ignore[attr-defined]


def test_override_helpers_reject_bad_steps(env) -> None:  # type: ignore[no-untyped-def]
    svc, _ = service(env)
    with pytest.raises(LookupError):
        svc.diff("missing")


def _unused(_: Any) -> None:  # keep Any imported for readers editing the helpers above
    return None
