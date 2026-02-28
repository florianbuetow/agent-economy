# Observatory Backend Scaffold — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Scaffold the observatory backend service in `agent-economy/` so it boots, serves `GET /health`, and passes CI.

**Architecture:** FastAPI service following the exact patterns from `agent-economy-test/services/identity/`. Read-only service (GET endpoints only) that will eventually read from a shared SQLite database via aiosqlite. No authentication, no middleware (read-only service has no POST/PUT/PATCH).

**Tech Stack:** Python 3.12, FastAPI, uvicorn, aiosqlite, sse-starlette, service-commons (shared lib), pydantic, uv

**Reference repos:**
- Patterns: `/Users/ryanzidago/Projects/agent-economy-group/agent-economy-test/`
- Target: `/Users/ryanzidago/Projects/agent-economy-group/agent-economy/.claude/worktrees/observatory-scaffold-v2/`
- API spec: `/Users/ryanzidago/Projects/agent-economy-group/agent-economy-test/docs/specifications/service-api/observatory-service-specs.md`
- Test spec: `/Users/ryanzidago/Projects/agent-economy-group/agent-economy-test/docs/specifications/service-tests/observatory-service-tests.md`
- SQL schema: `/Users/ryanzidago/Projects/agent-economy-group/agent-economy-test/docs/specifications/schema.sql`
- Identity service (reference pattern): `/Users/ryanzidago/Projects/agent-economy-group/agent-economy-test/services/identity/`

**Working directory:** `/Users/ryanzidago/Projects/agent-economy-group/agent-economy/.claude/worktrees/observatory-scaffold-v2/`

**IMPORTANT CONVENTIONS:**
- No default parameter values in `src/` (semgrep rule enforces this)
- All config values explicit in YAML — no defaults in code
- `ConfigDict(extra="forbid")` on all Pydantic models
- `from __future__ import annotations` at top of every module
- Use `# nosemgrep` only on `create_settings_loader` call (established pattern)
- Strict mypy and pyright

---

### Task 1: Copy shared assets from agent-economy-test

**Files:**
- Copy: `libs/service-commons/` (entire directory)
- Copy: `docs/specifications/service-api/observatory-service-specs.md`
- Copy: `docs/specifications/service-tests/observatory-service-tests.md`
- Copy: `docs/specifications/schema.sql`
- Copy: `config/codespell/ignore.txt`
- Copy: `config/semgrep/no-default-values.yml`
- Copy: `config/semgrep/no-noqa.yml`
- Copy: `config/semgrep/no-type-suppression.yml`

**Step 1: Copy all shared assets**

```bash
WORKTREE="/Users/ryanzidago/Projects/agent-economy-group/agent-economy/.claude/worktrees/observatory-scaffold-v2"
SOURCE="/Users/ryanzidago/Projects/agent-economy-group/agent-economy-test"

# service-commons
mkdir -p "$WORKTREE/libs"
cp -r "$SOURCE/libs/service-commons" "$WORKTREE/libs/service-commons"

# specifications
mkdir -p "$WORKTREE/docs/specifications/service-api"
mkdir -p "$WORKTREE/docs/specifications/service-tests"
cp "$SOURCE/docs/specifications/service-api/observatory-service-specs.md" "$WORKTREE/docs/specifications/service-api/"
cp "$SOURCE/docs/specifications/service-tests/observatory-service-tests.md" "$WORKTREE/docs/specifications/service-tests/"
cp "$SOURCE/docs/specifications/schema.sql" "$WORKTREE/docs/specifications/"

# config
mkdir -p "$WORKTREE/config/codespell"
mkdir -p "$WORKTREE/config/semgrep"
cp "$SOURCE/config/codespell/ignore.txt" "$WORKTREE/config/codespell/"
cp "$SOURCE/config/semgrep/no-default-values.yml" "$WORKTREE/config/semgrep/"
cp "$SOURCE/config/semgrep/no-noqa.yml" "$WORKTREE/config/semgrep/"
cp "$SOURCE/config/semgrep/no-type-suppression.yml" "$WORKTREE/config/semgrep/"
```

