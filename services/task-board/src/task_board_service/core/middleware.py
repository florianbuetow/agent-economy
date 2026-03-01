"""ASGI middleware for request validation."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


_JSON_VALIDATION_ENDPOINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/tasks$")),
    ("POST", re.compile(r"^/tasks/[^/]+/cancel$")),
    ("POST", re.compile(r"^/tasks/[^/]+/submit$")),
    ("POST", re.compile(r"^/tasks/[^/]+/approve$")),
    ("POST", re.compile(r"^/tasks/[^/]+/dispute$")),
    ("POST", re.compile(r"^/tasks/[^/]+/ruling$")),
    ("POST", re.compile(r"^/tasks/[^/]+/bids$")),
    ("POST", re.compile(r"^/tasks/[^/]+/bids/[^/]+/accept$")),
)
_MULTIPART_VALIDATION_ENDPOINT = ("POST", re.compile(r"^/tasks/[^/]+/assets$"))


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

        expects_multipart = (
            method == _MULTIPART_VALIDATION_ENDPOINT[0]
            and _MULTIPART_VALIDATION_ENDPOINT[1].match(path) is not None
        )
        expects_json = any(
            candidate_method == method and pattern.match(path) is not None
            for candidate_method, pattern in _JSON_VALIDATION_ENDPOINTS
        )

        # Unknown endpoint/method combos should be handled by router as 404/405.
        if not expects_multipart and not expects_json:
            await self.app(scope, receive, send)
            return

        # Asset upload endpoint expects multipart/form-data
        if expects_multipart:
            # If Content-Type is omitted, let the router return NO_FILE/INVALID payload.
            if content_type != "" and not content_type.startswith("multipart/form-data"):
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

        # Valid JSON endpoints expect application/json
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
