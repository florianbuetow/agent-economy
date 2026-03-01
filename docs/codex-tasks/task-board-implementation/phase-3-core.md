# Phase 3 — Core Infrastructure

## Working Directory

All paths relative to `services/task-board/`.

---

## File 1: `src/task_board_service/core/state.py`

Create this file:

```python
"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_board_service.clients.central_bank_client import CentralBankClient
    from task_board_service.clients.identity_client import IdentityClient
    from task_board_service.clients.platform_signer import PlatformSigner
    from task_board_service.services.task_manager import TaskManager


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    task_manager: TaskManager | None = None
    identity_client: IdentityClient | None = None
    central_bank_client: CentralBankClient | None = None
    platform_signer: PlatformSigner | None = None

    @property
    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def started_at(self) -> str:
        """ISO format start time."""
        return self.start_time.isoformat(timespec="seconds").replace("+00:00", "Z")


# Global application state container
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

**Key differences from Identity service:** Four optional fields instead of one. The `task_manager` holds all business logic, `identity_client` and `central_bank_client` are HTTP clients for external services, and `platform_signer` creates JWS tokens for platform-signed escrow operations. All are `None` until initialized in the lifespan.

The `_state_container` dict pattern (instead of a bare global) avoids the `global` statement and the associated `PLW0603` ruff warning, matching the production implementation of the identity service.

---

## File 2: `src/task_board_service/core/exceptions.py`

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

from task_board_service.logging import get_logger

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

This is identical to the identity service exception handlers except for the import path (`task_board_service.logging` instead of `identity_service.logging`). The pattern is intentionally the same: `ServiceError` from service-commons for structured errors, a fallback for unhandled exceptions, and an HTTP exception handler for Starlette's 405 responses.

---

## File 3: `src/task_board_service/core/middleware.py`

Create this file:

```python
"""ASGI middleware for request validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


class RequestValidationMiddleware:
    """
    ASGI middleware that validates Content-Type and body size.

    Runs before FastAPI routes. Returns 415 for wrong content-type
    on POST/PUT/PATCH, and 413 for oversized request bodies.

    Two content types are supported:
    - application/json for all POST/PUT/PATCH endpoints
    - multipart/form-data for POST /tasks/{task_id}/assets (file upload)

    For multipart/form-data requests, body size is NOT checked here —
    file size validation is handled in the router/service layer where
    the file is streamed and individual file sizes are known.
    """

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = cast("str", scope.get("method", "GET"))

        if method not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        # Extract path and headers
        path = cast("str", scope.get("path", ""))
        raw_headers = cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
        headers: dict[bytes, bytes] = dict(raw_headers)
        content_type = headers.get(b"content-type", b"").decode().lower()

        # Asset upload endpoints expect multipart/form-data
        if path.endswith("/assets"):
            if not content_type.startswith("multipart/form-data"):
                response = JSONResponse(
                    status_code=415,
                    content={
                        "error": "UNSUPPORTED_MEDIA_TYPE",
                        "message": "Content-Type must be multipart/form-data",
                        "details": {},
                    },
                )
                await response(scope, receive, send)
                return

            # Skip body size check for multipart — file size is validated
            # in the service layer where individual file sizes are known.
            await self.app(scope, receive, send)
            return

        # All other POST/PUT/PATCH endpoints expect application/json
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
            message = cast("dict[str, Any]", await receive())
            chunk = cast("bytes", message.get("body", b""))
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

        await self.app(scope, buffered_receive, send)
```

**Two key differences from the identity service middleware:**

1. **Dual content-type support.** Paths ending with `/assets` expect `multipart/form-data`. All other POST/PUT/PATCH paths expect `application/json`. The identity service only supports `application/json`.

2. **No body buffering for multipart.** Multipart file uploads can be large (up to `assets.max_file_size`, default 10 MB). Buffering the entire upload in middleware would double memory usage. Instead, the middleware validates only the Content-Type header and passes through. File size is validated in the service layer as the file is streamed.

3. **No path allowlist.** The identity service middleware checks `(method, path)` against a fixed set of known endpoints. The task board has 15 endpoints — maintaining an allowlist is error-prone. Instead, the middleware applies to all POST/PUT/PATCH requests, which is the correct behavior: any unknown path will get proper Content-Type validation and then a 404 from FastAPI's router.

---

## File 4: `src/task_board_service/core/__init__.py`

Overwrite the existing empty file with:

```python
"""Core infrastructure components."""

from task_board_service.core.exceptions import ServiceError
from task_board_service.core.state import AppState, get_app_state, init_app_state

__all__ = ["AppState", "ServiceError", "get_app_state", "init_app_state"]
```

---

## Verification

```bash
cd services/task-board && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.

**Note:** This phase depends on `task_board_service.logging` existing (imported in `exceptions.py`). If Phase 2 has not been completed, the ruff check will still pass (ruff does not resolve imports), but running the service will fail. Complete Phase 2 first.
