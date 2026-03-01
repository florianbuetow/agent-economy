# Phase 2 — Foundation Files

## Working Directory

All paths relative to `services/db-gateway/`.

---

## File 1: `src/db_gateway_service/__init__.py`

Overwrite the existing empty file with:

```python
"""Database Gateway Service — write serialization layer for the shared economy.db."""

__version__ = "0.1.0"
```

---

## File 2: `src/db_gateway_service/config.py`

Create this file:

```python
"""
Configuration management for the Database Gateway service.

Loads configuration from YAML with ZERO defaults.
Every value must be explicitly specified or startup fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict
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
    request: RequestConfig


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
```

---

## File 3: `src/db_gateway_service/logging.py`

Create this file:

```python
"""Structured JSON logging."""

from __future__ import annotations

from typing import TYPE_CHECKING

from service_commons.logging import (
    VALID_LOG_LEVELS,
    JSONFormatter,
    get_named_logger,
    setup_logging,
)

if TYPE_CHECKING:
    import logging

__all__ = [
    "VALID_LOG_LEVELS",
    "JSONFormatter",
    "get_logger",
    "setup_logging",
]


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    from db_gateway_service.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    return get_named_logger(settings.service.name, name)
```

---

## File 4: `src/db_gateway_service/schemas.py`

Create this file:

```python
"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Response model for GET /health."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]
    uptime_seconds: float
    started_at: str
    database_size_bytes: int
    total_events: int


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(extra="forbid")
    error: str
    message: str
    details: dict[str, object]
```

**Key difference from Identity service**: `HealthResponse` includes `database_size_bytes` and `total_events` instead of `registered_agents`.

---

## Verification

```bash
cd services/db-gateway && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.
