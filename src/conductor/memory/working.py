"""Short-term memory in Redis, scoped by task_id.

Holds the plan, subtask outputs, intermediates and an error log. Everything gets a TTL so a
crashed task cannot leak keys forever, and clear() removes it all on completion.
"""

from __future__ import annotations

import json
from typing import Any

import redis

_PARTS = ("plan", "outputs", "inter", "errors")


class WorkingMemory:
    def __init__(self, client: redis.Redis, ttl_seconds: int = 3600) -> None:
        self._r = client
        self._ttl = ttl_seconds

    def _key(self, task_id: str, part: str) -> str:
        return f"wm:{task_id}:{part}"

    def _touch(self, key: str) -> None:
        self._r.expire(key, self._ttl)

    def set_plan(self, task_id: str, plan: dict[str, Any]) -> None:
        k = self._key(task_id, "plan")
        self._r.set(k, json.dumps(plan), ex=self._ttl)

    def get_plan(self, task_id: str) -> dict[str, Any] | None:
        raw = self._r.get(self._key(task_id, "plan"))
        return json.loads(raw) if raw else None

    def put_output(self, task_id: str, subtask_id: str, output: dict[str, Any]) -> None:
        k = self._key(task_id, "outputs")
        self._r.hset(k, subtask_id, json.dumps(output))
        self._touch(k)

    def outputs(self, task_id: str) -> dict[str, Any]:
        raw = self._r.hgetall(self._key(task_id, "outputs"))
        return {_s(k): json.loads(v) for k, v in raw.items()}

    def put_intermediate(self, task_id: str, name: str, value: Any) -> None:
        k = self._key(task_id, "inter")
        self._r.hset(k, name, json.dumps(value))
        self._touch(k)

    def intermediates(self, task_id: str) -> dict[str, Any]:
        raw = self._r.hgetall(self._key(task_id, "inter"))
        return {_s(k): json.loads(v) for k, v in raw.items()}

    def log_error(self, task_id: str, message: str) -> None:
        k = self._key(task_id, "errors")
        self._r.rpush(k, message)
        self._touch(k)

    def errors(self, task_id: str) -> list[str]:
        return [_s(v) for v in self._r.lrange(self._key(task_id, "errors"), 0, -1)]

    def clear(self, task_id: str) -> None:
        self._r.delete(*[self._key(task_id, p) for p in _PARTS])


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)
