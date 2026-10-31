"""
Configuration management for the UI service.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

import re
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator
from service_commons.config import (
    REDACTION_MARKER,
    get_safe_model_config,
    load_settings,
    load_yaml_config,
)
from service_commons.config import (
    get_config_path as resolve_config_path,
)
from service_commons.telemetry import TelemetryConfig


class ServiceConfig(BaseModel):
    """Service identity configuration."""

    model_config = ConfigDict(extra="forbid")
    name: str
    version: str


class ServerConfig(BaseModel):
    """HTTP server configuration.

    ``host`` is the Q-3 security boundary (see
    docs/plans/2026-07-10-q3-proxy-exposure-decision.md): /api/proxy/* is
    unauthenticated, so 127.0.0.1-only binding is what keeps it
    local-single-user. No shared-secret header exists yet — add one before
    ever binding this service to a non-loopback address.
    """

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


class RecoveryConfig(BaseModel):
    """Explicit maintenance coordination configuration."""

    model_config = ConfigDict(extra="forbid")
    maintenance_lock_path: Path


def _loopback_hostname(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class BrowserSecurityConfig(BaseModel):
    """Explicit authority and response policy for the local operator browser."""

    model_config = ConfigDict(extra="forbid", strict=True)
    allowed_hosts: list[str]
    allowed_origins: list[str]
    csrf_header_name: str
    csrf_header_value: str
    content_security_policy: str

    @field_validator("allowed_hosts")
    @classmethod
    def hosts_must_be_local_authorities(cls, values: list[str]) -> list[str]:
        if not values or len(set(values)) != len(values):
            raise ValueError("allowed_hosts must be explicit and unique")
        for value in values:
            host = value[1:-1] if value.startswith("[") and value.endswith("]") else value
            if (
                value != value.strip()
                or not value.isascii()
                or (":" in value and not value.startswith("["))
                or not _loopback_hostname(host)
            ):
                raise ValueError("allowed_hosts must contain only localhost or loopback IP hosts")
        return values

    @field_validator("allowed_origins")
    @classmethod
    def origins_must_be_exact_and_local(cls, values: list[str]) -> list[str]:
        if not values or len(set(values)) != len(values):
            raise ValueError("allowed_origins must be explicit and unique")
        for value in values:
            parsed = urlsplit(value)
            port = parsed.port
            if (
                not value.isascii()
                or any(character.isspace() for character in value)
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or not _loopback_hostname(parsed.hostname)
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
                or value != f"{parsed.scheme}://{parsed.netloc}"
                or parsed.netloc.endswith(":")
                or (port is not None and port <= 0)
            ):
                raise ValueError("allowed_origins must contain exact local HTTP(S) origins")
        return values

    @field_validator("csrf_header_name")
    @classmethod
    def marker_header_must_be_custom(cls, value: str) -> str:
        if re.fullmatch(r"X-[A-Za-z0-9-]+", value, flags=re.IGNORECASE) is None:
            raise ValueError("csrf_header_name must be an ASCII custom X- header")
        if value.lower().startswith("x-forwarded-"):
            raise ValueError("csrf_header_name must not alter proxy forwarding metadata")
        return value

    @field_validator("csrf_header_value", "content_security_policy")
    @classmethod
    def response_values_must_be_printable_ascii(cls, value: str) -> str:
        if (
            not value
            or value != value.strip()
            or not value.isascii()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("browser header values must be non-blank printable ASCII")
        return value


class UserAgentConfig(BaseModel):
    """User agent configuration."""

    model_config = ConfigDict(extra="forbid")
    agent_config_path: str
    # Roster handle the UI's UserAgent signs proxy actions as (Q-2). A
    # distinct economic identity from "platform" — see
    # docs/plans/2026-07-10-q2-operator-identity-decision.md. Genesis funding
    # for this identity is NOT UI config's concern (Q-9) — see
    # docs/plans/2026-07-10-q9-treasury-bootstrap-decision.md and
    # agents/src/treasury_provision_cli.
    handle: str

    @field_validator("agent_config_path", "handle")
    @classmethod
    def signing_configuration_must_not_be_blank(cls, value: str) -> str:
        """Require explicit load-only operator credential configuration."""
        if not value.strip():
            msg = "operator signing configuration must not be blank"
            raise ValueError(msg)
        return value


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
    sse: SSEConfig
    frontend: FrontendConfig
    request: RequestConfig
    browser_security: BrowserSecurityConfig
    user_agent: UserAgentConfig
    recovery: RecoveryConfig

    @field_validator("server")
    @classmethod
    def server_must_bind_to_loopback(cls, value: ServerConfig) -> ServerConfig:
        try:
            is_loopback = ip_address(value.host).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            msg = (
                "UI server.host must be a loopback IP because no complete "
                "authenticated non-loopback deployment mode exists"
            )
            raise ValueError(msg)
        return value


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
    user_agent_config = yaml_config.get("user_agent")
    if isinstance(user_agent_config, dict):
        # Backfill keys missing from a partial user_agent block (exception #10,
        # WP-08) with the canonical values from the service config.yaml. This
        # only fires when the section is present but incomplete — an entirely
        # absent user_agent section is NOT backfilled: it falls straight
        # through to load_settings() below, which fails fast (Settings.user_agent
        # is a required field with no default), per this module's own contract
        # ("ZERO defaults... missing fields cause immediate startup failure").
        default_user_agent = _load_default_user_agent_config()
        for key, value in default_user_agent.items():
            user_agent_config.setdefault(key, value)
    return load_settings(Settings, yaml_config)


def clear_settings_cache() -> None:
    """Clear cached settings for test isolation."""
    get_settings.cache_clear()


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
