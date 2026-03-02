"""Configuration models for named worker profiles."""

from __future__ import annotations

import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator


def resolve_env_vars(value: str) -> str:
    """Resolve ``${VAR_NAME}`` placeholders from environment variables.

    Performs a single-pass, non-recursive substitution.  Raises immediately
    if a referenced variable is not set.

    Args:
        value: String potentially containing ``${VAR}`` references.

    Returns:
        The string with all placeholders replaced by their env var values.

    Raises:
        ValueError: If a referenced environment variable is not set.
    """
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            msg = (
                f"Environment variable '{var_name}' is not set "
                f"(referenced in config as '${{{var_name}}}')"
            )
            raise ValueError(msg)
        return env_value

    return pattern.sub(_replace, value)


_HANDLE_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class WorkerLLMConfig(BaseModel):
    """LLM endpoint configuration for a worker profile."""

    model_config = ConfigDict(extra="forbid")

    base_url: str
    api_key: SecretStr
    model_id: str
    temperature: float
    max_tokens: int

    @field_validator("base_url")
    @classmethod
    def _validate_base_url_scheme(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            msg = f"base_url must use http:// or https:// scheme, got: {v}"
            raise ValueError(msg)
        return v

    @model_validator(mode="before")
    @classmethod
    def _resolve_api_key_env_vars(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("api_key"), str):
            data["api_key"] = resolve_env_vars(data["api_key"])
        return data


class WorkerBehaviorConfig(BaseModel):
    """Behaviour tuning for a math worker agent."""

    model_config = ConfigDict(extra="forbid")

    scan_interval_seconds: int
    poll_interval_seconds: int
    max_poll_attempts: int
    error_backoff_seconds: int
    min_reward: int
    max_reward: int


class WorkerProfile(BaseModel):
    """A named worker profile combining identity, LLM, and behaviour."""

    model_config = ConfigDict(extra="forbid")

    handle: str
    type: str
    llm: WorkerLLMConfig
    behavior: WorkerBehaviorConfig

    @field_validator("handle")
    @classmethod
    def _validate_handle(cls, v: str) -> str:
        if not _HANDLE_RE.match(v):
            msg = f"Worker handle must match [a-zA-Z0-9_-]+, got: {v!r}"
            raise ValueError(msg)
        return v

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        allowed = {"math_worker"}
        if v not in allowed:
            msg = f"Unknown worker type {v!r}, must be one of: {allowed}"
            raise ValueError(msg)
        return v
