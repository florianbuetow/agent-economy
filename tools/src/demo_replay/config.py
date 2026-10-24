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


class DemoReplayConfig(BaseModel):
    """Explicit durable replay storage and environment identity."""

    model_config = ConfigDict(extra="forbid")

    environment_id: str
    state_directory: Path
    max_record_bytes: StrictInt
    diagnostic_limit: StrictInt

    @field_validator("environment_id")
    @classmethod
    def validate_environment_id(cls, value: str) -> str:
        if not value:
            msg = "environment_id must be a nonempty string"
            raise ValueError(msg)
        return value

    @field_validator("max_record_bytes", "diagnostic_limit")
    @classmethod
    def validate_positive_integer(cls, value: int) -> int:
        if value <= 0:
            msg = "durable replay limits must be positive integers"
            raise ValueError(msg)
        return value


class _PlatformFileSettings(BaseModel):
    """Raw YAML file shape — only the section this module needs."""

    model_config = ConfigDict(extra="allow")

    platform: PlatformConfig


class _DemoFileSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    demo_replay: DemoReplayConfig


def _load_raw(config_path: Path | None) -> tuple[dict[str, object], Path]:
    if config_path is None:
        config_path = resolve_config_path(
            env_var_name="TOOLS_CONFIG_PATH",
            default_filename="config.yaml",
        )
    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        msg = f"Invalid config file: {config_path}"
        raise ValueError(msg)
    return raw, config_path


def load_platform_settings(config_path: Path | None = None) -> PlatformConfig:
    """Load platform service URLs from config.yaml.

    Args:
        config_path: Explicit path to config.yaml. Falls back to the
                     TOOLS_CONFIG_PATH env var, then to ``config.yaml``
                     in the current working directory.

    Returns:
        PlatformConfig instance.
    """
    raw, config_path = _load_raw(config_path)
    settings = _PlatformFileSettings.model_validate(raw)
    key_path = settings.platform.platform_key_path
    if not key_path.is_absolute():
        settings.platform.platform_key_path = (config_path.parent / key_path).resolve(strict=False)
    return settings.platform


def load_demo_settings(config_path: Path | None = None) -> DemoReplayConfig:
    """Load the explicit durable replay settings and resolve its state path."""
    raw, config_path = _load_raw(config_path)
    settings = _DemoFileSettings.model_validate(raw)
    state_directory = settings.demo_replay.state_directory
    if not state_directory.is_absolute():
        settings.demo_replay.state_directory = (config_path.parent / state_directory).resolve(
            strict=False
        )
    return settings.demo_replay
