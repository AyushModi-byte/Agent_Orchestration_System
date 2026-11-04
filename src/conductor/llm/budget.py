"""Persisted daily live-call counter so a crash or restart cannot reset the quota guard."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from conductor.llm.types import BudgetExceeded


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


class BudgetGuard:
    def __init__(
        self,
        path: Path | str,
        caps: dict[str, int],
        today: Callable[[], str] = _today,
    ) -> None:
        self._caps = caps
        self._today = today
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS budget (day TEXT, provider TEXT, n INTEGER,"
            " PRIMARY KEY (day, provider))"
        )
        self._db.commit()

    def used(self, provider: str) -> int:
        row = self._db.execute(
            "SELECT n FROM budget WHERE day = ? AND provider = ?", (self._today(), provider)
        ).fetchone()
        return int(row[0]) if row else 0

    def consume(self, provider: str) -> None:
        """Count one live call, or raise before it is made if the cap is reached."""
        cap = self._caps.get(provider)
        if cap is not None and self.used(provider) >= cap:
            raise BudgetExceeded(f"{provider}: daily live-call budget of {cap} reached")
        self._db.execute(
            "INSERT INTO budget (day, provider, n) VALUES (?, ?, 1)"
            " ON CONFLICT(day, provider) DO UPDATE SET n = n + 1",
            (self._today(), provider),
        )
        self._db.commit()
