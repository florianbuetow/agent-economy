"""POST /agents/verify-jws — protected-header ``exp`` enforcement (WP-03).

WP-02 taught Identity's joserfc registry to *tolerate* ``iat``/``exp`` protected-header
params. WP-03 completes the hand-off: a token whose header ``exp`` is in the past is
rejected with ``token_expired`` (401), while tokens without ``iat``/``exp`` keep the
legacy tolerance (accepted). Time is read from an injectable clock seam, never env vars.

Tokens are signed for real with Ed25519 over the compact JWS signing input, mirroring the
platform signer's canonicalization (``iat``/``exp`` live in the protected header).
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from identity_service.services import agent_registry

if TYPE_CHECKING:
    from httpx import AsyncClient


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, f"ed25519:{base64.b64encode(pub_bytes).decode()}"


async def _register(client: AsyncClient, name: str, public_key: str) -> str:
    resp = await client.post("/agents/register", json={"name": name, "public_key": public_key})
    assert resp.status_code == 201
    return resp.json()["agent_id"]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _signed_token(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    *,
    iat: int | None = None,
    exp: int | None = None,
) -> str:
    """Build a real compact JWS; ``iat``/``exp`` are stamped into the protected header."""
    header: dict[str, object] = {"alg": "EdDSA", "kid": agent_id}
    if iat is not None:
        header["iat"] = iat
    if exp is not None:
        header["exp"] = exp
    payload = {"action": "escrow_lock", "amount": 10}
    header_b64 = _b64url(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input)
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"


@pytest.mark.unit
async def test_expired_token_rejected(client: AsyncClient) -> None:
    """A token whose header exp is in the past is rejected with token_expired (401)."""
    private_key, public_key = _keypair()
    agent_id = await _register(client, "Alice", public_key)

    past = int(datetime(2001, 1, 1, tzinfo=UTC).timestamp())
    token = _signed_token(private_key, agent_id, iat=past, exp=past + 60)
    resp = await client.post("/agents/verify-jws", json={"token": token})

    assert resp.status_code == 401
    assert resp.json()["error"] == "token_expired"


@pytest.mark.unit
async def test_future_exp_token_accepted(client: AsyncClient) -> None:
    """A token with a future exp verifies successfully."""
    private_key, public_key = _keypair()
    agent_id = await _register(client, "Bob", public_key)

    now = int(datetime.now(UTC).timestamp())
    token = _signed_token(private_key, agent_id, iat=now, exp=now + 3600)
    resp = await client.post("/agents/verify-jws", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@pytest.mark.unit
async def test_token_without_exp_still_accepted(client: AsyncClient) -> None:
    """Legacy tolerance: a token without iat/exp is still accepted."""
    private_key, public_key = _keypair()
    agent_id = await _register(client, "Carol", public_key)

    token = _signed_token(private_key, agent_id)
    resp = await client.post("/agents/verify-jws", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@pytest.mark.unit
async def test_clock_seam_controls_expiry(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The injectable clock seam decides whether a fixed-exp token is expired."""
    private_key, public_key = _keypair()
    agent_id = await _register(client, "Dora", public_key)

    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    anchor_ts = int(anchor.timestamp())
    token = _signed_token(private_key, agent_id, iat=anchor_ts, exp=anchor_ts + 100)

    # Identity clock before exp → valid.
    monkeypatch.setattr(agent_registry, "_clock", lambda: anchor)
    resp = await client.post("/agents/verify-jws", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

    # Identity clock after exp → expired.
    monkeypatch.setattr(agent_registry, "_clock", lambda: datetime(2027, 1, 1, tzinfo=UTC))
    resp = await client.post("/agents/verify-jws", json={"token": token})
    assert resp.status_code == 401
    assert resp.json()["error"] == "token_expired"