**Step 2: Verify copied files exist**

```bash
ls "$WORKTREE/libs/service-commons/pyproject.toml"
ls "$WORKTREE/docs/specifications/schema.sql"
ls "$WORKTREE/config/semgrep/no-default-values.yml"
```

Expected: All files exist, no errors.

**Step 3: Commit**

```bash
cd "$WORKTREE"
git add libs/ docs/specifications/ config/
git commit -m "chore: copy shared assets from agent-economy-test

Copy service-commons library, observatory specs, SQL schema,
and config (codespell, semgrep) needed for observatory service."
```

---

### Task 2: Create service config files

**Files:**
- Create: `services/observatory/config.yaml`
- Create: `services/observatory/pyproject.toml`
- Create: `services/observatory/pyrightconfig.json`

**Step 1: Create config.yaml**

Create `services/observatory/config.yaml`:

```yaml
# Observatory Service Configuration
# Environment variable overrides use prefix: OBSERVATORY__

service:
  name: "observatory"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8006
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

database:
  path: "data/economy.db"

sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50

frontend:
  dist_path: "frontend/dist"

request:
  max_body_size: 1572864
```

**Step 2: Create pyproject.toml**

Create `services/observatory/pyproject.toml` — follow identity pattern exactly but with observatory-specific dependencies:

```toml
[project]
name = "observatory-service"
version = "0.1.0"
description = "Read-only observatory dashboard for the Agent Task Economy"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "starlette>=0.47.2",
    "pydantic>=2.10.0",
    "service-commons",
    "aiosqlite>=0.21.0",
    "sse-starlette>=2.2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=6.0.0",
    "pytest-asyncio>=0.24.0",
    "pytestarch[visualization]>=2.0.0",
    "httpx>=0.28.0",
    "ruff>=0.8.0",
    "mypy>=1.13.0",
    "pyright>=1.1.390",
    "bandit>=1.7.0",
    "deptry>=0.21.0",
    "codespell>=2.3.0",
    "semgrep>=1.99.0",
    "pip-audit>=2.7.0",
    "pygount>=1.8.0",
    "types-PyYAML>=6.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv.sources]
service-commons = { path = "../../libs/service-commons", editable = true }

[tool.hatch.build.targets.wheel]
packages = ["src/observatory_service"]

# === Ruff ===
[tool.ruff]
target-version = "py312"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = [
    "E",      # pycodestyle errors
    "W",      # pycodestyle warnings
    "F",      # Pyflakes
    "I",      # isort
    "B",      # flake8-bugbear
    "C4",     # flake8-comprehensions
    "UP",     # pyupgrade
    "ARG",    # flake8-unused-arguments
    "SIM",    # flake8-simplify
    "TCH",    # flake8-type-checking
    "PTH",    # flake8-use-pathlib
    "ERA",    # eradicate (commented out code)
    "PL",     # pylint
    "RUF",    # Ruff-specific rules
]
ignore = [
    "PLR0913",  # Too many arguments
    "PLR2004",  # Magic value comparison
]

[tool.ruff.lint.isort]
known-first-party = ["observatory_service"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false
line-ending = "auto"

# === Mypy ===
[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_ignores = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
disallow_untyped_decorators = true
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_configs = true
show_error_codes = true
files = ["src"]

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false

[[tool.mypy.overrides]]
module = "aiosqlite.*"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "sse_starlette.*"
ignore_missing_imports = true

# === Bandit ===
[tool.bandit]
exclude_dirs = ["tests", ".venv"]
skips = ["B101"]

# === Pytest ===
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
asyncio_mode = "auto"
addopts = "-v --tb=short --strict-markers"
markers = [
    "unit: Unit tests (fast, isolated, no external dependencies)",
    "integration: Integration tests (require running services)",
    "slow: Tests that take more than 1 second",
    "performance: Performance benchmark tests",
    "architecture: Architecture import rule tests",
]

# === Coverage ===
[tool.coverage.run]
source = ["src"]
branch = true
omit = ["tests/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
    "if __name__ == .__main__.:",
]
fail_under = 80
show_missing = true

# === Deptry ===
[tool.deptry]
extend_exclude = [".venv", "tests"]

[tool.deptry.per_rule_ignores]
DEP002 = [
    "uvicorn",
    "pytest",
    "pytest-cov",
    "pytest-asyncio",
    "pytestarch",
    "httpx",
    "ruff",
    "mypy",
    "pyright",
    "bandit",
    "deptry",
    "codespell",
    "semgrep",
    "pip-audit",
    "pygount",
    "types-PyYAML",
]
```

