# Phase 4 — Core Infrastructure

## Working Directory

All paths relative to `services/central-bank/`.

---

## Task B4: Implement core/ modules (state, exceptions, middleware)

### Step 4.1: Write core/state.py

Create `services/central-bank/src/central_bank_service/core/state.py`:

```python
"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from central_bank_service.services.identity_client import IdentityClient
    from central_bank_service.services.ledger import Ledger


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    ledger: Ledger | None = None
    identity_client: IdentityClient | None = None

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

### Step 4.2: Write core/exceptions.py

Create `services/central-bank/src/central_bank_service/core/exceptions.py`:

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

from central_bank_service.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

    from fastapi import FastAPI, Request
    from starlette.requests import Request as StarletteRequest

    ExceptionHandler = Callable[
        [StarletteRequest, Exception],
        Coroutine[Any, Any, JSONResponse],
    ]

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
    _request: Request, exc: StarletteHTTPException
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

### Step 4.3: Write core/middleware.py

Create `services/central-bank/src/central_bank_service/core/middleware.py`:

```python
"""ASGI middleware for request validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


# POST endpoints that require JSON body validation
_JSON_POST_ENDPOINTS: set[tuple[str, str]] = {
    ("POST", "/accounts"),
    ("POST", "/escrow/lock"),
}

# POST endpoints with path parameters (matched by prefix)
_JSON_POST_PREFIXES: list[tuple[str, str]] = [
    ("POST", "/accounts/"),
    ("POST", "/escrow/"),
]


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

        method = cast("str", scope.get("method", "GET"))

        if method not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        path = cast("str", scope.get("path", ""))

        # Check if this endpoint requires JSON validation
        is_json_endpoint = (method, path) in _JSON_POST_ENDPOINTS
        if not is_json_endpoint:
            for prefix_method, prefix_path in _JSON_POST_PREFIXES:
                if method == prefix_method and path.startswith(prefix_path):
                    is_json_endpoint = True
                    break

        if not is_json_endpoint:
            await self.app(scope, receive, send)
            return

        # Check Content-Type header
        raw_headers = cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
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

### Step 4.4: Write core/__init__.py

Replace the empty `services/central-bank/src/central_bank_service/core/__init__.py` with:

```python
"""Core infrastructure components."""

from central_bank_service.core.exceptions import ServiceError
from central_bank_service.core.state import AppState, get_app_state, init_app_state

__all__ = ["AppState", "ServiceError", "get_app_state", "init_app_state"]
```

### Step 4.5: Commit

```bash
git add services/central-bank/src/central_bank_service/core/
git commit -m "feat(central-bank): add core modules (state, exceptions, middleware)"
```

---

## Verification

```bash
cd services/central-bank && uv run ruff check src/ && uv run ruff format --check src/
```
