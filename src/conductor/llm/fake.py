"""Scripted stand-in for LLMGateway. Default for every test so no quota is ever spent."""

from __future__ import annotations

from collections import defaultdict, deque

from pydantic import BaseModel

from conductor.llm.gateway import parse_model
from conductor.llm.types import LLMResult, Message, Usage


class FakeLLM:
    """Same interface as LLMGateway. Responses are queued per role and served in order."""

    def __init__(self, scripts: dict[str, list[str]] | None = None) -> None:
        self._queues: dict[str, deque[str]] = defaultdict(deque)
        for role, responses in (scripts or {}).items():
            self._queues[role].extend(responses)
        self.calls: list[tuple[str, list[Message]]] = []

    def script(self, role: str, *responses: str) -> None:
        self._queues[role].extend(responses)

    async def complete(
        self, role: str, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> LLMResult:
        self.calls.append((role, messages))
        if not self._queues[role]:
            raise AssertionError(f"FakeLLM has no scripted response left for role {role!r}")
        text = self._queues[role].popleft()
        parsed = parse_model(text, schema) if schema else None
        return LLMResult(text=text, parsed=parsed, usage=Usage(model=f"fake/{role}"))
