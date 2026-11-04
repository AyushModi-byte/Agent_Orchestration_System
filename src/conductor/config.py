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


def load_models_config(path: Path | None = None) -> ModelsConfig:
    path = path or DEFAULT_CONFIG_DIR / "models.yaml"
    return ModelsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
