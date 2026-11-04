"""SQLite disk cache. Makes identical prompts (and replays) free after the first live call."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from conductor.llm.types import Message, ProviderResponse


def cache_key(
    role: str, model: str, messages: list[Message], schema: type[BaseModel] | None
) -> str:
    schema_repr = json.dumps(schema.model_json_schema(), sort_keys=True) if schema else ""
    blob = json.dumps([role, model, messages, schema_repr], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class DiskCache:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT)")
        self._db.commit()

    def get(self, key: str) -> ProviderResponse | None:
        row = self._db.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return ProviderResponse.model_validate_json(row[0]) if row else None

    def put(self, key: str, value: ProviderResponse) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)",
            (key, value.model_dump_json()),
        )
        self._db.commit()
