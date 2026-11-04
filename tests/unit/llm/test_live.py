"""Live smoke tests. Skipped unless LIVE_LLM=1 because they spend free-tier quota."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conductor.llm.gateway import LLMGateway

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LIVE_LLM") != "1", reason="needs LIVE_LLM=1"),
]


async def test_live_cheap_role(tmp_path: Path) -> None:
    gw = LLMGateway(state_dir=tmp_path)
    res = await gw.complete("cheap", [{"role": "user", "content": "Reply with the word: pong"}])
    assert res.text
    assert res.usage.tokens_out > 0
