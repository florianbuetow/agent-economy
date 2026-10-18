"""The operation writer must receive the real authenticated service identity."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx
import pytest
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.jws import TokenPolicy
from service_auth.signing import generate_keypair
from starlette.responses import JSONResponse

from db_gateway_service.core.middleware import (
    GatewayRouteMatcher,
    RequestValidationMiddleware,
    TrustedGatewayCaller,
)

if TYPE_CHECKING:
    from pathlib import Path

    from starlette.types import ASGIApp, Receive, Scope, Send

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _surface(
    tmp_path: Path, *, prior_caller: str | None
) -> tuple[ASGIApp, dict[str, GatewayRequestSigner], list[object]]:
    signers: dict[str, GatewayRequestSigner] = {}
    callers: dict[str, TrustedGatewayCaller] = {}
    for kid in ("board-primary", "board-secondary", "untrusted-key"):
        _private, public = generate_keypair(kid, tmp_path)
        signers[kid] = GatewayRequestSigner(
            GatewaySignerConfig(
                kid=kid,
                private_key_path=tmp_path / f"{kid}.key",
                audience="db-gateway",
                token_ttl_seconds=30,
            ),
            config_dir=tmp_path,
        )
        if kid != "untrusted-key":
            callers[kid] = TrustedGatewayCaller(
                public_key=public, capabilities=frozenset({"gateway.board.write"})
            )
    observed: list[object] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        caller = scope.get("state", {}).get("gateway_caller_kid")
        observed.append(caller)
        await JSONResponse({"verified_caller": caller})(scope, receive, send)

    middleware = RequestValidationMiddleware(
        app=downstream,
        max_body_size=4096,
        audience="db-gateway",
        token_policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
        trusted_callers=callers,
        route_matchers=(GatewayRouteMatcher("/board/tasks", re.compile("/board/tasks")),),
        route_policies={("POST", "/board/tasks"): ("gateway.board.write",)},
    )

    async def surface(scope: Scope, receive: Receive, send: Send) -> None:
        if prior_caller is not None:
            scope["state"] = {"gateway_caller_kid": prior_caller}
        await middleware(scope, receive, send)

    return surface, signers, observed


def _signed_request(signer: GatewayRequestSigner) -> httpx.Request:
    return signer.sign_request(
        httpx.Request(
            "POST",
            "http://testserver/board/tasks",
            headers={"Content-Type": "application/json", "X-Caller-Kid": "body-spoof"},
            json={"caller_kid": "body-spoof"},
        ),
        action="gateway.board.write",
    )


@pytest.mark.parametrize("kid", ["board-primary", "board-secondary"])
@pytest.mark.parametrize("prior_caller", [None, "scope-spoof"])
async def test_authenticated_kid_replaces_untrusted_caller_hints(
    tmp_path: Path, kid: str, prior_caller: str | None
) -> None:
    app, signers, observed = _surface(tmp_path, prior_caller=prior_caller)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        response = await client.send(_signed_request(signers[kid]))
    assert response.status_code == 200, response.text
    assert response.json() == {"verified_caller": kid}
    assert observed == [kid]


@pytest.mark.parametrize("invalid", ["untrusted-key", "tampered-body"])
async def test_unverified_request_never_reaches_the_operation_writer(
    tmp_path: Path, invalid: str
) -> None:
    app, signers, observed = _surface(tmp_path, prior_caller="scope-spoof")
    signer = signers["untrusted-key" if invalid == "untrusted-key" else "board-primary"]
    request = _signed_request(signer)
    if invalid == "tampered-body":
        request = httpx.Request(
            request.method,
            request.url,
            headers={
                "Authorization": request.headers["Authorization"],
                "Content-Type": "application/json",
            },
            json={"caller_kid": "different-body"},
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        response = await client.send(request)
    expected_status, expected_error = (
        (403, "forbidden") if invalid == "untrusted-key" else (400, "invalid_payload")
    )
    assert response.status_code == expected_status, response.text
    assert response.json()["error"] == expected_error
    assert observed == []