**Step 3: Create pyrightconfig.json**

Create `services/observatory/pyrightconfig.json`:

```json
{
  "include": ["src"],
  "exclude": ["**/__pycache__", ".venv"],
  "typeCheckingMode": "strict",
  "pythonVersion": "3.12",
  "reportMissingTypeStubs": false,
  "reportUnknownMemberType": false,
  "reportUnknownArgumentType": false,
  "reportUnknownVariableType": false
}
```

**Step 4: Verify directory structure**

```bash
ls services/observatory/config.yaml services/observatory/pyproject.toml services/observatory/pyrightconfig.json
```

Expected: All three files listed.

**Step 5: Commit**

```bash
git add services/observatory/config.yaml services/observatory/pyproject.toml services/observatory/pyrightconfig.json
git commit -m "chore: add observatory service config files

config.yaml with observatory-specific settings (port 8006, SSE config,
database path, frontend dist path). pyproject.toml with aiosqlite and
sse-starlette dependencies. pyrightconfig.json for strict type checking."
```

---

### Task 3: Create core Python modules

**Files:**
- Create: `services/observatory/src/observatory_service/__init__.py`
- Create: `services/observatory/src/observatory_service/config.py`
- Create: `services/observatory/src/observatory_service/logging.py`
- Create: `services/observatory/src/observatory_service/core/__init__.py`
- Create: `services/observatory/src/observatory_service/core/state.py`
- Create: `services/observatory/src/observatory_service/core/lifespan.py`
- Create: `services/observatory/src/observatory_service/core/exceptions.py`

**Step 1: Create __init__.py**

Create `services/observatory/src/observatory_service/__init__.py`:

```python
"""Observatory Service — Read-only dashboard for the Agent Task Economy."""

__version__ = "0.1.0"
```

**Step 2: Create config.py**

Create `services/observatory/src/observatory_service/config.py`:

Follow identity pattern exactly. Config sections: service, server, logging, database, sse, frontend, request.

```python
"""
Configuration management for the observatory service.

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


class SSEConfig(BaseModel):
    """Server-Sent Events configuration."""

    model_config = ConfigDict(extra="forbid")
    poll_interval_seconds: int
    keepalive_interval_seconds: int
    batch_size: int


class FrontendConfig(BaseModel):
    """Frontend static file serving configuration."""

    model_config = ConfigDict(extra="forbid")
    dist_path: str


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
    sse: SSEConfig
    frontend: FrontendConfig
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

**Step 3: Create logging.py**

Create `services/observatory/src/observatory_service/logging.py`:

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

from observatory_service.config import get_settings

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

**Step 4: Create core/__init__.py**

Create `services/observatory/src/observatory_service/core/__init__.py`:

```python
"""Core application infrastructure."""
```

**Step 5: Create core/state.py**

Create `services/observatory/src/observatory_service/core/state.py`:

The observatory holds no service-layer object yet (database connection will come later). For now, AppState just tracks uptime.

```python
"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def started_at(self) -> str:
        """ISO format start time."""
        return self.start_time.isoformat(timespec="seconds").replace("+00:00", "Z")


# Global application state instance
_state_container: dict[str, AppState | None] = {"app_state": None}


def get_app_state() -> AppState:
    """Get the current application state."""
    app_state = _state_container["app_state"]
    if app_state is None:
        msg = "Application state not initialized"
        raise RuntimeError(msg)
    return app_state


def init_app_state() -> AppState:
    """Initialize application state. Called during startup."""
    app_state = AppState()
    _state_container["app_state"] = app_state
    return app_state


def reset_app_state() -> None:
    """Reset application state. Used in testing."""
    _state_container["app_state"] = None
