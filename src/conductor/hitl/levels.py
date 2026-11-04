"""The four approval levels and what each lets a human do.

notify          proceed, and tell the human afterwards
approve_action  pause; the human confirms (or edits/rejects) the next step
approve_plan    pause before any work; the human reviews the whole plan
take_over       pause and stand the agents down; the human supplies the output
"""

from __future__ import annotations

from enum import StrEnum


class Level(StrEnum):
    NOTIFY = "notify"
    APPROVE_ACTION = "approve_action"
    APPROVE_PLAN = "approve_plan"
    TAKE_OVER = "take_over"


_RANK = {Level.NOTIFY: 0, Level.APPROVE_ACTION: 1, Level.APPROVE_PLAN: 2, Level.TAKE_OVER: 3}

ALL_DECISIONS = ("approve", "modify", "reject", "take_over")
_LEVEL_DECISIONS = {
    Level.APPROVE_ACTION: ALL_DECISIONS,
    Level.APPROVE_PLAN: ALL_DECISIONS,
    # Approving would mean letting agents continue, which is exactly what take_over forbids.
    Level.TAKE_OVER: ("take_over", "reject"),
    Level.NOTIFY: (),
}


def highest(levels: list[Level]) -> Level:
    return max(levels, key=lambda lv: _RANK[lv], default=Level.NOTIFY)


def pauses(level: Level) -> bool:
    """Only notify lets work continue without a human."""
    return level is not Level.NOTIFY


def allowed_decisions(level: Level, kind_allowed: tuple[str, ...] = ALL_DECISIONS) -> list[str]:
    return [d for d in _LEVEL_DECISIONS[level] if d in kind_allowed]
