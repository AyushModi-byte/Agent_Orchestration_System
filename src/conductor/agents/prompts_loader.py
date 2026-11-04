"""Prompts live in prompts/*.md so they can be edited and diffed without touching code."""

from __future__ import annotations

from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")
