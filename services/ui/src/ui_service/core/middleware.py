"""ASGI middleware for request validation."""

from __future__ import annotations

import re
from ipaddress import ip_address
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from service_commons.exceptions import middleware_error_response

if TYPE_CHECKING:
    from starlette.responses import JSONResponse
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


_JSON_VALIDATION_ENDPOINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/api/proxy/tasks$")),
    ("POST", re.compile(r"^/api/proxy/tasks/[^/]+/bids/[^/]+/accept$")),
    ("POST", re.compile(r"^/api/proxy/tasks/[^/]+/approve$")),
    ("POST", re.compile(r"^/api/proxy/tasks/[^/]+/dispute$")),
)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _error_response(error: str, message: str, status_code: int) -> JSONResponse:
    return middleware_error_response(
        error=error,
        message=message,
        status_code=status_code,
        details={},
    )


class BrowserSecurityMiddleware:
    """Enforce local browser authority and attach the configured CSP."""

    def __init__(
        self,
        app: ASGIApp,
        allowed_hosts: list[str],
        allowed_origins: list[str],
        csrf_header_name: str,
        csrf_header_value: str,
        content_security_policy: str,
    ) -> None:
        self.app = app
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self.allowed_origins = frozenset(allowed_origins)
        self.csrf_header_name = csrf_header_name.lower().encode("ascii")
        self.csrf_header_value = csrf_header_value.encode("utf-8")
        self.content_security_policy = content_security_policy.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response = self._admission_error(scope)
        if response is not None:
            await response(scope, receive, send)
            return

        async def send_with_csp(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"content-security-policy", self.content_security_policy))
                headers.append((b"x-content-type-options", b"nosniff"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_csp)

    def _admission_error(self, scope: Scope) -> JSONResponse | None:
        raw_headers = cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
        host_values = [value for name, value in raw_headers if name.lower() == b"host"]
        if len(host_values) != 1 or not self._host_allowed(host_values[0]):
            return _error_response("invalid_host", "Host is not allowed", 400)

        path = cast("str", scope.get("path", ""))
        method = cast("str", scope.get("method", "GET"))
        is_proxy = path == "/api/proxy" or path.startswith("/api/proxy/")
        if not is_proxy:
            return None
        if not self._client_is_loopback(scope):
            return _error_response(
                "non_loopback_client",
                "Operator proxy access is restricted to loopback clients",
                403,
            )
        origin_values = [value for name, value in raw_headers if name.lower() == b"origin"]
        if len(origin_values) > 1 or (
            origin_values
            and origin_values[0].decode("ascii", errors="replace") not in self.allowed_origins
        ):
            return _error_response("forbidden_origin", "Origin is not allowed", 403)
        if method in _MUTATING_METHODS:
            return self._write_error(scope, raw_headers, host_values[0], origin_values)
        return None

    def _write_error(
        self,
        scope: Scope,
        raw_headers: list[tuple[bytes, bytes]],
        host: bytes,
        origins: list[bytes],
    ) -> JSONResponse | None:
        if len(origins) != 1:
            return _error_response(
                "forbidden_origin", "A same-origin Origin header is required", 403
            )
        if not self._origin_matches_authority(origins[0], host, scope.get("scheme", "")):
            return _error_response(
                "forbidden_origin", "Origin must match the request authority", 403
            )
        csrf_values = [
            value for name, value in raw_headers if name.lower() == self.csrf_header_name
        ]
        if csrf_values != [self.csrf_header_value]:
            return _error_response(
                "csrf_failed", "Browser request marker is missing or invalid", 403
            )
        return None

    def _host_allowed(self, raw_host: bytes) -> bool:
        try:
            host = raw_host.decode("ascii").lower()
            if re.fullmatch(r"(?:\[[0-9a-f:.]+\]|[a-z0-9.-]+)(?::[0-9]+)?", host) is None:
                return False
            parsed = urlsplit("//" + host)
            port = parsed.port
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.netloc.endswith(":")
                or (port is not None and port <= 0)
            ):
                return False
            hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            # A closing IPv6 bracket must end the hostname or precede a valid port.
            if host.startswith("[") and host != hostname and not host.startswith(hostname + ":"):
                return False
            return hostname in self.allowed_hosts
        except (UnicodeDecodeError, ValueError):
            return False

    @staticmethod
    def _origin_matches_authority(origin: bytes, host: bytes, scheme: str) -> bool:
        try:
            origin_url = urlsplit(origin.decode("ascii"))
            authority = urlsplit("//" + host.decode("ascii"))
            if scheme not in {"http", "https"} or origin_url.scheme != scheme:
                return False
            protocol_port = 80 if scheme == "http" else 443
            return origin_url.hostname == authority.hostname and (
                origin_url.port or protocol_port
            ) == (authority.port or protocol_port)
        except (UnicodeDecodeError, ValueError):
            return False

    @staticmethod
    def _client_is_loopback(scope: Scope) -> bool:
        client = scope.get("client")
        if not client:
            return False
        try:
            return ip_address(client[0]).is_loopback
        except ValueError:
            return False


class RequestValidationMiddleware:
    """
    ASGI middleware that validates Content-Type and body size.

    Runs before FastAPI routes. Returns 415 for wrong content-type
    on JSON-bodied proxy endpoints and 413 for oversized bodies on every
    mutating method, including actions that do not consume a JSON body.
    """

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = cast("str", scope.get("method", "GET"))
        path = cast("str", scope.get("path", ""))

        expects_json = any(
            candidate_method == method and pattern.match(path) is not None
            for candidate_method, pattern in _JSON_VALIDATION_ENDPOINTS
        )
        if not expects_json and method not in _MUTATING_METHODS:
            await self.app(scope, receive, send)
            return

        # Check Content-Type header
        raw_headers = cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
        headers: dict[bytes, bytes] = dict(raw_headers)
        content_type = headers.get(b"content-type", b"").decode().lower()

        # Read and buffer body, checking size
        body_parts: list[bytes] = []
        body_size = 0

        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = cast("bytes", message.get("body", b""))
            body_parts.append(chunk)
            body_size += len(chunk)

            if body_size > self.max_body_size:
                response = middleware_error_response(
                    error="payload_too_large",
                    message="Request body exceeds maximum allowed size",
                    status_code=413,
                    details={},
                )
                await response(scope, receive, send)
                return

            if not message.get("more_body", False):
                break

        if expects_json and not content_type.startswith("application/json"):
            response = middleware_error_response(
                error="unsupported_media_type",
                message="Content-Type must be application/json",
                status_code=415,
                details={},
            )
            await response(scope, receive, send)
            return

        # Replay buffered body for downstream app
        full_body = b"".join(body_parts)
        body_sent = False

        async def buffered_receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)
