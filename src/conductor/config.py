"""Typed loading of config/models.yaml so the gateway never reads raw dicts."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class RoleModels(BaseModel):
    primary: str
    fallback: str


class ProviderLimits(BaseModel):
    rpm: float
    daily_live_budget: int


class ModelsConfig(BaseModel):
    roles: dict[str, RoleModels]
    providers: dict[str, ProviderLimits]


def _load(name: str, path: Path | None) -> object:
    return yaml.safe_load((path or DEFAULT_CONFIG_DIR / name).read_text(encoding="utf-8"))


def load_models_config(path: Path | None = None) -> ModelsConfig:
    return ModelsConfig.model_validate(_load("models.yaml", path))


class SpecialistConfig(BaseModel):
    max_iterations: int
    tools: list[str]


class AgentsConfig(BaseModel):
    specialists: dict[str, SpecialistConfig]


class Escalation(BaseModel):
    min_plan_confidence: float
    min_reviewer_score: float
    max_specialist_retries: int
    max_reviewer_rejections: int


class PoliciesConfig(BaseModel):
    escalation: Escalation


class Price(BaseModel):
    input: float
    output: float


class MemoryConfig(BaseModel):
    top_k: int = 3
    max_distance: float = 0.9
    consolidation_similarity: float = 0.9
    expiry_threshold: float = 0.15
    half_life_days: float = 30.0
    access_scale: float = 5.0


def load_memory_config(path: Path | None = None) -> MemoryConfig:
    return MemoryConfig.model_validate(_load("memory.yaml", path))


def load_agents_config(path: Path | None = None) -> AgentsConfig:
    return AgentsConfig.model_validate(_load("agents.yaml", path))


def load_policies(path: Path | None = None) -> PoliciesConfig:
    return PoliciesConfig.model_validate(_load("policies.yaml", path))


def load_prices(path: Path | None = None) -> dict[str, Price]:
    raw = _load("prices.yaml", path)
    assert isinstance(raw, dict)
    return {k: Price.model_validate(v) for k, v in raw.items()}
