"""Wraps any LLM so each call is traced and lands in llm_calls with a notional cost.

The full prompt and response go to the llm_calls row (via the sink); the span only carries the
row id, which keeps spans small and traces cheap to ship.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel

from conductor.config import Price
from conductor.llm.types import LLM, LLMResult, Message, Usage
from conductor.obs.tracing import current, get_tracer


class LLMRecord(BaseModel):
    role: str
    messages: list[Message]
    schema_name: str | None
    text: str
    usage: Usage
    cost_usd: float
    agent: str | None
    subtask_id: str | None


class RecordingLLM:
    def __init__(
        self,
        inner: LLM,
        sink: Callable[[LLMRecord], int | None],
        prices: dict[str, Price],
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._prices = prices

    async def complete(
        self, role: str, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> LLMResult:
        with get_tracer().span("llm.call", role=role) as sp:
            start = time.perf_counter()
            res = await self._inner.complete(role, messages, schema)
            latency_ms = (time.perf_counter() - start) * 1000
            u = res.usage
            price = self._prices.get(u.model)
            # a cache hit (gateway cache or replay) costs nothing, whatever it would have cost
            cost = (
                0.0
                if u.cache_hit or price is None
                else (u.tokens_in * price.input + u.tokens_out * price.output) / 1e6
            )
            row_id = self._sink(
                LLMRecord(
                    role=role,
                    messages=messages,
                    schema_name=schema.__name__ if schema else None,
                    text=res.text,
                    usage=u,
                    cost_usd=cost,
                    agent=current("agent"),
                    subtask_id=current("subtask_id"),
                )
            )
            sp.set_attribute("model", u.model)
            sp.set_attribute("tokens_in", u.tokens_in)
            sp.set_attribute("tokens_out", u.tokens_out)
            sp.set_attribute("cost_usd", cost)
            sp.set_attribute("latency_ms", latency_ms)
            sp.set_attribute("cache_hit", u.cache_hit)
            sp.set_attribute("llm_call_id", row_id)
        return res
