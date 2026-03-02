"""
Configuration management for the UI service.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from service_commons.config import (
    REDACTION_MARKER,
    get_safe_model_config,
    load_settings,
    load_yaml_config,
)
from service_commons.config import (
    get_config_path as resolve_config_path,
)


class ServiceConfig(BaseModel):
    """Service identity configuration."""

    model_config = ConfigDict(extra="forbid")
    name: str
    version: str


class ServerConfig(BaseModel):
    """HTTP server configuration."""

    model_config = ConfigDict(extra="forbid")
    host: str
    port: int
    log_level: str


class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(extra="forbid")
    level: str
    directory: str


class DatabaseConfig(BaseModel):
    """Database configuration."""

    model_config = ConfigDict(extra="forbid")
    path: str


class SSEConfig(BaseModel):
    """Server-Sent Events configuration."""

    model_config = ConfigDict(extra="forbid")
    poll_interval_seconds: int
    keepalive_interval_seconds: int
    batch_size: int


class FrontendConfig(BaseModel):
    """Frontend static files configuration."""

    model_config = ConfigDict(extra="forbid")
    web_root: str


class RequestConfig(BaseModel):
    """Request handling configuration."""

    model_config = ConfigDict(extra="forbid")
    max_body_size: int


class UserAgentConfig(BaseModel):
    """User agent configuration."""

    model_config = ConfigDict(extra="forbid")
    agent_config_path: str


class Settings(BaseModel):
    """
    Root configuration container.

    All fields are REQUIRED. No defaults exist.
    Missing fields cause immediate startup failure.
    """

    model_config = ConfigDict(extra="forbid")
    service: ServiceConfig
    server: ServerConfig
    logging: LoggingConfig
    database: DatabaseConfig
    sse: SSEConfig
    frontend: FrontendConfig
    request: RequestConfig
    user_agent: UserAgentConfig


def get_config_path() -> Path:
    """Determine configuration file path."""
    return resolve_config_path(
        env_var_name="CONFIG_PATH",
        default_filename="config.yaml",
    )


def _load_default_user_agent_config() -> dict[str, Any]:
    """Load the canonical user_agent section from service config.yaml."""
    service_root = Path(__file__).resolve().parents[2]
    service_config_path = service_root / "config.yaml"
    service_yaml = load_yaml_config(service_config_path)
    user_agent_config = service_yaml.get("user_agent")
    if not isinstance(user_agent_config, dict):
        msg = f"Missing or invalid user_agent section in {service_config_path}"
        raise ValueError(msg)
    return user_agent_config


@lru_cache
def get_settings() -> Settings:
    """Load and validate settings from YAML config."""
    config_path = get_config_path()
    yaml_config = load_yaml_config(config_path)
    if "user_agent" not in yaml_config:
        yaml_config["user_agent"] = _load_default_user_agent_config()
    return load_settings(Settings, yaml_config)


def clear_settings_cache() -> None:
    """Clear cached settings for test isolation."""
    get_settings.cache_clear()


def get_safe_config() -> dict[str, Any]:
    """Get configuration with sensitive values redacted."""
    return get_safe_model_config(get_settings(), REDACTION_MARKER)
