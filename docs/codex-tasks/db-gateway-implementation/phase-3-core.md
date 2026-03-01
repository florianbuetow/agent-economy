# Phase 3 — Core Infrastructure

## Working Directory

All paths relative to `services/db-gateway/`.

---

## File 1: `src/db_gateway_service/core/state.py`

Create this file:

```python
"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    db_writer: DbWriter | None = None

    @property
    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def started_at(self) -> str:
        """ISO format start time."""
        return self.start_time.isoformat(timespec="seconds").replace("+00:00", "Z")


# Global application state instance
_app_state: AppState | None = None


def get_app_state() -> AppState:
    """Get the current application state."""
    if _app_state is None:
        msg = "Application state not initialized"
        raise RuntimeError(msg)
    return _app_state


def init_app_state() -> AppState:
    """Initialize application state. Called during startup."""
    global _app_state  # noqa: PLW0603
    _app_state = AppState()
    return _app_state


def reset_app_state() -> None:
    """Reset application state. Used in testing."""
    global _app_state  # noqa: PLW0603
    _app_state = None
```

---

## File 2: `src/db_gateway_service/core/exceptions.py`

Create this file:

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

from db_gateway_service.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

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


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
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

---

## File 3: `src/db_gateway_service/core/middleware.py`

Create this file:

```python
"""ASGI middleware for request validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


class RequestValidationMiddleware:
    """
    ASGI middleware that validates Content-Type and body size.

    Runs before FastAPI routes. Returns 415 for wrong content-type
    on POST/PUT/PATCH, and 413 for oversized request bodies.
    """

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope.get("method", "GET")  # type: ignore[assignment]

        if method not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        # Check Content-Type header
        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])  # type: ignore[assignment]
        headers: dict[bytes, bytes] = dict(raw_headers)
        content_type = headers.get(b"content-type", b"").decode().lower()

        if not content_type.startswith("application/json"):
            response = JSONResponse(
                status_code=415,
                content={
                    "error": "UNSUPPORTED_MEDIA_TYPE",
                    "message": "Content-Type must be application/json",
                    "details": {},
                },
            )
            await response(scope, receive, send)
            return

        # Read and buffer body, checking size
        body_parts: list[bytes] = []
        body_size = 0

        while True:
            message: dict[str, Any] = await receive()  # type: ignore[assignment]
            chunk: bytes = message.get("body", b"")  # type: ignore[assignment]
            body_parts.append(chunk)
            body_size += len(chunk)

            if body_size > self.max_body_size:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": "PAYLOAD_TOO_LARGE",
                        "message": "Request body exceeds maximum allowed size",
                        "details": {},
                    },
                )
                await response(scope, receive, send)
                return

            if not message.get("more_body", False):
                break

        # Replay buffered body for downstream app
        full_body = b"".join(body_parts)
        body_sent = False

        async def buffered_receive() -> dict[str, Any]:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, buffered_receive, send)  # type: ignore[arg-type]
```

---

## File 4: `src/db_gateway_service/core/__init__.py`

Overwrite the existing empty file with:

```python
"""Core infrastructure components."""

from db_gateway_service.core.exceptions import ServiceError
from db_gateway_service.core.state import AppState, get_app_state, init_app_state

__all__ = ["AppState", "ServiceError", "get_app_state", "init_app_state"]
```

---

## File 5: `src/db_gateway_service/core/lifespan.py`

Create this file:

```python
"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from db_gateway_service.config import get_settings
from db_gateway_service.core.state import init_app_state
from db_gateway_service.logging import get_logger, setup_logging
from db_gateway_service.services.db_writer import DbWriter

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

    state = init_app_state()

    # Ensure database directory exists
    db_path = settings.database.path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Initialize the schema from the SQL file
    schema_path = Path(settings.database.schema_path)
    schema_sql: str | None = None
    if schema_path.exists():
        schema_sql = schema_path.read_text()

    # Initialize database writer
    state.db_writer = DbWriter(
        db_path=db_path,
        busy_timeout_ms=settings.database.busy_timeout_ms,
        journal_mode=settings.database.journal_mode,
        schema_sql=schema_sql,
    )

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
            "database": db_path,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
    if state.db_writer is not None:
        state.db_writer.close()
```

**Key differences from Identity service lifespan:**
- Reads `schema.sql` and passes it to `DbWriter` for initialization
- No crypto configuration needed
- `DbWriter` replaces `AgentRegistry`
- Database is shared `economy.db`, not service-specific

---

## Verification

```bash
cd services/db-gateway && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.
