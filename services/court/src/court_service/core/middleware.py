"""ASGI middleware for request validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

_JSON_POST_ENDPOINTS: set[tuple[str, str]] = {
    ("POST", "/disputes/file"),
}

_JSON_POST_PREFIXES: list[tuple[str, str]] = [
    ("POST", "/disputes/"),
]


class RequestValidationMiddleware:
    """
    Validate Content-Type and body size for JSON POST dispute endpoints.

    Returns:
      - 415 when Content-Type is not application/json
      - 413 when body exceeds configured max size
    """

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = cast("str", scope.get("method", "GET"))
        if method != "POST":
            await self.app(scope, receive, send)
            return

        path = cast("str", scope.get("path", ""))
        is_json_endpoint = (method, path) in _JSON_POST_ENDPOINTS
        if not is_json_endpoint:
            for prefix_method, prefix_path in _JSON_POST_PREFIXES:
                if method == prefix_method and path.startswith(prefix_path):
                    is_json_endpoint = True
                    break

        if not is_json_endpoint:
            await self.app(scope, receive, send)
            return

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

        full_body = b"".join(body_parts)
        body_sent = False

        async def buffered_receive() -> dict[str, Any]:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, buffered_receive, send)