```

**Step 6: Create core/lifespan.py**

Create `services/observatory/src/observatory_service/core/lifespan.py`:

```python
"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from observatory_service.config import get_settings
from observatory_service.core.state import init_app_state
from observatory_service.logging import get_logger, setup_logging

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # === STARTUP ===
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name)
    logger = get_logger(__name__)

    init_app_state()

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Service shutting down")
```

**Step 7: Create core/exceptions.py**

Create `services/observatory/src/observatory_service/core/exceptions.py`:

```python
"""Custom exception handlers for consistent error responses."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError
from service_commons.exceptions import (
    register_exception_handlers as register_common_exception_handlers,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from observatory_service.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from starlette.types import ExceptionHandler

__all__ = ["ServiceError", "register_exception_handlers"]


async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    """Handle ServiceError exceptions."""
    logger = get_logger(__name__)
    logger.warning(
        "Service error",
        extra={
            "error_code": exc.error,
            "status_code": exc.status_code,
            "path": str(request.url.path),
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "message": exc.message, "details": exc.details},
    )


async def unhandled_exception_handler(request: Request, _exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    logger = get_logger(__name__)
    logger.exception("Unhandled exception", extra={"path": str(request.url.path)})
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred",
            "details": {},
        },
    )


async def http_exception_handler(
    _request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Handle Starlette HTTP exceptions (e.g., 405 from router)."""
    if exc.status_code == 405:
        return JSONResponse(
            status_code=405,
            content={
                "error": "METHOD_NOT_ALLOWED",
                "message": "Method not allowed",
                "details": {},
            },
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "HTTP_ERROR",
            "message": str(exc.detail),
            "details": {},
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the app."""
    register_common_exception_handlers(
        app,
        ServiceError,
        service_error_handler,
        unhandled_exception_handler,
    )
    app.add_exception_handler(
        StarletteHTTPException,
        cast("ExceptionHandler", http_exception_handler),
    )
```

**Step 8: Commit**

```bash
git add services/observatory/src/
git commit -m "feat: add observatory core Python modules

__init__.py, config.py (settings with SSE/frontend/database sections),
logging.py, core/state.py (AppState singleton), core/lifespan.py
(startup/shutdown lifecycle), core/exceptions.py (error handlers)."
```

---

### Task 4: Create schemas, health router, and app factory

**Files:**
- Create: `services/observatory/src/observatory_service/schemas.py`
- Create: `services/observatory/src/observatory_service/routers/__init__.py`
- Create: `services/observatory/src/observatory_service/routers/health.py`
- Create: `services/observatory/src/observatory_service/app.py`

**Step 1: Create schemas.py**

Create `services/observatory/src/observatory_service/schemas.py`:

Only the HealthResponse for now. Other schemas will be added when implementing endpoints.

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
    latest_event_id: int
    database_readable: bool


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(extra="forbid")
    error: str
    message: str
    details: dict[str, object]
```

**Step 2: Create routers/__init__.py**

Create `services/observatory/src/observatory_service/routers/__init__.py`:

```python
"""API route handlers."""
```

**Step 3: Create routers/health.py**

Create `services/observatory/src/observatory_service/routers/health.py`:

```python
"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from observatory_service.core.state import get_app_state
from observatory_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        latest_event_id=0,
        database_readable=False,
    )
```

**Step 4: Create app.py**

Create `services/observatory/src/observatory_service/app.py`:

```python
"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from observatory_service.config import get_settings
from observatory_service.core.exceptions import register_exception_handlers
from observatory_service.core.lifespan import lifespan
from observatory_service.routers import health


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI instance with all routers registered.
    """
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.service.name} Service",
        version=settings.service.version,
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    app.include_router(health.router, tags=["Operations"])

    return app
```

**Step 5: Commit**

```bash
git add services/observatory/src/
git commit -m "feat: add schemas, health router, and app factory

