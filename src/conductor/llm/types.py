"""Shared gateway types. Usage is returned on every call so cost/trace layers need no reparsing."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

Message = dict[str, str]


class Usage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    model: str = ""
    cache_hit: bool = False


class LLMResult(BaseModel):
    text: str
    parsed: Any | None = None  # validated schema instance when a schema was requested
    usage: Usage


class ProviderResponse(BaseModel):
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


class Provider(Protocol):
    async def __call__(self, model: str, messages: list[Message]) -> ProviderResponse: ...


class LLM(Protocol):
    """Interface shared by LLMGateway and FakeLLM so agents never care which one they hold."""

    async def complete(
        self, role: str, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> LLMResult: ...


class BudgetExceeded(Exception):
    """Daily live-call cap hit for a provider."""


class LiveCallsDisabled(Exception):
    """A real provider call was attempted without LIVE_LLM=1."""


class SchemaValidationError(Exception):
    """Model output stayed invalid after the single permitted retry."""


class AllModelsFailed(Exception):
    """Primary and fallback both errored."""
