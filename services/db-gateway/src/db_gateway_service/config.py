"""
Configuration management for the Database Gateway service.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from service_auth.gateway import GatewayTrustConfig
from service_commons.config import (
    REDACTION_MARKER,
    create_settings_loader,
    get_safe_model_config,
)
from service_commons.config import (
    get_config_path as resolve_config_path,
)
from service_commons.telemetry import TelemetryConfig

from db_gateway_service.schema import SQLITE_MAX_INTEGER

# Protocol representability bound: the last complete second before year 10000.
_RecoverySeconds = Annotated[int, Field(strict=True, ge=1, le=253402300799)]


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


class BoardAuthorityConfig(BaseModel):
    """Receiver-owned Task Board actor authority configuration."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    platform_public_key_path: Path
    deadline_actor_id: str

    @field_validator("platform_public_key_path", mode="before")
    @classmethod
    def parse_platform_public_key_path(cls, value: object) -> Path:
        """Accept an explicit YAML path string without scalar coercion."""
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            return Path(value)
        msg = "platform_public_key_path must be a path string"
        raise ValueError(msg)

    @field_validator("platform_public_key_path")
    @classmethod
    def validate_platform_public_key_path(cls, value: Path) -> Path:
        """Require a non-blank canonical public-key filename."""
        if not str(value).strip() or value.suffix != ".pub":
            msg = "platform_public_key_path must be a non-blank canonical .pub path"
            raise ValueError(msg)
        return value

    @field_validator("deadline_actor_id")
    @classmethod
    def validate_deadline_actor_id(cls, value: str) -> str:
        """Require one explicit stable system actor identifier."""
        if not value.strip() or value != value.strip():
            msg = "deadline_actor_id must be a non-blank canonical string"
            raise ValueError(msg)
        return value


class BoardEffectRecoveryConfig(BaseModel):
    """Gateway-owned delivery limits; every value is explicitly configured."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    current_task_board_caller_kid: str
    max_attempts_per_delivery: Annotated[int, Field(strict=True, ge=1, le=SQLITE_MAX_INTEGER)]
    initial_backoff_seconds: _RecoverySeconds
    max_backoff_seconds: _RecoverySeconds
    lease_duration_seconds: _RecoverySeconds
    evidence_interval_seconds: _RecoverySeconds
    gateway_timeout_seconds: _RecoverySeconds
    bank_timeout_seconds: _RecoverySeconds

    @field_validator("current_task_board_caller_kid")
    @classmethod
    def validate_current_caller(cls, value: str) -> str:
        """Require the exact configured principal rather than whitespace normalization."""
        if not value or value != value.strip():
            raise ValueError("current_task_board_caller_kid must be a nonblank canonical string")
        return value

    @model_validator(mode="after")
    def validate_scheduling_bounds(self) -> BoardEffectRecoveryConfig:
        """Reject retry ordering and lease budgets that cannot cover dependency calls."""
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must cover initial_backoff_seconds")
        if self.lease_duration_seconds <= 2 * (
            self.gateway_timeout_seconds + self.bank_timeout_seconds
        ):
            raise ValueError("lease_duration_seconds must exceed twice the dependency timeout sum")
        return self


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
    database: DatabaseConfig
    request: RequestConfig
    auth: GatewayTrustConfig
    recovery: RecoveryConfig
    board_authority: BoardAuthorityConfig
    board_effect_recovery: BoardEffectRecoveryConfig

    @model_validator(mode="after")
    def validate_recovery_principal(self) -> Settings:
        """Reject unreachable recovery ownership before any service state is opened."""
        caller = self.auth.trusted_callers.get(
            self.board_effect_recovery.current_task_board_caller_kid
        )
        if caller is None or "gateway.board.write" not in caller.capabilities:
            raise ValueError(
                "current_task_board_caller_kid must have gateway.board.write capability"
            )
        return self


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