HealthResponse with observatory-specific fields (latest_event_id,
database_readable). App factory follows standard create_app() pattern."
```

---

### Task 5: Create test infrastructure and write health test

**Files:**
- Create: `services/observatory/tests/conftest.py`
- Create: `services/observatory/tests/unit/conftest.py`
- Create: `services/observatory/tests/unit/routers/conftest.py`
- Create: `services/observatory/tests/unit/routers/test_health.py`
- Create: `services/observatory/tests/unit/test_config.py`
- Create: `services/observatory/tests/integration/conftest.py`
- Create: `services/observatory/tests/performance/conftest.py`

**Step 1: Create tests/conftest.py**

Create `services/observatory/tests/conftest.py`:

```python
"""Shared test configuration."""
```

**Step 2: Create tests/unit/conftest.py**

Create `services/observatory/tests/unit/conftest.py`:

```python
"""Unit test fixtures — cache clearing."""

import pytest

from observatory_service.config import clear_settings_cache
from observatory_service.core.state import reset_app_state


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear settings cache and app state between tests."""
    clear_settings_cache()
    reset_app_state()
    yield
    clear_settings_cache()
    reset_app_state()
```

**Step 3: Create tests/unit/routers/conftest.py**

Create `services/observatory/tests/unit/routers/conftest.py`:

```python
"""Router test fixtures — app with lifespan and async client."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from observatory_service.app import create_app
from observatory_service.config import clear_settings_cache
from observatory_service.core.lifespan import lifespan
from observatory_service.core.state import reset_app_state


@pytest.fixture
async def app(tmp_path):
    """Create a test app with temporary config."""
    config_content = f"""
service:
  name: "observatory"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8006
  log_level: "info"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "{tmp_path}/test.db"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  dist_path: "{tmp_path}/dist"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        yield test_app

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


@pytest.fixture
async def client(app):
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

**Step 4: Create test_health.py**

Create `services/observatory/tests/unit/routers/test_health.py`:

```python
"""Tests for the health endpoint."""

import pytest


@pytest.mark.unit
async def test_health_returns_ok(client):
    """GET /health returns 200 with expected fields."""
    response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["uptime_seconds"], (int, float))
    assert isinstance(data["started_at"], str)
    assert "latest_event_id" in data
    assert "database_readable" in data
```

**Step 5: Create test_config.py**

Create `services/observatory/tests/unit/test_config.py`:

```python
"""Tests for configuration loading."""

import os

import pytest

from observatory_service.config import Settings, clear_settings_cache, get_settings


@pytest.mark.unit
def test_config_loads_from_yaml(tmp_path):
    """Settings loads all sections from a valid config file."""
    config_content = """
