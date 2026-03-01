"""Configuration for the Task Feeder."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict
from service_commons.config import get_config_path as resolve_config_path


class TaskFeederConfig(BaseModel):
    """Task Feeder behaviour settings."""

    model_config = ConfigDict(extra="forbid")

    handle: str
    tasks_file: str
    feed_interval_seconds: int
    max_open_tasks: int
    bidding_deadline_seconds: int
    execution_deadline_seconds: int
    review_deadline_seconds: int
    base_reward: int
    reward_per_level: int
    shuffle: bool


class _FileSettings(BaseModel):
    """Raw YAML file shape — only the section this module needs."""

    model_config = ConfigDict(extra="allow")

    task_feeder: TaskFeederConfig


def load_task_feeder_settings(
    config_path: Path | None = None,
) -> TaskFeederConfig:
    """Load Task Feeder settings from config.yaml.

    Args:
        config_path: Explicit path to config.yaml.  Falls back to the
                     AGENT_CONFIG_PATH env var, then to ``config.yaml``
                     next to the calling package.

    Returns:
        TaskFeederConfig instance.
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
    return settings.task_feeder
