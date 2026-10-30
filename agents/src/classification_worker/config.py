"""Configuration for classification worker profiles (``classification_workers:`` section)."""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from base_agent.worker_config import WorkerBehaviorConfig, WorkerLLMConfig
from classification_worker.adaptation import PricingRule

SECTION = "classification_workers"


class PricingConfig(BaseModel):
    """Outcome-based pricing parameters; see ``classification_worker.adaptation``."""

    model_config = ConfigDict(extra="forbid")

    initial_bid_pct: int
    min_bid_pct: int
    max_bid_pct: int
    raise_step_pct: int
    lower_step_pct: int
    min_samples: int
    min_approval_pct: int

    def to_rule(self) -> PricingRule:
        return PricingRule(**self.model_dump())


class ClassificationWorkerProfile(BaseModel):
    """One named classification worker: identity, strategy, behaviour and pricing."""

    model_config = ConfigDict(extra="forbid")

    handle: str
    strategy: Literal["llm", "keyword"]
    llm: WorkerLLMConfig | None
    keywords: dict[str, list[str]] | None
    behavior: WorkerBehaviorConfig
    pricing: PricingConfig

    @model_validator(mode="after")
    def _strategy_inputs(self) -> Self:
        if self.strategy == "llm" and self.llm is None:
            raise ValueError("strategy 'llm' requires an llm section")
        if self.strategy == "keyword" and not self.keywords:
            raise ValueError("strategy 'keyword' requires a nonempty keywords table")
        self.pricing.to_rule()
        return self


def _roster_handles(raw: dict[str, object], config_path: Path) -> set[str]:
    data = raw.get("data")
    roster_value = data.get("roster_path") if isinstance(data, dict) else None
    if not isinstance(roster_value, str) or not roster_value:
        raise ValueError(f"Config file missing valid data.roster_path: {config_path}")
    roster_path = Path(roster_value)
    if not roster_path.is_absolute():
        roster_path = config_path.parent / roster_path
    roster = yaml.safe_load(roster_path.read_text())
    agents = roster.get("agents") if isinstance(roster, dict) else None
    if not isinstance(agents, dict):
        raise ValueError(f"Roster file missing 'agents' mapping: {roster_path}")
    return {str(handle) for handle in agents}


def load_classification_profile(config_path: Path, name: str) -> ClassificationWorkerProfile:
    """Load and validate exactly one named profile; other profiles' secrets are not read."""
    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid config file: {config_path}")
    section = raw.get(SECTION)
    if not isinstance(section, dict) or not section:
        raise ValueError(f"Config file must contain a non-empty '{SECTION}' section")
    if name not in section:
        available = ", ".join(sorted(str(key) for key in section))
        raise KeyError(f"Classification worker profile {name!r} not found. Available: {available}")
    profile = ClassificationWorkerProfile.model_validate(section[name])
    if profile.handle not in _roster_handles(raw, config_path):
        raise KeyError(f"Profile {name!r} handle {profile.handle!r} is not in roster.yaml")
    return profile
