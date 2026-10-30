"""Settings for the economy report (``economy_report:`` section plus shared sections)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from classification_worker.config import SECTION as WORKER_SECTION
from classification_worker.config import PricingConfig


class EconomyReportConfig(BaseModel):
    """The ``economy_report`` section."""

    model_config = ConfigDict(extra="forbid")

    worker_roles: list[str] = Field(min_length=1)
    confidence_z: float = Field(gt=0)
    quality_timeout_seconds: float = Field(gt=0)


class ReportSettings(BaseModel):
    """Everything the report needs, resolved from one config file."""

    model_config = ConfigDict(extra="forbid")

    report: EconomyReportConfig
    runtime_directory: Path
    reputation_url: str
    pricing: dict[str, PricingConfig]


def load_report_settings(config_path: Path) -> ReportSettings:
    """Load report settings; profile pricing is read without resolving LLM secrets."""
    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid config file: {config_path}")
    runtime_dir = Path(str(raw["runtime_state"]["directory"]))
    if not runtime_dir.is_absolute():
        runtime_dir = (config_path.parent / runtime_dir).resolve()
    workers = raw.get(WORKER_SECTION)
    if not isinstance(workers, dict):
        raise ValueError(f"Config file has no '{WORKER_SECTION}' section")
    return ReportSettings(
        report=EconomyReportConfig.model_validate(raw["economy_report"]),
        runtime_directory=runtime_dir,
        reputation_url=str(raw["platform"]["reputation_url"]),
        pricing={
            str(name): PricingConfig.model_validate(profile["pricing"])
            for name, profile in workers.items()
        },
    )
