"""
Shared configuration infrastructure for services.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing."""

    pass


# Keywords that indicate sensitive data (case-insensitive)
SENSITIVE_KEYWORDS: frozenset[str] = frozenset(
    {
        "key",
        "secret",
        "pass",
        "password",
        "token",
        "credential",
        "auth",
        "api_key",
        "apikey",
        "private",
        "bearer",
    }
)

# Compiled regex for efficient matching
_SENSITIVE_PATTERN = re.compile(
    r"(" + "|".join(re.escape(kw) for kw in SENSITIVE_KEYWORDS) + r")",
    re.IGNORECASE,
)

REDACTION_MARKER: str = "[REDACTED]"


def load_yaml_config(config_path: Path) -> dict[str, Any]:
    """
    Load and parse YAML configuration file.

    Args:
        config_path: Path to config.yaml

    Returns:
        Parsed configuration dictionary

    Raises:
        ConfigurationError: If file is missing, empty, or invalid
    """
    if not config_path.exists():
        raise ConfigurationError(
            f"Configuration file not found: {config_path}\n"
            f"Expected location: {config_path.absolute()}\n"
            f"Create the file or set CONFIG_PATH environment variable."
        )

    try:
        with config_path.open() as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Invalid YAML in {config_path}: {e}") from e

    if config is None:
        raise ConfigurationError(
            f"Configuration file is empty: {config_path}\n"
            f"All configuration values must be explicitly specified."
        )

    if not isinstance(config, dict):
        raise ConfigurationError(
            f"Configuration must be a YAML mapping, got {type(config).__name__}"
        )

    return config


def get_config_path(
    env_var_name: str,
    default_filename: str,
) -> Path:
    """
    Determine configuration file path.

    Uses an explicit environment variable if set, otherwise falls back to an
    explicit default filename.

    Args:
        env_var_name: Environment variable to read config path from
        default_filename: Fallback filename to use when env var is not set

    Returns:
        Path to configuration file
    """
    config_path_str = os.environ.get(env_var_name)
    if config_path_str is None:
        config_path_str = default_filename
    return Path(config_path_str)


def load_settings(
    settings_model: type[ModelT],
    yaml_config: dict[str, Any],
) -> ModelT:
    """
    Build typed settings from raw YAML config.

    Args:
        settings_model: Pydantic settings model type
        yaml_config: Parsed YAML configuration

    Returns:
        Validated settings model

    Raises:
        ConfigurationError: If validation fails
    """
    try:
        return settings_model(**yaml_config)
    except ValidationError as e:
        raise ConfigurationError(
            f"Configuration validation failed: {e}\n"
            f"All configuration values must be explicitly specified.\n"
            f"No default values are allowed."
        ) from e


def create_settings_loader(
    settings_model: type[ModelT],
    get_config_path_fn: Callable[[], Path],
) -> tuple[Callable[[], ModelT], Callable[[], None]]:
    """
    Create cached settings getter and cache resetter for a service.

    Args:
        settings_model: Pydantic settings model type
        get_config_path_fn: Function that resolves the config path

    Returns:
        Tuple of (get_settings, clear_settings_cache)
    """

    @lru_cache
    def get_settings() -> ModelT:
        config_path = get_config_path_fn()
        yaml_config = load_yaml_config(config_path)
        return load_settings(settings_model, yaml_config)

    def clear_settings_cache() -> None:
        get_settings.cache_clear()

    return get_settings, clear_settings_cache


def is_sensitive_key(key: str) -> bool:
    """
    Check if a configuration key contains sensitive keywords.

    Args:
        key: Configuration key name

    Returns:
        True if the key likely contains sensitive data
    """
    return bool(_SENSITIVE_PATTERN.search(key))


def redact_sensitive_values(
    data: dict[str, Any],
    redaction_marker: str,
) -> dict[str, Any]:
    """
    Recursively redact sensitive values from configuration.

    Args:
        data: Configuration dictionary
        redaction_marker: String to replace sensitive values

    Returns:
        New dictionary with sensitive values redacted
    """
    result: dict[str, Any] = {}

    for key, value in data.items():
        if is_sensitive_key(key):
            result[key] = redaction_marker
        elif isinstance(value, dict):
            result[key] = redact_sensitive_values(value, redaction_marker)
        elif isinstance(value, list):
            result[key] = [
                redact_sensitive_values(item, redaction_marker) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value

    return result


def get_safe_model_config(
    settings: BaseModel,
    redaction_marker: str,
) -> dict[str, Any]:
    """
    Dump settings with sensitive values redacted.

    Args:
        settings: Typed settings model
        redaction_marker: String to replace sensitive values

    Returns:
        Configuration dictionary safe for logging/API exposure
    """
    raw_config = settings.model_dump()
    return redact_sensitive_values(raw_config, redaction_marker)
