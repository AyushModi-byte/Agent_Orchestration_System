"""Each trigger fires on exactly its condition; levels map to the decisions they permit."""

from __future__ import annotations

from typing import Any

import pytest

from conductor.config import load_policies
from conductor.hitl.levels import Level, allowed_decisions, highest, pauses
from conductor.hitl.queue import Decision
from conductor.hitl.triggers import evaluate

POL = load_policies()


def names(state: dict[str, Any], stage: str, action: dict[str, Any] | None = None) -> set[str]:
    return {t.name for t in evaluate(state, POL, stage, action)}


def plan(conf: float) -> dict[str, Any]:
    return {"steps": [], "confidence": conf}


def test_low_plan_confidence() -> None:
    assert "low_plan_confidence" in names({"request": {}, "plan": plan(0.3)}, "plan")
    assert names({"request": {}, "plan": plan(0.9)}, "plan") == set()


def test_invalid_plan() -> None:
    assert names({"request": {}, "plan": None, "plan_error": "cycle"}, "plan") == {"invalid_plan"}


def test_user_requested_review() -> None:
    state = {"request": {"review_required": True}, "plan": plan(0.9)}
    assert names(state, "plan") == {"user_requested"}


def test_production_label_requires_plan_approval() -> None:
    ts = evaluate({"request": {"labels": ["production"]}, "plan": plan(0.9)}, POL, "plan")
    assert [(t.name, t.level) for t in ts] == [("production_repo", Level.APPROVE_PLAN)]
    assert names({"request": {"labels": ["docs"]}, "plan": plan(0.9)}, "plan") == set()


def test_repeated_failure_needs_two_failures() -> None:
    assert names({"request": {}, "fail_count": {"s1": 1}}, "work") == set()
    ts = evaluate({"request": {}, "fail_count": {"s1": 2}}, POL, "work")
    assert [(t.name, t.level, t.subtask_id) for t in ts] == [
        ("repeated_failure", Level.TAKE_OVER, "s1")
    ]


def test_low_reviewer_score_only_when_not_approved() -> None:
    low = {"s1": {"approved": False, "score": 0.2}}
    ok = {"s1": {"approved": True, "score": 0.9}}
    assert names({"request": {}, "verdicts": low}, "work") == {"low_reviewer_score"}
    assert names({"request": {}, "verdicts": ok}, "work") == set()


def test_merge_request_any_phrasing() -> None:
    for text in ("please merge this PR", "Merge #4 when green"):
        assert names({"request": {"request": text}}, "intake") == {"merge_request"}
    assert names({"request": {"request": "review the merger of modules"}}, "intake") == set()
    assert evaluate({"request": {"merge": True}}, POL, "intake")[0].level is Level.TAKE_OVER


@pytest.mark.parametrize(
    ("tool", "labels", "fires"),
    [
        ("push_fix_commit", [], True),  # in the sensitive list
        ("delete_branch", [], True),  # deleting data
        ("send_email", [], True),  # contacting externally
        ("post_review_comment", [], False),  # fine on a normal repo
        ("post_review_comment", ["production"], True),  # sensitive on a production repo
        ("get_file", [], False),
    ],
)
def test_sensitive_operation(tool: str, labels: list[str], fires: bool) -> None:
    state = {"request": {"labels": labels}}
    assert ("sensitive_operation" in names(state, "action", {"tool": tool})) is fires


def test_triggers_only_fire_at_their_stage() -> None:
    state = {"request": {"request": "merge it", "review_required": True}, "plan": plan(0.1)}
    assert names(state, "work") == set()
    assert names(state, "action") == set()


def test_levels_define_allowed_decisions() -> None:
    assert allowed_decisions(Level.NOTIFY) == []  # nothing to decide: work proceeds
    assert not pauses(Level.NOTIFY) and pauses(Level.APPROVE_ACTION)
    assert set(allowed_decisions(Level.APPROVE_PLAN)) == {
        "approve",
        "modify",
        "reject",
        "take_over",
    }
    # take_over stands the agents down, so "approve" (continue) is not on offer
    assert allowed_decisions(Level.TAKE_OVER) == ["take_over", "reject"]
    assert highest([Level.NOTIFY, Level.TAKE_OVER, Level.APPROVE_PLAN]) is Level.TAKE_OVER
    assert highest([]) is Level.NOTIFY


def test_decision_validation() -> None:
    with pytest.raises(ValueError):
        Decision(type="reject")  # reason required
    with pytest.raises(ValueError):
        Decision(type="modify")
    with pytest.raises(ValueError):
        Decision(type="take_over")
    assert Decision(type="reject", reason="too broad").reason == "too broad"
