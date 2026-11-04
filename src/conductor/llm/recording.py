"""Wraps any LLM so each call lands in the llm_calls table with a notional cost."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from conductor.config import Price
from conductor.llm.types import LLM, LLMResult, Message


class RecordingLLM:
    def __init__(
        self,
        inner: LLM,
        sink: Callable[[str, LLMResult, float], None],
        prices: dict[str, Price],
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._prices = prices

    async def complete(
        self, role: str, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> LLMResult:
        res = await self._inner.complete(role, messages, schema)
        price = self._prices.get(res.usage.model)
        cost = (
            (res.usage.tokens_in * price.input + res.usage.tokens_out * price.output) / 1e6
            if price
            else 0.0
        )
        self._sink(role, res, cost)
        return res
