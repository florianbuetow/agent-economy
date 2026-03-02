"""Configuration management for the court service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, field_validator
from service_commons.config import (
    REDACTION_MARKER,
    create_settings_loader,
    get_safe_model_config,
)
from service_commons.config import get_config_path as resolve_config_path

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


class DatabaseConfig(BaseModel):
    """Database configuration."""

    model_config = ConfigDict(extra="forbid")
    path: str


class PlatformConfig(BaseModel):
    """Platform agent configuration."""

    model_config = ConfigDict(extra="forbid")
    agent_id: str = ""
    private_key_path: str | None = None
    agent_config_path: str | None = None

    @field_validator("private_key_path")
    @classmethod
    def private_key_path_if_present_must_not_be_empty(cls, value: str | None) -> str | None:
        """Reject blank private key path when a value is provided."""
        if value is None:
            return None
        if not value.strip():
            msg = "platform.private_key_path must not be empty when provided"
            raise ValueError(msg)
        return value


class DisputesConfig(BaseModel):
    """Dispute configuration."""

    model_config = ConfigDict(extra="forbid")
    rebuttal_deadline_seconds: int
    max_claim_length: int
    max_rebuttal_length: int


class JudgeConfig(BaseModel):
    """Single judge configuration."""

    model_config = ConfigDict(extra="forbid")
    id: str
    model: str
    provider: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None


class JudgesConfig(BaseModel):
    """Judge panel configuration."""

    model_config = ConfigDict(extra="forbid")
    panel_size: int
    judges: list[JudgeConfig]

    @field_validator("judges")
    @classmethod
    def validate_panel(cls, judges: list[JudgeConfig], info: Any) -> list[JudgeConfig]:
        """Validate panel size and judge identities."""
        panel_size = info.data.get("panel_size")
        if panel_size is not None:
            if panel_size < 1 or panel_size % 2 == 0:
                msg = "INVALID_PANEL_SIZE: judges.panel_size must be odd and >= 1"
                raise ValueError(msg)
            if panel_size != len(judges):
                msg = "INVALID_PANEL_SIZE: judges.panel_size must equal len(judges)"
                raise ValueError(msg)

        seen: set[str] = set()
        for judge in judges:
            if judge.id in seen:
                msg = f"Duplicate judge id: {judge.id}"
                raise ValueError(msg)
            seen.add(judge.id)
        return judges


class RequestConfig(BaseModel):
    """Request validation configuration."""

    model_config = ConfigDict(extra="forbid")
    max_body_size: int


class DbGatewayConfig(BaseModel):
    """DB Gateway service configuration."""

    model_config = ConfigDict(extra="forbid")
    url: str
    timeout_seconds: int


class Settings(BaseModel):
    """Root configuration container."""

    model_config = ConfigDict(extra="forbid")
    service: ServiceConfig
    server: ServerConfig
    logging: LoggingConfig
    database: DatabaseConfig
    platform: PlatformConfig
    disputes: DisputesConfig
    judges: JudgesConfig
    request: RequestConfig
    db_gateway: DbGatewayConfig | None = None


def get_config_path() -> Path:
    """Resolve configuration path."""
    return resolve_config_path(
        env_var_name="CONFIG_PATH",
        default_filename="config.yaml",
    )


get_settings, clear_settings_cache = create_settings_loader(Settings, get_config_path)  # nosemgrep


def get_safe_config() -> dict[str, Any]:
    """Return redacted config for logs/diagnostics."""
    return get_safe_model_config(get_settings(), REDACTION_MARKER)
