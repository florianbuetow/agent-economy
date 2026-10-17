"""
Configuration management for the Database Gateway service.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from service_auth.gateway import GatewayTrustConfig
from service_commons.config import (
    REDACTION_MARKER,
    create_settings_loader,
    get_safe_model_config,
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
    format: str


class DatabaseConfig(BaseModel):
    """Database configuration."""

    model_config = ConfigDict(extra="forbid")
    path: str
    schema_path: str
    busy_timeout_ms: int
    journal_mode: str


class RequestConfig(BaseModel):
    """Request handling configuration."""

    model_config = ConfigDict(extra="forbid")
    max_body_size: int


class RecoveryConfig(BaseModel):
    """Explicit paths used by offline recovery operations."""

    model_config = ConfigDict(extra="forbid")
    maintenance_lock_path: Path
    backup_root: Path


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
    request: RequestConfig
    auth: GatewayTrustConfig
    recovery: RecoveryConfig


Settings.model_rebuild(_types_namespace={"GatewayTrustConfig": GatewayTrustConfig})


def get_config_path() -> Path:
    """Determine configuration file path."""
    return resolve_config_path(
        env_var_name="CONFIG_PATH",
        default_filename="config.yaml",
    )


get_settings, clear_settings_cache = create_settings_loader(Settings, get_config_path)  # nosemgrep


def resolve_recovery_path(path: Path) -> Path:
    """Resolve a recovery path lexically against the selected config parent."""
    candidate = path if path.is_absolute() else get_config_path().parent / path
    return _absolute_lexical_path(candidate)


def _absolute_lexical_path(path: Path) -> Path:
    candidate = path if path.is_absolute() else Path.cwd() / path
    normalized_parts: list[str] = []
    for part in candidate.parts:
        if part in {candidate.anchor, "", "."}:
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(part)
    return Path(candidate.anchor, *normalized_parts)


def get_safe_config() -> dict[str, Any]:
    """Get configuration with sensitive values redacted."""
    return get_safe_model_config(get_settings(), REDACTION_MARKER)
