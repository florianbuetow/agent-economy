"""
Configuration management for the reputation service.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from service_auth.gateway import GatewaySignerConfig
from service_commons.config import (
    REDACTION_MARKER,
    create_settings_loader,
    get_safe_model_config,
)
from service_commons.config import (
    get_config_path as resolve_config_path,
)
from service_commons.telemetry import TelemetryConfig

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

    @field_validator("agent_config_path")
    @classmethod
    def agent_config_path_must_not_be_blank(cls, value: str) -> str:
        """Require the load-only platform credential configuration."""
        if not value.strip():
            msg = "platform.agent_config_path must not be blank"
            raise ValueError(msg)
        return value


class IdentityConfig(BaseModel):
    """Identity service configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    get_agent_path: str
    verify_jws_path: str
    timeout_seconds: int = Field(strict=True, gt=0)

    @field_validator("base_url")
    @classmethod
    def base_url_must_be_http_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
        except ValueError as exc:
            msg = "identity.base_url must be a non-blank HTTP(S) URL"
            raise ValueError(msg) from exc
        if (
            value != value.strip()
            or parsed.scheme not in {"http", "https"}
            or hostname is None
            or not hostname
            or parsed.query
            or parsed.fragment
        ):
            msg = "identity.base_url must be a non-blank HTTP(S) URL"
            raise ValueError(msg)
        return value

    @field_validator("get_agent_path", "verify_jws_path")
    @classmethod
    def endpoint_path_must_be_absolute(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            value != value.strip()
            or not value.startswith("/")
            or value.startswith("//")
            or parsed.path != value
            or parsed.query
            or parsed.fragment
        ):
            msg = "identity endpoint paths must start with one '/'"
            raise ValueError(msg)
        return value


class RequestConfig(BaseModel):
    """Request validation configuration."""

    model_config = ConfigDict(extra="forbid")
    max_body_size: int


class FeedbackConfig(BaseModel):
    """Feedback submission configuration."""

    model_config = ConfigDict(extra="forbid")
    reveal_timeout_seconds: int
    max_comment_length: int
    reveal_sweep_interval_seconds: int = Field(strict=True, gt=0)


class QualityConfig(BaseModel):
    """Quality score configuration."""

    model_config = ConfigDict(extra="forbid")
    # Identity display name of the local operator, who may read all source-level
    # quality provenance (Q-M3 visibility P-A).
    operator_name: str = Field(min_length=1)


class TaskBoardConfig(BaseModel):
    """Task Board service configuration (task lookups for feedback authorization)."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    timeout_seconds: int = Field(strict=True, gt=0)

    @field_validator("base_url")
    @classmethod
    def base_url_must_be_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if value != value.strip() or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            msg = "task_board.base_url must be a non-blank HTTP(S) URL"
            raise ValueError(msg)
        return value


class GatewaySigningConfig(GatewaySignerConfig):
    """Reputation's shared strict Gateway signer configuration."""


class DbGatewayConfig(BaseModel):
    """DB Gateway service configuration."""

    model_config = ConfigDict(extra="forbid")
    url: str
    timeout_seconds: int
    signing: GatewaySigningConfig


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
    telemetry: TelemetryConfig
    platform: PlatformConfig
    identity: IdentityConfig
    task_board: TaskBoardConfig
    request: RequestConfig
    feedback: FeedbackConfig
    quality: QualityConfig
    db_gateway: DbGatewayConfig


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
