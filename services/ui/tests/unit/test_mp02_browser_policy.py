"""Fail-fast local browser policy and exact authority parsing."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from ui_service.config import BrowserSecurityConfig
from ui_service.core.middleware import BrowserSecurityMiddleware, RequestValidationMiddleware

pytestmark = pytest.mark.unit


def policy(**updates):
    values = {
        "allowed_hosts": ["localhost", "127.0.0.1", "[::1]"],
        "allowed_origins": ["http://localhost:8008", "http://127.0.0.1:8008", "http://[::1]:8008"],
        "csrf_header_name": "X-ATE-CSRF",
        "csrf_header_value": "local-operator",
        "content_security_policy": "default-src 'self'; script-src 'self'; object-src 'none'",
    }
    return BrowserSecurityConfig(**{**values, **updates})


@pytest.mark.parametrize(
    "host", ["*", "example.invalid", "localhost:8008", " localhost", "user@localhost"]
)
def test_allowed_hosts_cannot_enable_external_browser_origins(host):
    with pytest.raises(ValidationError):
        policy(allowed_hosts=[host])


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.invalid",
        " http://localhost:8008",
        "http://localhost:99999",
        "http://localhost:8008/",
    ],
)
def test_origin_configuration_is_exact_and_local(origin):
    with pytest.raises(ValidationError):
        policy(allowed_origins=[origin])


@pytest.mark.parametrize(
    "updates",
    [
        {"csrf_header_name": "Content-Type"},
        {"csrf_header_name": "Origin"},
        {"csrf_header_name": "X-Test\n"},
        {"csrf_header_value": "value\r\n"},
        {"content_security_policy": "default-src 'self'\r\n"},
        {"content_security_policy": "non-ascii-\u00e9"},
    ],
)
def test_browser_header_configuration_rejects_invalid_values(updates):
    with pytest.raises(ValidationError):
        policy(**updates)


async def _request(host, client_address):
    calls = []

    async def application(scope, _receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    config = policy()
    app = BrowserSecurityMiddleware(application, **config.model_dump())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(client_address, 123)),
        base_url="http://localhost:8008",
    ) as client:
        response = await client.post(
            "/api/proxy/tasks/example/approve",
            headers={
                "Host": host,
                "Origin": "http://localhost:8008",
                "X-ATE-CSRF": "local-operator",
            },
        )
    return response, calls


@pytest.mark.parametrize(
    "host", ["localhost:invalid", "localhost:99999", "[::1]suffix", "localhost:"]
)
async def test_malformed_allowed_host_is_rejected_before_handler(host):
    response, calls = await _request(host, "127.0.0.1")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_host"
    assert calls == []


@pytest.mark.parametrize("address", ["203.0.113.5", "unknown", "192.0.2.1"])
async def test_nonlocal_clients_cannot_use_forged_local_headers(address):
    response, calls = await _request("localhost:8008", address)
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("address", ["127.0.0.1", "::1"])
async def test_valid_local_browser_request_reaches_handler(address):
    response, calls = await _request("localhost:8008", address)
    assert response.status_code == 200
    assert calls == ["/api/proxy/tasks/example/approve"]
    assert response.headers["content-security-policy"] == policy().content_security_policy


@pytest.mark.parametrize("host", ["@localhost:8008", "localhost:8008?", "localhost:8008#"])
async def test_host_cannot_hide_url_components(host):
    response, calls = await _request(host, "127.0.0.1")
    assert response.status_code == 400
    assert calls == []


@pytest.mark.parametrize(
    "origin", ["http://@localhost:8008", "http://localhost:8008?", "http://localhost:8008#"]
)
def test_origin_rejects_empty_url_components(origin):
    with pytest.raises(ValidationError):
        policy(allowed_origins=[origin])


async def test_origin_must_match_current_authority_even_if_both_hosts_are_allowed():
    response, calls = await _request("127.0.0.1:8008", "127.0.0.1")
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "path", ["/api/proxy/tasks/example/approve", "/api/proxy/tasks/example/bids/bid/accept"]
)
async def test_bodyless_operator_routes_still_bound_request_bytes(path):
    calls = []

    async def application(scope, _receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    body_guard = RequestValidationMiddleware(application, max_body_size=64)
    app = BrowserSecurityMiddleware(body_guard, **policy().model_dump())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8008"
    ) as client:
        response = await client.post(
            path,
            content=b"x" * 65,
            headers={"Origin": "http://localhost:8008", "X-ATE-CSRF": "local-operator"},
        )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"
    assert calls == []


async def test_body_guard_preserves_exact_allowed_bytes():
    received = []

    async def application(_scope, receive, send):
        message = await receive()
        received.append(message["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = RequestValidationMiddleware(application, max_body_size=64)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        response = await client.post("/api/proxy/tasks/example/approve", content=b"x" * 64)
    assert response.status_code == 200
    assert received == [b"x" * 64]
