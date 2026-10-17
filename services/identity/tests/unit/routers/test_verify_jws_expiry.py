"""Strict Identity lifetime-policy regressions for the documented JWS-15 to JWS-17 cases."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from service_auth import jws as core_jws

ANCHOR = datetime(2030, 1, 1, tzinfo=UTC)
NOW = int(ANCHOR.timestamp())


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, f"ed25519:{base64.b64encode(public).decode()}"


def _signed_token(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    *,
    issued_at: int,
    expires_at: int | None,
) -> str:
    header: dict[str, object] = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": agent_id,
        "iat": issued_at,
        "aud": "central-bank",
        "resource": "POST /escrow/lock",
    }
    if expires_at is not None:
        header["exp"] = expires_at
    payload = {"action": "escrow_lock", "amount": 10}
    header_segment = _b64url(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    payload_segment = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    return f"{header_segment}.{payload_segment}.{_b64url(private_key.sign(signing_input))}"


async def _register(client, public_key: str) -> str:
    response = await client.post(
        "/agents/register",
        json={"name": "Lifetime Agent", "public_key": public_key},
    )
    assert response.status_code == 201
    return response.json()["agent_id"]


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_jws, "_clock", lambda: ANCHOR)


@pytest.mark.unit
async def test_expired_token_is_rejected(client) -> None:
    private_key, public_key = _keypair()
    agent_id = await _register(client, public_key)
    token = _signed_token(
        private_key,
        agent_id,
        issued_at=NOW - 300,
        expires_at=NOW,
    )
    response = await client.post("/agents/verify-jws", json={"token": token})
    assert response.status_code == 401
    assert response.json()["error"] == "token_expired"


@pytest.mark.unit
async def test_valid_bounded_lifetime_is_accepted(client) -> None:
    private_key, public_key = _keypair()
    agent_id = await _register(client, public_key)
    token = _signed_token(
        private_key,
        agent_id,
        issued_at=NOW,
        expires_at=NOW + 300,
    )
    response = await client.post("/agents/verify-jws", json={"token": token})
    assert response.status_code == 200
    assert response.json()["valid"] is True


@pytest.mark.unit
async def test_token_without_expiry_is_rejected(client) -> None:
    private_key, public_key = _keypair()
    agent_id = await _register(client, public_key)
    token = _signed_token(
        private_key,
        agent_id,
        issued_at=NOW,
        expires_at=None,
    )
    response = await client.post("/agents/verify-jws", json={"token": token})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
async def test_canonical_core_clock_controls_admission(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, public_key = _keypair()
    agent_id = await _register(client, public_key)
    token = _signed_token(
        private_key,
        agent_id,
        issued_at=NOW,
        expires_at=NOW + 100,
    )
    monkeypatch.setattr(
        core_jws,
        "_clock",
        lambda: datetime.fromtimestamp(NOW + 99, tz=UTC),
    )
    accepted = await client.post("/agents/verify-jws", json={"token": token})
    assert accepted.status_code == 200

    monkeypatch.setattr(
        core_jws,
        "_clock",
        lambda: datetime.fromtimestamp(NOW + 100, tz=UTC),
    )
    expired = await client.post("/agents/verify-jws", json={"token": token})
    assert expired.status_code == 401
    assert expired.json()["error"] == "token_expired"
