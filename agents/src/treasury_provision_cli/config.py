"""Configuration for the Treasury Provision CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict
from service_commons.config import get_config_path as resolve_config_path

if TYPE_CHECKING:
    from pathlib import Path


class TreasuryConfig(BaseModel):
    """Treasury genesis settings (WP-08 / Q-9)."""

    model_config = ConfigDict(extra="forbid")

    handle: str
    genesis_amount: int
    genesis_reference: str


class _FileSettings(BaseModel):
    """Raw YAML file shape — only the section this module needs."""

    model_config = ConfigDict(extra="allow")

    treasury: TreasuryConfig


def load_treasury_settings(config_path: Path | None = None) -> TreasuryConfig:
    """Load Treasury Provision settings from config.yaml.

    Args:
        config_path: Explicit path to config.yaml.  Falls back to the
                     AGENT_CONFIG_PATH env var, then to ``config.yaml``
                     next to the calling package.

    Returns:
        TreasuryConfig instance.
    """
    if config_path is None:
        config_path = resolve_config_path(
            env_var_name="AGENT_CONFIG_PATH",
            default_filename="config.yaml",
        )

    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        msg = f"Invalid config file: {config_path}"
        raise ValueError(msg)

    settings = _FileSettings(**raw)
    return settings.treasury
