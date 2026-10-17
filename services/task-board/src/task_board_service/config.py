"""
Configuration management for the task board service.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
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
    agent_config_path: str


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


class LimitsConfig(BaseModel):
    """Optional legacy limits configuration (asset-storage fallback only).

    ``max_title_length``/``max_spec_length``/``max_reason_length`` were
    removed (GAP-C8/exception #16, WP-11 round 2): title/spec/reason length
    validation in task_creation.py and task_ruling.py uses hardcoded limits,
    never these fields. ``max_file_size``/``max_assets_per_task`` are kept —
    lifespan.py reads them as the legacy fallback when no ``assets:`` section
    is present.
    """

    model_config = ConfigDict(extra="forbid")
    max_file_size: int
    max_assets_per_task: int


class GatewaySigningConfig(GatewaySignerConfig):
    """Task Board's shared strict Gateway signer configuration."""


class DbGatewayConfig(BaseModel):
    """Database gateway configuration."""

    model_config = ConfigDict(extra="forbid")
    url: str
    timeout_seconds: int
    signing: GatewaySigningConfig


class DeadlineEvaluationConfig(BaseModel):
    """Periodic deadline-sweep configuration (Q-5, GAP-A3)."""

    model_config = ConfigDict(extra="forbid")
    evaluation_interval_seconds: int


class RecoveryConfig(BaseModel):
    """Explicit maintenance coordination configuration."""

    model_config = ConfigDict(extra="forbid")
    maintenance_lock_path: Path


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
    db_gateway: DbGatewayConfig
    assets: AssetsConfig | None = None
    limits: LimitsConfig | None = None
    deadline_evaluation: DeadlineEvaluationConfig | None = None
    recovery: RecoveryConfig


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
