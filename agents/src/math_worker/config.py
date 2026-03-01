"""Configuration for the Math Worker Agent."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict
from service_commons.config import get_config_path as resolve_config_path


class LLMConfig(BaseModel):
    """OpenAI-compatible LLM endpoint configuration."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    api_key: str
    model_id: str
    temperature: float
    max_tokens: int


class MathWorkerConfig(BaseModel):
    """Math Worker Agent behaviour settings."""

    model_config = ConfigDict(extra="forbid")

    handle: str
    scan_interval_seconds: int
    poll_interval_seconds: int
    max_poll_attempts: int
    error_backoff_seconds: int
    min_reward: int
    max_reward: int


class _FileSettings(BaseModel):
    """Raw YAML file shape — only the sections this module needs."""

    model_config = ConfigDict(extra="allow")

    llm: LLMConfig
    math_worker: MathWorkerConfig


def load_math_worker_settings(
    config_path: Path | None = None,
) -> tuple[LLMConfig, MathWorkerConfig]:
    """Load LLM and Math Worker settings from config.yaml.

    Args:
        config_path: Explicit path to config.yaml.  Falls back to the
                     AGENT_CONFIG_PATH env var, then to ``config.yaml``
                     next to the calling package.

    Returns:
        Tuple of (LLMConfig, MathWorkerConfig).
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
    return settings.llm, settings.math_worker
