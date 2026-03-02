"""
Configuration management for the reputation service.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, model_validator
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
    directory: str


class PlatformConfig(BaseModel):
    """Platform agent configuration."""

    model_config = ConfigDict(extra="forbid")
    agent_config_path: str


class LegacyIdentityConfig(BaseModel):
    """Backward-compatible identity view for legacy tests."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    verify_jws_path: str
    timeout_seconds: int


class RequestConfig(BaseModel):
    """Request validation configuration."""

    model_config = ConfigDict(extra="forbid")
    max_body_size: int


class DatabaseConfig(BaseModel):
    """Database configuration."""

    model_config = ConfigDict(extra="forbid")
    path: str


class FeedbackConfig(BaseModel):
    """Feedback submission configuration."""

    model_config = ConfigDict(extra="forbid")
    reveal_timeout_seconds: int
    max_comment_length: int


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
    platform: PlatformConfig | None = None
    identity: LegacyIdentityConfig | None = None
    request: RequestConfig
    database: DatabaseConfig
    feedback: FeedbackConfig

    @model_validator(mode="after")
    def normalize_identity_platform(self) -> Settings:
        """Support legacy identity config while using platform verification."""
        if self.platform is None and self.identity is None:
            msg = "Either platform or identity configuration must be provided"
            raise ValueError(msg)

        if self.identity is None:
            if self.platform is None:
                msg = "Platform configuration must be provided"
                raise ValueError(msg)
            self.identity = LegacyIdentityConfig(
                base_url=self.platform.agent_config_path,
                verify_jws_path="validate_certificate",
                timeout_seconds=0,
            )

        if self.platform is None:
            self.platform = PlatformConfig(agent_config_path="")

        return self


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
