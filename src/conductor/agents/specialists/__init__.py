"""The four specialists. They differ only by prompt, permitted tools and iteration cap, all of
which come from prompts/*.md and config/agents.yaml, so one class serves all of them."""

from __future__ import annotations

from conductor.agents.specialists.base import Specialist, SpecialistError
from conductor.config import AgentsConfig, load_agents_config
from conductor.llm.types import LLM
from conductor.tools.registry import ToolRegistry


def build_specialists(
    llm: LLM, registry: ToolRegistry, config: AgentsConfig | None = None
) -> dict[str, Specialist]:
    cfg = config or load_agents_config()
    return {
        name: Specialist(name, llm, registry, c.tools, c.max_iterations)
        for name, c in cfg.specialists.items()
    }


__all__ = ["Specialist", "SpecialistError", "build_specialists"]
