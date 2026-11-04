"""tool_calls rows. SQLite here; the Postgres-backed store replaces it in the db phase
behind the same two methods."""

from __future__ import annotations

import sqlite3
from typing import Protocol

from pydantic import BaseModel


class ToolCallRecord(BaseModel):
    agent: str
    tool: str
    inputs: str
    outputs: str | None
    latency_ms: float
    success: bool
    error: str | None = None


class ToolCallStore(Protocol):
    def record(self, rec: ToolCallRecord) -> None: ...
    def all(self) -> list[ToolCallRecord]: ...


class SqliteToolCallStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tool_calls (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " agent TEXT, tool TEXT, inputs TEXT, outputs TEXT, latency_ms REAL,"
            " success INTEGER, error TEXT)"
        )

    def record(self, rec: ToolCallRecord) -> None:
        self._db.execute(
            "INSERT INTO tool_calls (agent, tool, inputs, outputs, latency_ms, success, error)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rec.agent,
                rec.tool,
                rec.inputs,
                rec.outputs,
                rec.latency_ms,
                int(rec.success),
                rec.error,
            ),
        )
        self._db.commit()

    def all(self) -> list[ToolCallRecord]:
        rows = self._db.execute(
            "SELECT agent, tool, inputs, outputs, latency_ms, success, error FROM tool_calls"
            " ORDER BY id"
        ).fetchall()
        return [
            ToolCallRecord(
                agent=r[0],
                tool=r[1],
                inputs=r[2],
                outputs=r[3],
                latency_ms=r[4],
                success=bool(r[5]),
                error=r[6],
            )
            for r in rows
        ]
