"""Configuration for the Task Feeder."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from service_commons.config import get_config_path as resolve_config_path

if TYPE_CHECKING:
    from pathlib import Path

# Acceptance keys share the ``task_feeder`` section but are parsed into a
# dedicated ``AcceptanceConfig`` (below), so the feeder loader strips them before
# constructing ``TaskFeederConfig`` (which forbids extra keys).
_ACCEPTANCE_ONLY_KEYS: tuple[str, ...] = (
    "acceptance_after_seconds",
    "acceptance_poll_interval_seconds",
    "min_bids_to_accept",
)


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
    review_interval_seconds: int = 30
    auto_approve_on_error: bool = False
    base_reward: int
    reward_per_level: int
    shuffle: bool


class AcceptanceConfig(BaseModel):
    """Autonomous bid-acceptance settings (WP-15 / Q-16).

    ``bidding_deadline_seconds`` mirrors the same key on ``TaskFeederConfig``; it
    is carried here only to enforce the T-035 invariant that acceptance opens
    strictly before a task's bidding deadline (otherwise every task would race
    its own expiry and the loop would accept nothing).
    """

    model_config = ConfigDict(extra="forbid")

    acceptance_after_seconds: int
    acceptance_poll_interval_seconds: int
    min_bids_to_accept: int
    bidding_deadline_seconds: int

    @model_validator(mode="after")
    def _acceptance_before_deadline(self) -> AcceptanceConfig:
        if self.acceptance_after_seconds >= self.bidding_deadline_seconds:
            msg = (
                "acceptance_after_seconds "
                f"({self.acceptance_after_seconds}) must be strictly less than "
                f"bidding_deadline_seconds ({self.bidding_deadline_seconds}); "
                "otherwise tasks expire before they can be accepted (T-035)"
            )
            raise ValueError(msg)
        return self


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

    section = raw.get("task_feeder")
    if isinstance(section, dict):
        # Acceptance keys are parsed separately by ``load_acceptance_settings``;
        # drop them here so ``TaskFeederConfig(extra="forbid")`` does not reject
        # them.  Any other unknown key still fails fast.
        stripped = {k: v for k, v in section.items() if k not in _ACCEPTANCE_ONLY_KEYS}
        raw = {**raw, "task_feeder": stripped}

    settings = _FileSettings(**raw)
    return settings.task_feeder


def load_acceptance_settings(
    config_path: Path | None = None,
) -> AcceptanceConfig:
    """Load autonomous-acceptance settings from the ``task_feeder`` section.

    Args:
        config_path: Explicit path to config.yaml.  Falls back to the
                     AGENT_CONFIG_PATH env var, then to ``config.yaml``
                     next to the calling package.

    Returns:
        AcceptanceConfig instance.

    Raises:
        ValueError: If the config file or ``task_feeder`` section is missing.
        pydantic.ValidationError: If a required acceptance key is missing or the
            T-035 ordering invariant is violated (fail fast at startup).
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

    section = raw.get("task_feeder")
    if not isinstance(section, dict):
        msg = f"Missing 'task_feeder' section in config file: {config_path}"
        raise ValueError(msg)

    keys = (*_ACCEPTANCE_ONLY_KEYS, "bidding_deadline_seconds")
    values = {key: section[key] for key in keys if key in section}
    return AcceptanceConfig(**values)
