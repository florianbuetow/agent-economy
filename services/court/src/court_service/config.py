"""Configuration management for the court service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, field_validator
from service_auth.gateway import GatewaySignerConfig
from service_commons.config import (
    REDACTION_MARKER,
    create_settings_loader,
    get_safe_model_config,
)
from service_commons.config import get_config_path as resolve_config_path

from court_service.judges.output import validate_rating

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
    agent_id: str = ""
    private_key_path: str | None = None
    agent_config_path: str

    @field_validator("agent_config_path")
    @classmethod
    def agent_config_path_must_not_be_blank(cls, value: str) -> str:
        """Require the load-only platform credential configuration."""
        if not value.strip():
            msg = "platform.agent_config_path must not be blank"
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
    # WP-11 (exception #11): feedback cutoffs and comment cap de-hardcoded off
    # RulingOrchestrator's ruling->feedback-rating mapping.
    feedback_extremely_satisfied_cutoff: int
    feedback_satisfied_cutoff: int
    feedback_comment_max_length: int
    # Plan 0.3: a ruling run owns a dispute through a Gateway judging lease of
    # this many seconds; the reclaim sweep resumes expired or released leases.
    judging_lease_seconds: int
    reclaim_interval_seconds: int


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
    mock_worker_pct: int
    # Plan 3.2: mock judges return these fixed quality findings (dev/test/CI).
    mock_spec_quality: str
    mock_delivery_quality: str
    judges: list[JudgeConfig]

    @field_validator("mock_spec_quality", "mock_delivery_quality")
    @classmethod
    def validate_mock_rating(cls, value: str, info: Any) -> str:
        """Mock findings must use the Reputation rating vocabulary."""
        return validate_rating(str(info.field_name), value)

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


class EvidenceConfig(BaseModel):
    """Plan 3.1: evidence limits and supported text formats for the judge bundle."""

    model_config = ConfigDict(extra="forbid")
    max_total_bytes: int
    max_file_bytes: int
    allowed_extensions: list[str]
    allowed_content_types: list[str]

    @field_validator("max_total_bytes", "max_file_bytes")
    @classmethod
    def limits_must_be_positive(cls, value: int, info: Any) -> int:
        """Reject non-positive byte limits."""
        if value < 1:
            msg = f"evidence.{info.field_name} must be >= 1"
            raise ValueError(msg)
        return value

    @field_validator("allowed_extensions")
    @classmethod
    def extensions_must_be_dotted_lowercase(cls, value: list[str]) -> list[str]:
        """Extensions are compared lowercase and include the leading dot."""
        if not value or any(not e.startswith(".") or e != e.lower() for e in value):
            msg = "evidence.allowed_extensions must be non-empty lowercase '.ext' values"
            raise ValueError(msg)
        return value

    @field_validator("allowed_content_types")
    @classmethod
    def content_types_must_be_lowercase(cls, value: list[str]) -> list[str]:
        """Media types are compared lowercase without parameters."""
        if not value or any(t != t.lower() or ";" in t or "/" not in t for t in value):
            msg = "evidence.allowed_content_types must be non-empty lowercase media types"
            raise ValueError(msg)
        return value


class RequestConfig(BaseModel):
    """Request validation configuration."""

    model_config = ConfigDict(extra="forbid")
    max_body_size: int


class GatewaySigningConfig(GatewaySignerConfig):
    """Court's shared strict Gateway signer configuration."""


class DbGatewayConfig(BaseModel):
    """DB Gateway service configuration."""

    model_config = ConfigDict(extra="forbid")
    url: str
    timeout_seconds: int
    signing: GatewaySigningConfig


class Settings(BaseModel):
    """Root configuration container."""

    model_config = ConfigDict(extra="forbid")
    service: ServiceConfig
    server: ServerConfig
    logging: LoggingConfig
    platform: PlatformConfig
    disputes: DisputesConfig
    judges: JudgesConfig
    evidence: EvidenceConfig
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
