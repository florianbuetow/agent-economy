"""Strict Identity verification client contract tests."""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from service_auth.jws import VerifiedJws
from service_commons.exceptions import ServiceError

from service_clients.identity import IdentityClient

Handler = Callable[[httpx.Request], httpx.Response]


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _token(payload: dict[str, object] | None = None) -> tuple[str, dict[str, object]]:
    header: dict[str, object] = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": "a-1",
        "iat": 100,
        "exp": 200,
        "aud": "central-bank",
        "resource": "POST /escrow/lock",
    }
    signed_payload = (
        {"action": "escrow_lock", "nested": {"items": [{"value": True}]}}
        if payload is None
        else payload
    )
    header_segment = _b64url(json.dumps(header, sort_keys=True).encode())
    payload_segment = _b64url(json.dumps(signed_payload, sort_keys=True).encode())
    return (
        f"{header_segment}.{payload_segment}.{_b64url(bytes(64))}",
        {"valid": True, "agent_id": "a-1", "protected_header": header, "payload": signed_payload},
    )


def _client(handler: Handler) -> IdentityClient:
    client = IdentityClient(
        base_url="http://identity.invalid",
        get_agent_path="/agents",
        verify_jws_path="/agents/verify-jws",
        timeout_seconds=1,
    )
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://identity.invalid",
    )
    return client


@pytest.mark.unit
async def test_verify_jws_returns_complete_typed_result() -> None:
    token, response_body = _token()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agents/verify-jws"
        assert json.loads(request.content) == {"token": token}
        return httpx.Response(200, json=response_body)

    verified = await _client(handler).verify_jws(token)
    assert isinstance(verified, VerifiedJws)
    assert verified.header.kid == "a-1"
    assert verified.payload == response_body["payload"]


@pytest.mark.unit
async def test_verify_jws_invalid_result_raises_raw_invalid_signature() -> None:
    token, _response_body = _token()
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={"valid": False, "reason": "signature mismatch"},
        )
    )
    with pytest.raises(InvalidSignature, match="signature mismatch"):
        await client.verify_jws(token)


def _missing_header(body: dict[str, Any]) -> None:
    del body["protected_header"]


def _kid_mismatch(body: dict[str, Any]) -> None:
    body["agent_id"] = "a-2"


def _bool_to_int(body: dict[str, Any]) -> None:
    body["payload"]["nested"]["items"][0]["value"] = 1


def _bool_to_float(body: dict[str, Any]) -> None:
    body["payload"]["nested"]["items"][0]["value"] = 1.0


def _wrong_valid_type(body: dict[str, Any]) -> None:
    body["valid"] = 1


def _wrong_payload_type(body: dict[str, Any]) -> None:
    body["payload"] = ["not", "an", "object"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutate",
    [
        _missing_header,
        _kid_mismatch,
        _bool_to_int,
        _bool_to_float,
        _wrong_valid_type,
        _wrong_payload_type,
    ],
)
async def test_malformed_claimed_success_maps_to_identity_unavailable(mutate) -> None:
    token, response_body = _token()
    malformed = copy.deepcopy(response_body)
    mutate(malformed)
    client = _client(lambda _request: httpx.Response(200, json=malformed))
    with pytest.raises(ServiceError) as excinfo:
        await client.verify_jws(token)
    assert excinfo.value.error == "identity_service_unavailable"
    assert excinfo.value.status_code == 502


@pytest.mark.unit
async def test_claimed_success_for_invalid_requested_token_maps_to_identity_unavailable() -> None:
    _token_value, response_body = _token()
    client = _client(lambda _request: httpx.Response(200, json=response_body))
    with pytest.raises(ServiceError) as excinfo:
        await client.verify_jws("invalid.requested.token")
    assert excinfo.value.error == "identity_service_unavailable"
    assert excinfo.value.status_code == 502


@pytest.mark.unit
@pytest.mark.parametrize(
    "status,error",
    [(400, "invalid_jws"), (401, "token_expired"), (404, "agent_not_found")],
)
async def test_identity_http_errors_are_preserved(status: int, error: str) -> None:
    token, _response_body = _token()
    client = _client(
        lambda _request: httpx.Response(
            status,
            json={"error": error, "message": "rejected", "details": {}},
        )
    )
    with pytest.raises(ServiceError) as excinfo:
        await client.verify_jws(token)
    assert excinfo.value.error == error
    assert excinfo.value.status_code == status


@pytest.mark.unit
async def test_get_agent_contract_is_unchanged() -> None:
    client = _client(
        lambda request: httpx.Response(
            200,
            json={"agent_id": request.url.path.rsplit("/", 1)[-1]},
        )
    )
    assert await client.get_agent("a-7") == {"agent_id": "a-7"}


@pytest.mark.unit
async def test_unparseable_deep_identity_success_is_unavailable() -> None:
    token, _response_body = _token()
    malformed = '{"valid":true,"payload":' + "[" * 10000 + "0" + "]" * 10000 + "}"
    client = _client(
        lambda _request: httpx.Response(
            200, content=malformed, headers={"Content-Type": "application/json"}
        )
    )
    try:
        with pytest.raises(ServiceError) as excinfo:
            await client.verify_jws(token)
        assert excinfo.value.error == "identity_service_unavailable"
        assert excinfo.value.status_code == 502
    finally:
        await client.close()


@pytest.mark.unit
async def test_internal_transport_recursion_is_not_an_upstream_contract_error() -> None:
    token, _response_body = _token()

    def broken_transport(_request: httpx.Request) -> httpx.Response:
        raise RecursionError("injected transport programming failure")

    client = _client(broken_transport)
    try:
        with pytest.raises(RecursionError, match="injected transport programming failure"):
            await client.verify_jws(token)
    finally:
        await client.close()
