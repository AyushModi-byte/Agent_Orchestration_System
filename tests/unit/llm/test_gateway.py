"""Gateway behaviour with a scripted provider: no network, no real quota."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from conductor.config import ModelsConfig, ProviderLimits, RoleModels
from conductor.llm.budget import BudgetGuard
from conductor.llm.cache import DiskCache
from conductor.llm.fake import FakeLLM
from conductor.llm.gateway import LLMGateway
from conductor.llm.limiter import RateLimiter
from conductor.llm.provider import RateLimited
from conductor.llm.types import (
    AllModelsFailed,
    BudgetExceeded,
    Message,
    ProviderResponse,
    SchemaValidationError,
)

MSGS: list[Message] = [{"role": "user", "content": "hi"}]


class Answer(BaseModel):
    n: int


class ScriptedProvider:
    """Pops one item per call: a string is a reply, an Exception is raised."""

    def __init__(self, *items: str | Exception) -> None:
        self.items = list(items)
        self.calls: list[str] = []

    async def __call__(self, model: str, messages: list[Message]) -> ProviderResponse:
        self.calls.append(model)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return ProviderResponse(text=item, tokens_in=3, tokens_out=2)


async def _no_sleep(_: float) -> None:
    return None


def make_gateway(tmp_path: Path, provider: ScriptedProvider, caps: dict[str, int] | None = None):
    cfg = ModelsConfig(
        roles={"cheap": RoleModels(primary="groq/a", fallback="gemini/b")},
        providers={
            "groq": ProviderLimits(rpm=600, daily_live_budget=100),
            "gemini": ProviderLimits(rpm=600, daily_live_budget=100),
        },
    )
    return LLMGateway(
        config=cfg,
        provider=provider,
        cache=DiskCache(tmp_path / "c.sqlite"),
        limiter=RateLimiter({"groq": 600, "gemini": 600}, sleep=_no_sleep),
        budget=BudgetGuard(tmp_path / "b.sqlite", caps or {"groq": 100, "gemini": 100}),
    )


async def test_cache_hit_skips_provider(tmp_path: Path) -> None:
    prov = ScriptedProvider("hello")
    gw = make_gateway(tmp_path, prov)
    first = await gw.complete("cheap", MSGS)
    second = await gw.complete("cheap", MSGS)
    assert first.text == second.text == "hello"
    assert not first.usage.cache_hit
    assert second.usage.cache_hit
    assert len(prov.calls) == 1


async def test_fallback_on_provider_error(tmp_path: Path) -> None:
    prov = ScriptedProvider(RuntimeError("boom"), "from fallback")
    res = await make_gateway(tmp_path, prov).complete("cheap", MSGS)
    assert res.text == "from fallback"
    assert prov.calls == ["groq/a", "gemini/b"]
    assert res.usage.model == "gemini/b"


async def test_fallback_on_429(tmp_path: Path) -> None:
    prov = ScriptedProvider(RateLimited("429"), "ok")
    res = await make_gateway(tmp_path, prov).complete("cheap", MSGS)
    assert res.usage.model == "gemini/b"


async def test_all_models_failed(tmp_path: Path) -> None:
    prov = ScriptedProvider(RuntimeError("x"), RuntimeError("y"))
    with pytest.raises(AllModelsFailed):
        await make_gateway(tmp_path, prov).complete("cheap", MSGS)


async def test_schema_retries_once_on_bad_json(tmp_path: Path) -> None:
    prov = ScriptedProvider("not json", '```json\n{"n": 4}\n```')
    res = await make_gateway(tmp_path, prov).complete("cheap", MSGS, schema=Answer)
    assert res.parsed == Answer(n=4)
    assert len(prov.calls) == 2
    assert res.usage.tokens_in == 6  # usage from both attempts


async def test_schema_fails_after_one_retry(tmp_path: Path) -> None:
    prov = ScriptedProvider("bad", "still bad")
    with pytest.raises(SchemaValidationError):
        await make_gateway(tmp_path, prov).complete("cheap", MSGS, schema=Answer)
    assert len(prov.calls) == 2


async def test_budget_exceeded_raises_when_all_providers_capped(tmp_path: Path) -> None:
    prov = ScriptedProvider("never used")
    gw = make_gateway(tmp_path, prov, caps={"groq": 0, "gemini": 0})
    with pytest.raises(BudgetExceeded):
        await gw.complete("cheap", MSGS)
    assert prov.calls == []


def test_budget_guard_counts_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "b.sqlite"
    guard = BudgetGuard(path, {"groq": 2}, today=lambda: "2026-01-01")
    guard.consume("groq")
    guard.consume("groq")
    with pytest.raises(BudgetExceeded):
        guard.consume("groq")
    reopened = BudgetGuard(path, {"groq": 2}, today=lambda: "2026-01-01")
    assert reopened.used("groq") == 2
    assert BudgetGuard(path, {"groq": 2}, today=lambda: "2026-01-02").used("groq") == 0


async def test_limiter_spaces_calls() -> None:
    now = 0.0
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        nonlocal now
        slept.append(s)
        now += s

    limiter = RateLimiter({"groq": 30}, clock=lambda: now, sleep=fake_sleep)  # 2s apart
    await limiter.acquire("groq")
    await limiter.acquire("groq")
    await limiter.acquire("groq")
    assert slept == [pytest.approx(2.0), pytest.approx(2.0)]


async def test_limiter_backoff_doubles_on_429() -> None:
    limiter = RateLimiter({"groq": 600}, clock=lambda: 0.0, sleep=_no_sleep, base_backoff=2.0)
    assert limiter.penalize("groq") == 2.0
    assert limiter.penalize("groq") == 4.0
    limiter.reset("groq")
    assert limiter.penalize("groq") == 2.0


async def test_fake_llm_scripted_per_role() -> None:
    fake = FakeLLM({"planner": ['{"n": 1}']})
    res = await fake.complete("planner", MSGS, schema=Answer)
    assert res.parsed == Answer(n=1)
    with pytest.raises(AssertionError):
        await fake.complete("planner", MSGS)