service:
  name: "observatory"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8006
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/economy.db"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  dist_path: "frontend/dist"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert settings.service.name == "observatory"
    assert settings.server.port == 8006
    assert settings.sse.poll_interval_seconds == 1
    assert settings.frontend.dist_path == "frontend/dist"

    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_rejects_extra_fields(tmp_path):
    """Settings rejects unknown configuration keys."""
    config_content = """
service:
  name: "observatory"
  version: "0.1.0"
  unknown_field: "should fail"
server:
  host: "0.0.0.0"
  port: 8006
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/economy.db"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  dist_path: "frontend/dist"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()

    with pytest.raises(Exception):
        get_settings()

    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)
```

**Step 6: Create integration and performance conftest stubs**

Create `services/observatory/tests/integration/conftest.py`:

```python
"""Integration test fixtures."""
```

Create `services/observatory/tests/performance/conftest.py`:

```python
"""Performance test fixtures."""
```

**Step 7: Verify tests pass**

```bash
cd services/observatory
uv sync --all-extras
uv run pytest tests/unit -m unit -v
```

Expected: All tests pass (2 config tests + 1 health test).

**Step 8: Commit**

```bash
git add tests/
git commit -m "test: add test infrastructure and initial unit tests

conftest fixtures for cache clearing and test app setup.
Tests for health endpoint and config loading."
```

---

### Task 6: Create justfile

**Files:**
- Create: `services/observatory/justfile`

**Step 1: Create justfile**

Create `services/observatory/justfile` — follow the identity service justfile pattern exactly, but with observatory-specific values (SERVICE_NAME := "observatory_service", PORT := "8006", service name in messages).

Copy the identity justfile and replace:
- `SERVICE_NAME := "identity_service"` → `SERVICE_NAME := "observatory_service"`
- `PORT := "8001"` → `PORT := "8006"`
- All `identity` references in messages and docker commands → `observatory`
- Port `8001` references → `8006`

The full justfile is long (450 lines). It follows the exact same structure as the identity service justfile. Key targets: `init`, `run`, `kill`, `test`, `test-unit`, `test-integration`, `ci`, `ci-quiet`, `code-format`, `code-style`, `code-typecheck`, `code-lspchecks`, `code-security`, `code-deptry`, `code-spell`, `code-semgrep`, `code-audit`.

**Step 2: Verify just commands work**

```bash
cd services/observatory
just init
just code-format
just code-style
```

Expected: All commands succeed.

**Step 3: Commit**

```bash
git add services/observatory/justfile
git commit -m "chore: add observatory service justfile

Standard service commands: init, run, test, ci, code quality checks.
Port 8006, service name observatory."
```

---

### Task 7: Create Dockerfile and docker-compose.yml

**Files:**
- Create: `services/observatory/Dockerfile`
- Create: `docker-compose.yml` (root level)

**Step 1: Create Dockerfile**

Create `services/observatory/Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /repo/services/observatory

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/observatory/pyproject.toml services/observatory/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/observatory/config.yaml .
COPY services/observatory/src/ src/

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "observatory_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
```

**Step 2: Create docker-compose.yml**

Create `docker-compose.yml` at the repo root:

```yaml
services:
  observatory:
    build:
      context: .
      dockerfile: services/observatory/Dockerfile
    ports:
      - "8006:8006"
    environment:
      - CONFIG_PATH=/repo/services/observatory/config.yaml
    volumes:
      - ./data:/repo/data:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8006/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

**Step 3: Commit**

```bash
git add services/observatory/Dockerfile docker-compose.yml
git commit -m "chore: add Dockerfile and docker-compose.yml

Single-stage Python 3.12 Dockerfile (frontend build stage added later).
Docker Compose with read-only data volume mount on port 8006."
```

---

### Task 8: Run full CI verification

**Step 1: Run just ci-quiet**

```bash
cd services/observatory
just ci-quiet
```

Expected: All checks pass:
- Init
- Code-format
- Code-style
- Code-typecheck (mypy)
- Code-security (bandit)
- Code-deptry
- Code-spell
- Code-semgrep
- Code-audit
- Unit tests
- Code-lspchecks (pyright)

**Step 2: Verify service boots**

```bash
cd services/observatory
uv run uvicorn observatory_service.app:create_app --factory --host 0.0.0.0 --port 8006 &
sleep 2
curl -s http://localhost:8006/health | python3 -m json.tool
kill %1
```

Expected: Returns `{"status": "ok", "uptime_seconds": ..., "started_at": "...", "latest_event_id": 0, "database_readable": false}`

**Step 3: Fix any issues found**

If any CI check fails, fix the issue and re-run. Common issues:
- Semgrep may flag default parameter values — ensure none exist in `src/`
- Mypy may need type annotations adjusted
- Codespell may flag domain terms — add to `config/codespell/ignore.txt`

**Step 4: Final commit if fixes were needed**

```bash
git add -A
git commit -m "fix: resolve CI issues from verification"
```

---

### Task 9: Create placeholder service modules

These are empty modules that establish the file structure for future implementation.

**Files:**
- Create: `services/observatory/src/observatory_service/services/__init__.py`
- Create: `services/observatory/src/observatory_service/services/database.py`
- Create: `services/observatory/src/observatory_service/services/metrics.py`
- Create: `services/observatory/src/observatory_service/services/events.py`
- Create: `services/observatory/src/observatory_service/services/agents.py`
- Create: `services/observatory/src/observatory_service/services/tasks.py`
- Create: `services/observatory/src/observatory_service/services/quarterly.py`

**Step 1: Create all placeholder service modules**

Each file follows this pattern:

```python
"""<Domain> business logic."""

from __future__ import annotations
```

The `database.py` module gets a slightly more descriptive docstring:

```python
"""Read-only database access via aiosqlite."""

from __future__ import annotations
```

**Step 2: Create placeholder routers**

Create these files (each with just the router and docstring):

- `services/observatory/src/observatory_service/routers/metrics.py`
- `services/observatory/src/observatory_service/routers/events.py`
- `services/observatory/src/observatory_service/routers/agents.py`
- `services/observatory/src/observatory_service/routers/tasks.py`
- `services/observatory/src/observatory_service/routers/quarterly.py`

Each file:

```python
"""<Endpoint> route handlers."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
```

**Step 3: Wire routers into app.py**

Update `services/observatory/src/observatory_service/app.py` to include all routers:

```python
from observatory_service.routers import agents, events, health, metrics, quarterly, tasks

