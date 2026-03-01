# Phase 2 — Foundation Files

## Working Directory

All paths relative to `services/task-board/`.

---

## File 1: `src/task_board_service/__init__.py`

Overwrite the existing empty file with:

```python
"""Task Board Service — Task lifecycle management, bidding, contracts, and asset store."""

__version__ = "0.1.0"
```

---

## File 2: `src/task_board_service/config.py`

Create this file:

```python
"""
Configuration management for the task board service.

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


class IdentityConfig(BaseModel):
    """Identity service connection configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    verify_jws_path: str
    timeout_seconds: int


class CentralBankConfig(BaseModel):
    """Central Bank service connection configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    escrow_lock_path: str
    escrow_release_path: str
    timeout_seconds: int


class PlatformConfig(BaseModel):
    """Platform agent configuration for signing escrow operations."""

    model_config = ConfigDict(extra="forbid")
    agent_id: str
    private_key_path: str


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
    assets: AssetsConfig
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

## File 3: `src/task_board_service/logging.py`

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

from task_board_service.config import get_settings

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
    settings = get_settings()
    return get_named_logger(settings.service.name, name)
```

---

## File 4: `src/task_board_service/schemas.py`

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
    total_tasks: int
    tasks_by_status: dict[str, int]


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(extra="forbid")
    error: str
    message: str
    details: dict[str, object]


class TaskResponse(BaseModel):
    """Full task detail response model."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    poster_id: str
    title: str
    spec: str
    reward: int
    bidding_deadline_seconds: int
    deadline_seconds: int
    review_deadline_seconds: int
    status: str
    escrow_id: str
    bid_count: int
    worker_id: str | None
    accepted_bid_id: str | None
    created_at: str
    accepted_at: str | None
    submitted_at: str | None
    approved_at: str | None
    cancelled_at: str | None
    disputed_at: str | None
    dispute_reason: str | None
    ruling_id: str | None
    ruled_at: str | None
    worker_pct: int | None
    ruling_summary: str | None
    expired_at: str | None
    escrow_pending: bool
    bidding_deadline: str
    execution_deadline: str | None
    review_deadline: str | None


class TaskSummary(BaseModel):
    """Summary task model for list views."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    poster_id: str
    title: str
    reward: int
    status: str
    bid_count: int
    worker_id: str | None
    created_at: str
    bidding_deadline: str
    execution_deadline: str | None
    review_deadline: str | None


class TaskListResponse(BaseModel):
    """Response model for GET /tasks."""

    model_config = ConfigDict(extra="forbid")
    tasks: list[TaskSummary]


class BidResponse(BaseModel):
    """Response model for a single bid."""

    model_config = ConfigDict(extra="forbid")
    bid_id: str
    task_id: str
    bidder_id: str
    proposal: str
    submitted_at: str


class BidListResponse(BaseModel):
    """Response model for GET /tasks/{task_id}/bids."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    bids: list[BidResponse]


class AssetResponse(BaseModel):
    """Response model for a single asset."""

    model_config = ConfigDict(extra="forbid")
    asset_id: str
    task_id: str
    uploader_id: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: str


class AssetListResponse(BaseModel):
    """Response model for GET /tasks/{task_id}/assets."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    assets: list[AssetResponse]
```

---

## Verification

```bash
cd services/task-board && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.
