"""Strict compact-JWS acceptance tests for Identity."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from service_auth import jws as core_jws

from identity_service.core.state import get_app_state

NOW = int(datetime(2030, 1, 1, tzinfo=UTC).timestamp())
_MISSING = object()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, f"ed25519:{base64.b64encode(public).decode()}"


def _token(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    *,
    header_updates: dict[str, object] | None = None,
    payload: object = None,
) -> str:
    header: dict[str, object] = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": agent_id,
        "iat": NOW,
        "exp": NOW + 300,
        "aud": "central-bank",
        "resource": "POST /escrow/lock",
    }
    if header_updates is not None:
        for key, value in header_updates.items():
            if value is None:
                header.pop(key, None)
            else:
                header[key] = value
    signed_payload = {"action": "escrow_lock", "amount": 10} if payload is None else payload
    header_segment = _b64url(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    payload_segment = _b64url(
        json.dumps(signed_payload, sort_keys=True, separators=(",", ":")).encode()
    )
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    return f"{header_segment}.{payload_segment}.{_b64url(private_key.sign(signing_input))}"


def _profile_header(agent_id: str) -> dict[str, object]:
    return {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": agent_id,
        "iat": NOW,
        "exp": NOW + 300,
        "aud": "central-bank",
        "resource": "POST /escrow/lock",
    }


def _raw_token(
    private_key: Ed25519PrivateKey,
    header: object,
    payload: object | bytes,
    *,
    signature: bytes | None = None,
) -> str:
    header_segment = _b64url(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    payload_bytes = (
        payload
        if isinstance(payload, bytes)
        else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    payload_segment = _b64url(payload_bytes)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signed = private_key.sign(signing_input) if signature is None else signature
    return f"{header_segment}.{payload_segment}.{_b64url(signed)}"


async def _register(client, public_key: str) -> str:
    response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )
    assert response.status_code == 201
    return response.json()["agent_id"]


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        core_jws,
        "_clock",
        lambda: datetime.fromtimestamp(NOW, tz=UTC),
    )


@pytest.mark.unit
async def test_valid_jws_returns_complete_authenticated_result(client) -> None:
    private_key, public_key = _generate_keypair()
    agent_id = await _register(client, public_key)
    token = _token(private_key, agent_id)
    response = await client.post("/agents/verify-jws", json={"token": token})
    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "agent_id": agent_id,
        "protected_header": {
            "alg": "EdDSA",
            "typ": "JWT",
            "kid": agent_id,
            "iat": NOW,
            "exp": NOW + 300,
            "aud": "central-bank",
            "resource": "POST /escrow/lock",
        },
        "payload": {"action": "escrow_lock", "amount": 10},
    }


@pytest.mark.unit
async def test_action_only_payload_is_valid(client) -> None:
    private_key, public_key = _generate_keypair()
    agent_id = await _register(client, public_key)
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _token(private_key, agent_id, payload={"action": "balance"})},
    )
    assert response.status_code == 200
    assert response.json()["payload"] == {"action": "balance"}


@pytest.mark.unit
async def test_empty_payload_is_rejected_as_invalid_payload(client) -> None:
    private_key, public_key = _generate_keypair()
    agent_id = await _register(client, public_key)
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _token(private_key, agent_id, payload={})},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


@pytest.mark.unit
async def test_wrong_signer_retains_explicit_invalid_result(client) -> None:
    _registered_key, public_key = _generate_keypair()
    wrong_key, _wrong_public = _generate_keypair()
    agent_id = await _register(client, public_key)
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _token(wrong_key, agent_id)},
    )
    assert response.status_code == 200
    assert response.json() == {"valid": False, "reason": "signature mismatch"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-action"),
        pytest.param({"action": True}, id="wrong-type-action"),
        pytest.param({"action": ""}, id="blank-action"),
    ],
)
async def test_wrong_signature_precedes_action_validation(
    client, payload: dict[str, object]
) -> None:
    _registered_key, public_key = _generate_keypair()
    wrong_key, _wrong_public = _generate_keypair()
    agent_id = await _register(client, public_key)
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _token(wrong_key, agent_id, payload=payload)},
    )
    assert response.status_code == 200
    assert response.json() == {"valid": False, "reason": "signature mismatch"}


@pytest.mark.unit
async def test_missing_kid_is_invalid_jws(client) -> None:
    private_key, public_key = _generate_keypair()
    agent_id = await _register(client, public_key)
    header = _profile_header(agent_id)
    del header["kid"]
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _raw_token(private_key, header, {"action": "test"})},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
async def test_wrong_algorithm_is_invalid_jws(client) -> None:
    private_key, _public_key = _generate_keypair()
    header = _profile_header("a-unknown")
    header["alg"] = "HS256"
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _raw_token(private_key, header, {"action": "test"})},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
async def test_authentically_signed_non_json_payload_is_invalid_jws(client) -> None:
    private_key, public_key = _generate_keypair()
    agent_id = await _register(client, public_key)
    response = await client.post(
        "/agents/verify-jws",
        json={
            "token": _raw_token(
                private_key,
                _profile_header(agent_id),
                b"not json at all",
            )
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, [], "value", 7])
async def test_non_object_protected_header_is_invalid_jws(client, value: object) -> None:
    private_key, _public_key = _generate_keypair()
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _raw_token(private_key, value, {"action": "test"})},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, [], "value", 7])
async def test_non_object_signed_payload_is_invalid_jws(client, value: object) -> None:
    private_key, _public_key = _generate_keypair()
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _raw_token(private_key, _profile_header("a-unknown"), value)},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value",
    [
        ("alg", _MISSING),
        ("alg", None),
        ("alg", True),
        ("alg", 1),
        ("alg", "HS256"),
        ("typ", _MISSING),
        ("typ", None),
        ("typ", True),
        ("typ", 1),
        ("typ", "NOT-JWT"),
        ("kid", _MISSING),
        ("kid", None),
        ("kid", True),
        ("kid", 1),
        ("kid", ""),
        ("iat", _MISSING),
        ("iat", None),
        ("iat", True),
        ("iat", "now"),
        ("iat", float(NOW)),
        ("exp", _MISSING),
        ("exp", None),
        ("exp", True),
        ("exp", "later"),
        ("exp", float(NOW + 300)),
        ("aud", _MISSING),
        ("aud", None),
        ("aud", True),
        ("aud", 1),
        ("aud", ""),
        ("resource", _MISSING),
        ("resource", None),
        ("resource", True),
        ("resource", 1),
        ("resource", ""),
        ("resource", "post /escrow/lock"),
        ("resource", "POST https://example.test/escrow/lock"),
        ("resource", "POST /escrow/lock?query=yes"),
    ],
)
async def test_every_protected_header_field_is_exactly_typed(
    client,
    field: str,
    value: object,
) -> None:
    private_key, _public_key = _generate_keypair()
    header = _profile_header("a-unknown")
    if value is _MISSING:
        del header[field]
    else:
        header[field] = value
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _raw_token(private_key, header, {"action": "test"})},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
async def test_extra_protected_header_field_is_invalid_jws(client) -> None:
    private_key, _public_key = _generate_keypair()
    header = _profile_header("a-unknown")
    header["extra"] = "field"
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _raw_token(private_key, header, {"action": "test"})},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
async def test_wrong_signature_length_is_structural_invalid_jws(client) -> None:
    private_key, _public_key = _generate_keypair()
    token = _raw_token(
        private_key,
        _profile_header("a-unknown"),
        {"action": "test"},
        signature=bytes(63),
    )
    response = await client.post("/agents/verify-jws", json={"token": token})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
@pytest.mark.parametrize(
    "updates",
    [
        {"typ": None},
        {"typ": "NOT-JWT"},
        {"iat": None},
        {"iat": True},
        {"exp": None},
        {"exp": True},
        {"exp": NOW},
        {"extra": "field"},
    ],
)
async def test_malformed_profile_is_rejected_before_lookup(client, updates) -> None:
    private_key, _public_key = _generate_keypair()
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _token(private_key, "a-unknown", header_updates=updates)},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
async def test_missing_expiry_is_rejected_without_store_lookup(client) -> None:
    private_key, _public_key = _generate_keypair()
    registry = get_app_state().registry
    assert registry is not None
    get_agent = AsyncMock()
    registry.get_agent = get_agent
    response = await client.post(
        "/agents/verify-jws",
        json={
            "token": _token(
                private_key,
                "a-unknown",
                header_updates={"exp": None},
            )
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"
    get_agent.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="missing-action-empty-payload"),
        pytest.param({"amount": 10}, id="missing-action-domain-payload"),
        pytest.param({"action": True}, id="wrong-type-action"),
        pytest.param({"action": ""}, id="blank-action"),
    ],
)
async def test_missing_or_wrong_action_is_invalid_payload(
    client,
    payload: dict[str, object],
) -> None:
    private_key, public_key = _generate_keypair()
    agent_id = await _register(client, public_key)
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _token(private_key, agent_id, payload=payload)},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


@pytest.mark.unit
async def test_unknown_typed_kid_is_404(client) -> None:
    private_key, _public_key = _generate_keypair()
    response = await client.post(
        "/agents/verify-jws",
        json={"token": _token(private_key, "a-unknown")},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "agent_not_found"


@pytest.mark.unit
async def test_lifetime_above_configured_maximum_is_invalid_jws(client) -> None:
    private_key, public_key = _generate_keypair()
    agent_id = await _register(client, public_key)
    response = await client.post(
        "/agents/verify-jws",
        json={
            "token": _token(
                private_key,
                agent_id,
                header_updates={"exp": NOW + 301},
            )
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
@pytest.mark.parametrize(
    "issued_at,expires_at",
    [
        (NOW, NOW),
        (NOW, NOW - 1),
    ],
)
async def test_nonpositive_lifetime_is_invalid_jws(
    client,
    issued_at: int,
    expires_at: int,
) -> None:
    private_key, _public_key = _generate_keypair()
    response = await client.post(
        "/agents/verify-jws",
        json={
            "token": _token(
                private_key,
                "a-unknown",
                header_updates={"iat": issued_at, "exp": expires_at},
            )
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
async def test_future_and_expiry_boundaries_use_canonical_core_clock(client) -> None:
    private_key, public_key = _generate_keypair()
    agent_id = await _register(client, public_key)
    future = await client.post(
        "/agents/verify-jws",
        json={
            "token": _token(
                private_key,
                agent_id,
                header_updates={"iat": NOW + 1, "exp": NOW + 2},
            )
        },
    )
    expired = await client.post(
        "/agents/verify-jws",
        json={
            "token": _token(
                private_key,
                agent_id,
                header_updates={"iat": NOW - 300, "exp": NOW},
            )
        },
    )
    assert future.status_code == 401
    assert future.json()["error"] == "token_not_yet_valid"
    assert expired.status_code == 401
    assert expired.json()["error"] == "token_expired"


@pytest.mark.unit
async def test_request_validation_and_method_contracts_remain(client) -> None:
    missing = await client.post("/agents/verify-jws", json={})
    null = await client.post("/agents/verify-jws", json={"token": None})
    wrong_type = await client.post("/agents/verify-jws", json={"token": 1})
    malformed = await client.post("/agents/verify-jws", json={"token": "not.a.token"})
    wrong_method = await client.get("/agents/verify-jws")
    assert (missing.status_code, missing.json()["error"]) == (400, "missing_field")
    assert (null.status_code, null.json()["error"]) == (400, "missing_field")
    assert (wrong_type.status_code, wrong_type.json()["error"]) == (400, "invalid_field_type")
    assert (malformed.status_code, malformed.json()["error"]) == (400, "invalid_jws")
    assert (wrong_method.status_code, wrong_method.json()["error"]) == (
        405,
        "method_not_allowed",
    )


@pytest.mark.unit
async def test_wrong_content_type_is_unsupported_media_type(client) -> None:
    response = await client.post(
        "/agents/verify-jws",
        content=b'{"token":"abc"}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"


@pytest.mark.unit
async def test_malformed_request_json_is_invalid_json(client) -> None:
    response = await client.post(
        "/agents/verify-jws",
        content=b"{broken",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"
