"""Configuration for the demo_replay engine.

Kills the hardcoded service URLs that used to live in ``clients.py``
(GAP-E11) — every service URL is resolved once from config.yaml and
passed explicitly into each client call.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, StrictInt, field_validator
from service_commons.config import get_config_path as resolve_config_path


class PlatformConfig(BaseModel):
    """Platform service URLs and the explicitly provisioned signing key path."""

    model_config = ConfigDict(extra="forbid")

    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str
    platform_key_path: Path
    token_ttl_seconds: StrictInt

    @field_validator("token_ttl_seconds")
    @classmethod
    def validate_token_ttl_seconds(cls, value: int) -> int:
        if value <= 0:
            msg = "token_ttl_seconds must be a positive integer"
            raise ValueError(msg)
        return value


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
    key_path = settings.platform.platform_key_path
    if not key_path.is_absolute():
        settings.platform.platform_key_path = (config_path.parent / key_path).absolute()
    return settings.platform
