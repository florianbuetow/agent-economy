"""T-072 coverage-vs-spec-ID sweep: closes gaps against
docs/specifications/service-tests/identity-service-tests.md, Category 5
(Cross-Cutting Security Assertions) — SEC-01, SEC-02, SEC-03.

New file only — existing test files are acceptance tests and must not be
edited (CLAUDE.md).
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from identity_service.config import get_settings

_AGENT_ID_RE = re.compile(r"^a-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_LEAKAGE_MARKERS = (
    "Traceback",
    "/Users",
    "site-packages",
    '.py"',
    "sqlite3.",
    "SELECT ",
    "INSERT ",
    'File "',
)


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _signed_token(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    *,
    iat: int | None = None,
    exp: int | None = None,
) -> str:
    """Build a real compact JWS; iat/exp are stamped into the protected header."""
    header: dict[str, object] = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": agent_id,
        "aud": "central-bank",
        "resource": "POST /escrow/lock",
    }
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


def _assert_envelope_shape(body: dict[str, object]) -> None:
    """Assert the exact three-field error envelope shape required by the spec."""
    assert set(body) == {"error", "message", "details"}
    assert isinstance(body["error"], str)
    assert isinstance(body["message"], str)
    assert isinstance(body["details"], dict)


@pytest.mark.unit
class TestSec01ErrorEnvelopeConsistency:
    """SEC-01: at least one failing test per documented error code uses the
    exact three-field envelope: error (str), message (str), details (object)."""

    async def test_missing_field_envelope(self, client):
        response = await client.post("/agents/register", json={"public_key": None})
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "missing_field"
        _assert_envelope_shape(body)

    async def test_invalid_field_type_envelope(self, client):
        response = await client.post("/agents/register", json={"name": 123, "public_key": True})
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "invalid_field_type"
        _assert_envelope_shape(body)

    async def test_invalid_json_envelope(self, client):
        response = await client.post(
            "/agents/register",
            content=b"{broken",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "invalid_json"
        _assert_envelope_shape(body)

    async def test_invalid_public_key_envelope(self, client):
        response = await client.post(
            "/agents/register", json={"name": "Alice", "public_key": "rsa:abc"}
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "invalid_public_key"
        _assert_envelope_shape(body)

    async def test_invalid_name_envelope(self, client):
        _, public_key = _generate_keypair()
        response = await client.post(
            "/agents/register", json={"name": "   ", "public_key": public_key}
        )
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "invalid_name"
        _assert_envelope_shape(body)

    async def test_invalid_jws_envelope(self, client):
        response = await client.post("/agents/verify-jws", json={"token": "not.a.valid.jws.token"})
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "invalid_jws"
        _assert_envelope_shape(body)

    async def test_token_expired_envelope(self, client):
        private_key, public_key = _generate_keypair()
        reg = await client.post(
            "/agents/register", json={"name": "Alice", "public_key": public_key}
        )
        agent_id = reg.json()["agent_id"]
        past = int(datetime(2001, 1, 1, tzinfo=UTC).timestamp())
        token = _signed_token(private_key, agent_id, iat=past, exp=past + 60)

        response = await client.post("/agents/verify-jws", json={"token": token})
        assert response.status_code == 401
        body = response.json()
        assert body["error"] == "token_expired"
        _assert_envelope_shape(body)

    async def test_agent_not_found_envelope(self, client):
        response = await client.get("/agents/a-00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        body = response.json()
        assert body["error"] == "agent_not_found"
        _assert_envelope_shape(body)

    async def test_method_not_allowed_envelope(self, client):
        response = await client.get("/agents/register")
        assert response.status_code == 405
        body = response.json()
        assert body["error"] == "method_not_allowed"
        _assert_envelope_shape(body)

    async def test_public_key_exists_envelope(self, client):
        _, public_key = _generate_keypair()
        first = await client.post(
            "/agents/register", json={"name": "Alice", "public_key": public_key}
        )
        assert first.status_code == 201
        response = await client.post(
            "/agents/register", json={"name": "Eve", "public_key": public_key}
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "public_key_exists"
        _assert_envelope_shape(body)

    async def test_payload_too_large_envelope(self, client):
        max_body_size = get_settings().request.max_body_size
        _, public_key = _generate_keypair()
        response = await client.post(
            "/agents/register",
            json={"name": "A" * (max_body_size + 1024), "public_key": public_key},
        )
        assert response.status_code == 413
        body = response.json()
        assert body["error"] == "payload_too_large"
        _assert_envelope_shape(body)

    async def test_unsupported_media_type_envelope(self, client):
        response = await client.post(
            "/agents/register",
            content=b'{"name":"Alice"}',
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 415
        body = response.json()
        assert body["error"] == "unsupported_media_type"
        _assert_envelope_shape(body)


@pytest.mark.unit
class TestSec02NoInternalErrorLeakage:
    """SEC-02: representative failures never leak stack traces, SQL fragments,
    file paths, or driver internals in the `message` field."""

    async def test_invalid_json_does_not_leak(self, client):
        response = await client.post(
            "/agents/register",
            content=b"{broken",
            headers={"Content-Type": "application/json"},
        )
        message = response.json()["message"]
        assert not any(marker in message for marker in _LEAKAGE_MARKERS)

    async def test_invalid_jws_does_not_leak(self, client):
        response = await client.post("/agents/verify-jws", json={"token": "not.a.valid.jws.token"})
        message = response.json()["message"]
        assert not any(marker in message for marker in _LEAKAGE_MARKERS)

    async def test_duplicate_key_does_not_leak(self, client):
        _, public_key = _generate_keypair()
        await client.post("/agents/register", json={"name": "Alice", "public_key": public_key})
        response = await client.post(
            "/agents/register", json={"name": "Eve", "public_key": public_key}
        )
        message = response.json()["message"]
        assert not any(marker in message for marker in _LEAKAGE_MARKERS)

    async def test_malformed_id_does_not_leak(self, client):
        response = await client.get("/agents/not-a-valid-id")
        message = response.json()["message"]
        assert not any(marker in message for marker in _LEAKAGE_MARKERS)


@pytest.mark.unit
async def test_sec03_agent_ids_are_opaque_and_random_format(client):
    """SEC-03: every returned agent_id matches `a-<uuid4>` across 5+ registrations."""
    agent_ids: list[str] = []
    for i in range(5):
        _, public_key = _generate_keypair()
        response = await client.post(
            "/agents/register", json={"name": f"Agent{i}", "public_key": public_key}
        )
        assert response.status_code == 201
        agent_ids.append(response.json()["agent_id"])

    for agent_id in agent_ids:
        assert _AGENT_ID_RE.match(agent_id), f"{agent_id!r} does not match a-<uuid4>"
    assert len(set(agent_ids)) == len(agent_ids)
