# Phase 3 — Foundation Modules

## Working Directory

All paths relative to `services/central-bank/`.

---

## Task B3: Implement __init__.py, logging.py, schemas.py

### Step 3.1: Write __init__.py

Replace the empty `services/central-bank/src/central_bank_service/__init__.py` with:

```python
"""Central Bank Service — Ledger, escrow, and payout service for the agent economy."""

__version__ = "0.1.0"
```

### Step 3.2: Write logging.py

Create `services/central-bank/src/central_bank_service/logging.py`:

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
    from central_bank_service.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    return get_named_logger(settings.service.name, name)
```

### Step 3.3: Write schemas.py

Create `services/central-bank/src/central_bank_service/schemas.py`:

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
    total_accounts: int
    total_escrowed: int


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(extra="forbid")
    error: str
    message: str
    details: dict[str, object]
```

### Step 3.4: Commit

```bash
git add services/central-bank/src/central_bank_service/__init__.py services/central-bank/src/central_bank_service/logging.py services/central-bank/src/central_bank_service/schemas.py
git commit -m "feat(central-bank): add init, logging, and schemas modules"
```

---

## Verification

```bash
cd services/central-bank && uv run ruff check src/ && uv run ruff format --check src/
```
