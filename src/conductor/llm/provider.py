"""LiteLLM-backed provider. The only module that touches a provider SDK."""

from __future__ import annotations

import os

from conductor.llm.types import LiveCallsDisabled, Message, ProviderResponse


class RateLimited(Exception):
    """Provider answered 429."""


class LiteLLMProvider:
    async def __call__(self, model: str, messages: list[Message]) -> ProviderResponse:
        if os.environ.get("LIVE_LLM") != "1":
            raise LiveCallsDisabled("real LLM calls require LIVE_LLM=1")
        import litellm  # lazy: keeps import cost and any network setup out of tests

        try:
            resp = await litellm.acompletion(model=model, messages=messages)
        except litellm.exceptions.RateLimitError as exc:
            raise RateLimited(str(exc)) from exc
        usage = resp.usage
        return ProviderResponse(
            text=resp.choices[0].message.content or "",
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
        )
