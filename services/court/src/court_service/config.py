"""Configuration management for the court service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
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


class IdentityConfig(BaseModel):
    """Identity service connection configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    verify_jws_path: str
    timeout_seconds: int


class TaskBoardConfig(BaseModel):
    """Task Board service connection configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str


class CentralBankConfig(BaseModel):
    """Central Bank service connection configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str


class ReputationConfig(BaseModel):
    """Reputation service connection configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str


class PlatformConfig(BaseModel):
    """Platform agent configuration."""

    model_config = ConfigDict(extra="forbid")
    agent_id: str
    private_key_path: str | None = None

    @field_validator("agent_id")
    @classmethod
    def agent_id_must_not_be_empty(cls, value: str) -> str:
        """Reject empty platform agent_id at startup."""
        if not value.strip():
            msg = "platform.agent_id must not be empty"
            raise ValueError(msg)
        return value

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
    temperature: float | None = None


class JudgesConfig(BaseModel):
    """Judge panel configuration."""

    model_config = ConfigDict(extra="forbid")
    panel_size: int
    judges: list[JudgeConfig]

    @model_validator(mode="after")
    def validate_panel(self) -> JudgesConfig:
        """Validate panel size and judge identities."""
        if self.panel_size < 1 or self.panel_size % 2 == 0:
            msg = "INVALID_PANEL_SIZE: judges.panel_size must be odd and >= 1"
            raise ValueError(msg)
        if self.panel_size != len(self.judges):
            msg = "INVALID_PANEL_SIZE: judges.panel_size must equal len(judges)"
            raise ValueError(msg)

        seen: set[str] = set()
        for judge in self.judges:
            if judge.id in seen:
                msg = f"Duplicate judge id: {judge.id}"
                raise ValueError(msg)
            seen.add(judge.id)
        return self


class RequestConfig(BaseModel):
    """Request validation configuration."""

    model_config = ConfigDict(extra="forbid")
    max_body_size: int


class Settings(BaseModel):
    """Root configuration container."""

    model_config = ConfigDict(extra="forbid")
    service: ServiceConfig
    server: ServerConfig
    logging: LoggingConfig
    database: DatabaseConfig
    identity: IdentityConfig
    task_board: TaskBoardConfig | None = None
    central_bank: CentralBankConfig | None = None
    reputation: ReputationConfig | None = None
    platform: PlatformConfig
    disputes: DisputesConfig
    judges: JudgesConfig
    request: RequestConfig


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
