"""Configuration for the demo_replay engine.

Kills the hardcoded service URLs that used to live in ``clients.py``
(GAP-E11) — every service URL is resolved once from config.yaml and
passed explicitly into each client call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict
from service_commons.config import get_config_path as resolve_config_path

if TYPE_CHECKING:
    from pathlib import Path


class PlatformConfig(BaseModel):
    """Service URLs for the platform demo_replay talks to."""

    model_config = ConfigDict(extra="forbid")

    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str


class _FileSettings(BaseModel):
    """Raw YAML file shape — only the section this module needs."""

    model_config = ConfigDict(extra="allow")

    platform: PlatformConfig


def load_platform_settings(config_path: Path | None = None) -> PlatformConfig:
    """Load platform service URLs from config.yaml.

    Args:
        config_path: Explicit path to config.yaml. Falls back to the
                     TOOLS_CONFIG_PATH env var, then to ``config.yaml``
                     in the current working directory.

    Returns:
        PlatformConfig instance.
    """
    if config_path is None:
        config_path = resolve_config_path(
            env_var_name="TOOLS_CONFIG_PATH",
            default_filename="config.yaml",
        )

    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        msg = f"Invalid config file: {config_path}"
        raise ValueError(msg)

    settings = _FileSettings(**raw)
    return settings.platform
