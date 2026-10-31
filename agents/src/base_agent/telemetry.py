"""Agent-side logging level and OpenTelemetry setup from the agent config file."""

from __future__ import annotations

import logging
import sys
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict
from service_auth.config import resolve_config_path
from service_commons.telemetry import TelemetryConfig, setup_telemetry


class _LoggingSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str


class _ObservabilitySettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    logging: _LoggingSection
    telemetry: TelemetryConfig


def _load() -> _ObservabilitySettings:
    config_path = resolve_config_path(
        env_var_name="AGENT_CONFIG_PATH", default_filename="config.yaml"
    )
    raw: Any = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        msg = f"Invalid agent config file: {config_path}"
        raise ValueError(msg)
    return _ObservabilitySettings(**raw)


def configure_observability(service_name: str) -> logging.Logger:
    """Configure stderr logging at the configured level and install telemetry.

    Returns the agent's root logger; call once at process start.
    """
    settings = _load()
    level = logging.getLevelNamesMapping()[settings.logging.level.upper()]
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )
    logger = logging.getLogger()
    setup_telemetry(settings.telemetry, service_name=service_name, app=None, log_logger=logger)
    return logger
