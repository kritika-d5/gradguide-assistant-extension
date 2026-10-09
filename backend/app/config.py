"""Load priority presets and engine constants from config/presets.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "presets.yaml"

SUBSCORES = (
    "field_fit",
    "career_fit",
    "budget_fit",
    "academic_fit",
    "preference_fit",
    "outcome_fit",
    "reputation_fit",
)


class Preset(BaseModel):
    key: str
    label: str
    weights: dict[str, float]

    @model_validator(mode="after")
    def _normalise(self):
        missing = set(SUBSCORES) - self.weights.keys()
        extra = self.weights.keys() - set(SUBSCORES)
        if missing or extra:
            raise ValueError(f"preset {self.key}: missing {sorted(missing)}, unknown {sorted(extra)}")
        if any(w < 0 for w in self.weights.values()):
            raise ValueError(f"preset {self.key}: weights must be non negative")
        total = sum(self.weights.values())
        self.weights = {k: self.weights[k] / total for k in SUBSCORES}
        return self


class Bands(BaseModel):
    strong: float
    good: float


class EngineConfig(BaseModel):
    version: str
    neutral_subscore: float
    subscore_floor: float
    stability_nudge: float
    bands: Bands
    presets: dict[str, Preset]


def load_config(path: Path = CONFIG_PATH) -> EngineConfig:
    raw = yaml.safe_load(path.read_text())
    presets = {key: Preset(key=key, **p) for key, p in raw["presets"].items()}
    return EngineConfig(version=raw["version"], presets=presets, **raw["constants"])


@lru_cache(maxsize=1)
def default_config() -> EngineConfig:
    return load_config()
