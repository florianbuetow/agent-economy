"""ASGI middleware for request validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

# POST endpoints that require JSON body validation
_JSON_POST_ENDPOINTS: set[tuple[str, str]] = {
    ("POST", "/feedback"),
}


class RequestValidationMiddleware:
    """
    ASGI middleware that validates Content-Type and body size.

    Runs before FastAPI routes. Returns 415 for wrong content-type
    on POST, and 413 for oversized request bodies.
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
        if (method, path) not in _JSON_POST_ENDPOINTS:
            await self.app(scope, receive, send)
            return

        # Check Content-Type header (use first occurrence; reject duplicates)
        raw_headers = cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
        ct_values = [v for k, v in raw_headers if k == b"content-type"]
        if len(ct_values) > 1:
            response = JSONResponse(
                status_code=400,
                content={
                    "error": "BAD_REQUEST",
                    "message": "Duplicate Content-Type header",
                    "details": {},
                },
            )
            await response(scope, receive, send)
            return
        content_type = ct_values[0].decode().lower() if ct_values else ""

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
