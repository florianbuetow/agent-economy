"""
Configuration management for the task board service.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict
from service_commons.config import (
    REDACTION_MARKER,
    create_settings_loader,
    get_safe_model_config,
)
from service_commons.config import (
    get_config_path as resolve_config_path,
)

if TYPE_CHECKING:
    from pathlib import Path


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
    format: str


class DatabaseConfig(BaseModel):
    """Database configuration."""

    model_config = ConfigDict(extra="forbid")
    path: str


class IdentityConfig(BaseModel):
    """Identity service connection configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    verify_jws_path: str
    timeout_seconds: int


class CentralBankConfig(BaseModel):
    """Central Bank service connection configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    escrow_lock_path: str
    escrow_release_path: str
    escrow_split_path: str | None = None
    timeout_seconds: int


class PlatformConfig(BaseModel):
    """Platform agent configuration for signing escrow operations."""

    model_config = ConfigDict(extra="forbid")
    agent_id: str
    private_key_path: str | None = None


class AssetsConfig(BaseModel):
    """Asset storage configuration."""

    model_config = ConfigDict(extra="forbid")
    storage_path: str
    max_file_size: int
    max_files_per_task: int


class RequestConfig(BaseModel):
    """Request handling configuration."""

    model_config = ConfigDict(extra="forbid")
    max_body_size: int


class DeadlinesConfig(BaseModel):
    """Optional legacy deadline defaults configuration."""

    model_config = ConfigDict(extra="forbid")
    default_bidding_seconds: int
    default_execution_seconds: int
    default_review_seconds: int


class LimitsConfig(BaseModel):
    """Optional legacy limits configuration."""

    model_config = ConfigDict(extra="forbid")
    max_title_length: int
    max_spec_length: int
    max_reason_length: int
    max_file_size: int
    max_assets_per_task: int


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
    identity: IdentityConfig
    central_bank: CentralBankConfig
    platform: PlatformConfig
    request: RequestConfig
    assets: AssetsConfig | None = None
    deadlines: DeadlinesConfig | None = None
    limits: LimitsConfig | None = None


def get_config_path() -> Path:
    """Determine configuration file path."""
    return resolve_config_path(
        env_var_name="CONFIG_PATH",
        default_filename="config.yaml",
    )


get_settings, clear_settings_cache = create_settings_loader(Settings, get_config_path)  # nosemgrep


def get_safe_config() -> dict[str, Any]:
    """Get configuration with sensitive values redacted."""
    return get_safe_model_config(get_settings(), REDACTION_MARKER)
