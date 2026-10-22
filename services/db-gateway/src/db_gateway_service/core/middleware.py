"""ASGI middleware for request validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from service_auth.gateway import verify_gateway_request
from service_auth.jws import ForbiddenError, JwsAdmissionError, TokenPolicy, parse_jws
from service_commons.exceptions import middleware_error_response

if TYPE_CHECKING:
    from re import Pattern

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from starlette.types import ASGIApp, Receive, Scope, Send


_ALL_GATEWAY_ACTIONS = (
    "gateway.identity.read",
    "gateway.identity.write",
    "gateway.bank.read",
    "gateway.bank.write",
    "gateway.board.read",
    "gateway.board.write",
    "gateway.board.task.read",
    "gateway.reputation.read",
    "gateway.reputation.write",
    "gateway.court.read",
    "gateway.court.write",
)

GATEWAY_ROUTE_POLICIES: dict[tuple[str, str], tuple[str, ...]] = {
    ("POST", "/board/task-publications"): ("gateway.board.write",),
    ("POST", "/board/task-publications/{operation_id}/reconcile"): ("gateway.board.write",),
    ("GET", "/board/task-publications/{operation_id}"): ("gateway.board.write",),
    ("GET", "/board/posting-recovery"): ("gateway.board.write",),
    ("POST", "/board/legacy-posting-observations/{observation_id}/reconcile"): (
        "gateway.board.write",
    ),
    ("POST", "/board/task-operations"): ("gateway.board.write",),
    ("POST", "/board/task-operations/{operation_id}/finalize"): ("gateway.board.write",),
    ("POST", "/identity/agents"): ("gateway.identity.write",),
    ("GET", "/identity/agents/count"): ("gateway.identity.read",),
    ("GET", "/identity/agents"): ("gateway.identity.read",),
    ("GET", "/identity/agents/{agent_id}"): ("gateway.identity.read",),
    ("POST", "/bank/accounts"): ("gateway.bank.write",),
    ("POST", "/bank/credit"): ("gateway.bank.write",),
    ("POST", "/bank/escrow/lock"): ("gateway.bank.write",),
    ("POST", "/bank/escrow/release"): ("gateway.bank.write",),
    ("POST", "/bank/escrow/split"): ("gateway.bank.write",),
    ("GET", "/bank/accounts/count"): ("gateway.bank.read",),
    ("GET", "/bank/accounts/{account_id}"): ("gateway.bank.read",),
    ("GET", "/bank/accounts/{account_id}/transactions"): ("gateway.bank.read",),
    ("GET", "/bank/escrow/total-locked"): ("gateway.bank.read",),
    ("GET", "/bank/escrow/{escrow_id}"): ("gateway.bank.read",),
    ("POST", "/board/tasks"): ("gateway.board.write",),
    ("POST", "/board/bids"): ("gateway.board.write",),
    ("POST", "/board/tasks/{task_id}/status"): ("gateway.board.write",),
    ("POST", "/board/assets"): ("gateway.board.write",),
    ("GET", "/board/tasks/count"): ("gateway.board.read",),
    ("GET", "/board/tasks/count-by-status"): ("gateway.board.read",),
    ("GET", "/board/tasks"): ("gateway.board.read",),
    ("GET", "/board/tasks/{task_id}"): (
        "gateway.board.read",
        "gateway.board.task.read",
    ),
    ("GET", "/board/tasks/{task_id}/bids"): ("gateway.board.read",),
    ("GET", "/board/tasks/{task_id}/assets/count"): ("gateway.board.read",),
    ("GET", "/board/tasks/{task_id}/assets"): ("gateway.board.read",),
    ("GET", "/board/bids/{bid_id}"): ("gateway.board.read",),
    ("GET", "/board/assets/{asset_id}"): ("gateway.board.read",),
    ("POST", "/reputation/feedback"): ("gateway.reputation.write",),
    ("GET", "/reputation/feedback/count"): ("gateway.reputation.read",),
    ("GET", "/reputation/feedback/{feedback_id}"): ("gateway.reputation.read",),
    ("GET", "/reputation/feedback"): ("gateway.reputation.read",),
    ("POST", "/court/claims"): ("gateway.court.write",),
    ("POST", "/court/rebuttals"): ("gateway.court.write",),
    ("POST", "/court/rulings"): ("gateway.court.write",),
    ("GET", "/court/claims/count"): ("gateway.court.read",),
    ("GET", "/court/claims/count-active"): ("gateway.court.read",),
    ("GET", "/court/claims"): ("gateway.court.read",),
    ("GET", "/court/claims/{claim_id}"): ("gateway.court.read",),
    ("POST", "/court/claims/{claim_id}/status"): ("gateway.court.write",),
    ("GET", "/court/claims/{claim_id}/rebuttal"): ("gateway.court.read",),
    ("GET", "/court/rulings/{claim_id}"): ("gateway.court.read",),
    ("DELETE", "/court/rulings/{claim_id}"): ("gateway.court.write",),
}


@dataclass(frozen=True)
class TrustedGatewayCaller:
    """Loaded public key and immutable capability grants for one caller."""

    public_key: Ed25519PublicKey
    capabilities: frozenset[str]


@dataclass(frozen=True)
class GatewayRouteMatcher:
    """One ordered FastAPI route template matcher."""

    route_template: str
    path_regex: Pattern[str]


@dataclass(frozen=True)
class _MiddlewareFailure(Exception):
    error: str
    message: str
    status_code: int


class RequestValidationMiddleware:
    """
    ASGI middleware that validates Content-Type and body size.

    Runs before FastAPI routes. Returns 415 for wrong content-type
    on POST/PUT/PATCH, and 413 for oversized request bodies.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_body_size: int,
        audience: str,
        token_policy: TokenPolicy,
        trusted_callers: dict[str, TrustedGatewayCaller],
        route_matchers: tuple[GatewayRouteMatcher, ...],
        route_policies: dict[tuple[str, str], tuple[str, ...]],
    ) -> None:
        self.app = app
        self.max_body_size = max_body_size
        self.audience = audience
        self.token_policy = token_policy
        self.trusted_callers = trusted_callers
        self.route_matchers = route_matchers
        self.route_policies = route_policies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await self._handle_http(scope, receive, send)

    async def _handle_http(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            method, path, route_path, query = self._request_context(scope)
            if method == "GET" and path == "/health" and query == b"":
                await self.app(scope, receive, send)
                return

            raw_headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
            self._validate_content_type(method, raw_headers)
            full_body = await self._read_body(receive)
            token = self._bearer_token(raw_headers)
            verified_caller_kid = self._authenticate(
                token,
                method=method,
                path=path,
                route_path=route_path,
                body=full_body,
                query=query,
            )
            scope.setdefault("state", {})["gateway_caller_kid"] = verified_caller_kid
        except _MiddlewareFailure as exc:
            await self._error(scope, receive, send, exc.error, exc.message, exc.status_code)
            return

        body_sent = False

        async def buffered_receive() -> dict[str, Any]:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, buffered_receive, send)

    @staticmethod
    def _request_context(scope: Scope) -> tuple[str, str, str, bytes]:
        method = str(scope.get("method", "GET")).upper()
        raw_path_value = scope.get("raw_path", str(scope.get("path", "")).encode("ascii"))
        try:
            path = bytes(raw_path_value).decode("ascii")
        except UnicodeDecodeError as exc:
            raise _MiddlewareFailure("invalid_payload", "Invalid request path", 400) from exc
        route_path = str(scope.get("path", ""))
        return method, path, route_path, bytes(scope.get("query_string", b""))

    @staticmethod
    def _validate_content_type(
        method: str,
        raw_headers: list[tuple[bytes, bytes]],
    ) -> None:
        headers: dict[bytes, bytes] = dict(raw_headers)
        content_type = headers.get(b"content-type", b"").decode(errors="replace").lower()
        if method in ("POST", "PUT", "PATCH", "DELETE") and not content_type.startswith(
            "application/json"
        ):
            raise _MiddlewareFailure(
                "unsupported_media_type",
                "Content-Type must be application/json",
                415,
            )

    async def _read_body(self, receive: Receive) -> bytes:
        body_parts: list[bytes] = []
        body_size = 0
        while True:
            message: dict[str, Any] = dict(await receive())
            chunk = bytes(message.get("body", b""))
            body_parts.append(chunk)
            body_size += len(chunk)
            if body_size > self.max_body_size:
                raise _MiddlewareFailure(
                    "payload_too_large",
                    "Request body exceeds maximum allowed size",
                    413,
                )
            if not message.get("more_body", False):
                return b"".join(body_parts)

    @staticmethod
    def _bearer_token(raw_headers: list[tuple[bytes, bytes]]) -> str:
        authorization_values = [
            value for name, value in raw_headers if name.lower() == b"authorization"
        ]
        if len(authorization_values) != 1:
            raise _MiddlewareFailure(
                "invalid_authorization",
                "Exactly one Authorization header is required",
                401,
            )
        try:
            authorization = authorization_values[0].decode("ascii")
        except UnicodeDecodeError as exc:
            raise _MiddlewareFailure(
                "invalid_authorization",
                "Authorization must use one Bearer token",
                401,
            ) from exc
        if not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
            raise _MiddlewareFailure(
                "invalid_authorization",
                "Authorization must use one Bearer token",
                401,
            )
        token = authorization.removeprefix("Bearer ")
        if not token:
            raise _MiddlewareFailure(
                "invalid_authorization",
                "Authorization Bearer token must not be empty",
                401,
            )
        return token

    def _authenticate(
        self,
        token: str,
        *,
        method: str,
        path: str,
        route_path: str,
        body: bytes,
        query: bytes,
    ) -> str:
        try:
            parsed = parse_jws(token)
            caller = self.trusted_callers.get(parsed.header.kid)
            if caller is None:
                raise ForbiddenError("Gateway caller key is not trusted")
            route_template = self._match_route_template(route_path)
            allowed_actions = self._allowed_actions(method, route_template)
            verified = verify_gateway_request(
                token,
                caller.public_key,
                policy=self.token_policy,
                audience=self.audience,
                method=method,
                path=path,
                body=body,
                query=query,
                allowed_actions=allowed_actions,
            )
            action = str(verified.payload["action"])
            if action not in caller.capabilities:
                raise ForbiddenError("Gateway caller lacks the required capability")
            if route_template is None:
                raise ForbiddenError("Gateway route has no explicit capability policy")
            return verified.header.kid
        except JwsAdmissionError as exc:
            raise _MiddlewareFailure(exc.error_code, str(exc), exc.status_code) from exc

    def _match_route_template(self, route_path: str) -> str | None:
        """Select the first FastAPI route template matching the decoded path."""
        for matcher in self.route_matchers:
            if matcher.path_regex.fullmatch(route_path) is not None:
                return matcher.route_template
        return None

    def _allowed_actions(self, method: str, route_template: str | None) -> tuple[str, ...]:
        """Return exact route policy, preserving authenticated 404/405 behavior."""
        if route_template is None:
            return _ALL_GATEWAY_ACTIONS
        if route_template == "/health":
            return _ALL_GATEWAY_ACTIONS
        exact = self.route_policies.get((method, route_template))
        if exact is not None:
            return exact
        domain_prefixes = {
            ".".join(action.split(".")[:2])
            for (_policy_method, policy_template), policy_actions in self.route_policies.items()
            if policy_template == route_template
            for action in policy_actions
        }
        return tuple(
            action
            for action in _ALL_GATEWAY_ACTIONS
            if ".".join(action.split(".")[:2]) in domain_prefixes
        )

    @staticmethod
    async def _error(
        scope: Scope,
        receive: Receive,
        send: Send,
        error: str,
        message: str,
        status_code: int,
    ) -> None:
        response = middleware_error_response(
            error=error,
            message=message,
            status_code=status_code,
            details={},
        )
        await response(scope, receive, send)