# ... in create_app():
app.include_router(health.router, tags=["Operations"])
app.include_router(metrics.router, prefix="/api", tags=["Metrics"])
app.include_router(events.router, prefix="/api", tags=["Events"])
app.include_router(agents.router, prefix="/api", tags=["Agents"])
app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
app.include_router(quarterly.router, prefix="/api", tags=["Quarterly"])
```

**Step 4: Run CI again**

```bash
cd services/observatory
just ci-quiet
```

Expected: All checks pass.

**Step 5: Commit**

```bash
git add services/observatory/src/
git commit -m "feat: add placeholder service modules and routers

Empty service layer modules (database, metrics, events, agents, tasks,
quarterly) and router stubs wired into the app factory. Establishes
full file structure for future implementation."
```

---

### Task 10: Update root justfile

**Files:**
- Modify: `justfile` (root level)

**Step 1: Add observatory targets to root justfile**

Add targets for initializing and running CI on the observatory service. Follow the existing root justfile conventions (printf for colors, empty echo lines, etc.).

Add these targets:

```just
# Initialize observatory service
init-observatory:
    @echo ""
    @printf "\033[34m=== Initializing Observatory Service ===\033[0m\n"
    @cd services/observatory && just init
    @printf "\033[32m✓ Observatory initialized\033[0m\n"
    @echo ""

# Run observatory CI
ci-observatory:
    @echo ""
    @printf "\033[34m=== Running Observatory CI ===\033[0m\n"
    @cd services/observatory && just ci-quiet
    @printf "\033[32m✓ Observatory CI passed\033[0m\n"
    @echo ""
```

Update the `help` target to include the new targets.

**Step 2: Commit**

```bash
git add justfile
git commit -m "chore: add observatory targets to root justfile"
```

---

## Summary of Deliverables

After all tasks complete:

```
services/observatory/
├── config.yaml
├── justfile
├── pyproject.toml
├── pyrightconfig.json
├── Dockerfile
├── src/observatory_service/
│   ├── __init__.py
│   ├── app.py
│   ├── config.py
│   ├── logging.py
│   ├── schemas.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── state.py
│   │   ├── lifespan.py
│   │   └── exceptions.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── health.py
│   │   ├── metrics.py
│   │   ├── events.py
│   │   ├── agents.py
│   │   ├── tasks.py
│   │   └── quarterly.py
│   └── services/
│       ├── __init__.py
│       ├── database.py
│       ├── metrics.py
│       ├── events.py
│       ├── agents.py
│       ├── tasks.py
│       └── quarterly.py
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── conftest.py
    │   ├── test_config.py
    │   └── routers/
    │       ├── conftest.py
    │       └── test_health.py
    ├── integration/
    │   └── conftest.py
    └── performance/
        └── conftest.py
```

Plus root-level:
- `libs/service-commons/` — shared library
- `docs/specifications/` — observatory specs + schema
- `config/` — codespell + semgrep rules
- `docker-compose.yml` — observatory container
- Updated `justfile` — observatory targets

## Success Criteria

1. `cd services/observatory && just ci-quiet` — all checks pass
2. `uv run uvicorn observatory_service.app:create_app --factory` — starts without error
3. `curl http://localhost:8006/health` — returns 200 with correct shape
4. `docker compose build` — succeeds
