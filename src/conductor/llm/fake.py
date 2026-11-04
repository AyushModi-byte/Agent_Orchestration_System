"""Scripted stand-in for LLMGateway. Default for every test so no quota is ever spent."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from conductor.llm.gateway import parse_model
from conductor.llm.types import LLMResult, Message, SchemaValidationError, Usage

Responder = Callable[[str, list[Message]], str]


class FakeLLM:
    """Same interface as LLMGateway. Queued responses are served per role in order; when a role's
    queue is empty an optional responder computes one from the messages, which keeps tests with
    concurrent callers deterministic."""

    def __init__(
        self, scripts: dict[str, list[str]] | None = None, responder: Responder | None = None
    ) -> None:
        self._queues: dict[str, deque[str]] = defaultdict(deque)
        for role, responses in (scripts or {}).items():
            self._queues[role].extend(responses)
        self._responder = responder
        self.calls: list[tuple[str, list[Message]]] = []

    def script(self, role: str, *responses: str) -> None:
        self._queues[role].extend(responses)

    async def complete(
        self, role: str, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> LLMResult:
        self.calls.append((role, messages))
        if self._queues[role]:
            text = self._queues[role].popleft()
        elif self._responder is not None:
            text = self._responder(role, messages)
        else:
            raise AssertionError(f"FakeLLM has no scripted response left for role {role!r}")
        try:
            parsed = parse_model(text, schema) if schema else None
        except (ValueError, ValidationError) as exc:
            raise SchemaValidationError(str(exc)) from exc
        return LLMResult(text=text, parsed=parsed, usage=Usage(model=f"fake/{role}"))
