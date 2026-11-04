"""The single entry point for model calls: cache -> limiter -> budget -> provider -> fallback."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from pydantic import BaseModel, ValidationError

from conductor.config import ModelsConfig, load_models_config
from conductor.llm.budget import BudgetGuard
from conductor.llm.cache import DiskCache, cache_key
from conductor.llm.limiter import RateLimiter
from conductor.llm.provider import LiteLLMProvider, RateLimited
from conductor.llm.types import (
    AllModelsFailed,
    BudgetExceeded,
    LiveCallsDisabled,
    LLMResult,
    Message,
    Provider,
    ProviderResponse,
    SchemaValidationError,
    Usage,
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_model(text: str, schema: type[BaseModel]) -> BaseModel:
    """Models often wrap JSON in code fences; strip them before validating."""
    return schema.model_validate(json.loads(_FENCE.sub("", text.strip())))


class LLMGateway:
    def __init__(
        self,
        config: ModelsConfig | None = None,
        provider: Provider | None = None,
        cache: DiskCache | None = None,
        limiter: RateLimiter | None = None,
        budget: BudgetGuard | None = None,
        state_dir: Path | str = ".cache",
    ) -> None:
        self._cfg = config or load_models_config()
        self._provider: Provider = provider or LiteLLMProvider()
        state = Path(state_dir)
        state.mkdir(parents=True, exist_ok=True)
        self._cache = cache or DiskCache(state / "llm_cache.sqlite")
        self._limiter = limiter or RateLimiter({p: v.rpm for p, v in self._cfg.providers.items()})
        self._budget = budget or BudgetGuard(
            state / "budget.sqlite",
            {p: v.daily_live_budget for p, v in self._cfg.providers.items()},
        )

    async def complete(
        self, role: str, messages: list[Message], schema: type[BaseModel] | None = None
    ) -> LLMResult:
        models = self._cfg.roles[role]
        key = cache_key(role, models.primary, messages, schema)
        if (hit := self._cache.get(key)) is not None:
            parsed = parse_model(hit.text, schema) if schema else None
            return LLMResult(
                text=hit.text, parsed=parsed, usage=Usage(model=models.primary, cache_hit=True)
            )

        resp, usage = await self._call(role, [models.primary, models.fallback], messages)
        parsed = None
        if schema is not None:
            try:
                parsed = parse_model(resp.text, schema)
            except (ValueError, ValidationError) as exc:
                retry = [
                    *messages,
                    {"role": "assistant", "content": resp.text},
                    {
                        "role": "user",
                        "content": f"Invalid JSON for the required schema ({exc}). "
                        "Reply with only valid JSON.",
                    },
                ]
                resp, usage2 = await self._call(role, [usage.model], retry)
                usage = _merge(usage, usage2)
                try:
                    parsed = parse_model(resp.text, schema)
                except (ValueError, ValidationError) as exc2:
                    raise SchemaValidationError(str(exc2)) from exc2
        self._cache.put(key, resp)  # only validated output is cached
        return LLMResult(text=resp.text, parsed=parsed, usage=usage)

    async def _call(
        self, role: str, models: list[str], messages: list[Message]
    ) -> tuple[ProviderResponse, Usage]:
        last: Exception | None = None
        budget_hit: BudgetExceeded | None = None
        for model in models:
            provider = model.split("/", 1)[0]
            try:
                self._budget.consume(provider)
            except BudgetExceeded as exc:
                budget_hit = exc
                continue  # a different provider may still have quota
            await self._limiter.acquire(provider)
            start = time.perf_counter()
            try:
                resp = await self._provider(model, messages)
            except LiveCallsDisabled:
                raise
            except RateLimited as exc:
                self._limiter.penalize(provider)
                last = exc
                continue
            except Exception as exc:
                last = exc
                continue
            self._limiter.reset(provider)
            usage = Usage(
                tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out,
                latency_ms=(time.perf_counter() - start) * 1000,
                model=model,
            )
            return resp, usage
        if last is None and budget_hit is not None:
            raise budget_hit
        raise AllModelsFailed(f"role {role}: {last}") from last


def _merge(a: Usage, b: Usage) -> Usage:
    return Usage(
        tokens_in=a.tokens_in + b.tokens_in,
        tokens_out=a.tokens_out + b.tokens_out,
        latency_ms=a.latency_ms + b.latency_ms,
        model=b.model,
    )
