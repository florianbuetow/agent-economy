"""T-072 coverage-vs-spec-ID sweep: closes gaps against
docs/specifications/service-tests/identity-service-tests.md, Category 1
(Registration). See that file for the full normative behavior each test below
cites by spec ID.

New file only — existing test files are acceptance tests and must not be
edited (CLAUDE.md).
"""

from __future__ import annotations

import base64
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from identity_service.config import get_settings


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


@pytest.mark.unit
async def test_reg02_second_agent_gets_a_different_id(client):
    """REG-02: registering a second agent with a different key returns a distinct agent_id."""
    _, public_key_a = _generate_keypair()
    _, public_key_b = _generate_keypair()

    resp_a = await client.post(
        "/agents/register", json={"name": "Alice", "public_key": public_key_a}
    )
    resp_b = await client.post("/agents/register", json={"name": "Bob", "public_key": public_key_b})

    assert resp_a.status_code == 201
    assert resp_b.status_code == 201
    assert resp_a.json()["agent_id"] != resp_b.json()["agent_id"]


@pytest.mark.unit
async def test_reg05_duplicate_names_are_allowed(client):
    """REG-05: two distinct keypairs may register under the same display name."""
    _, public_key_a = _generate_keypair()
    _, public_key_b = _generate_keypair()

    resp_a = await client.post(
        "/agents/register", json={"name": "SharedName", "public_key": public_key_a}
    )
    resp_b = await client.post(
        "/agents/register", json={"name": "SharedName", "public_key": public_key_b}
    )

    assert resp_a.status_code == 201
    assert resp_b.status_code == 201
    assert resp_a.json()["agent_id"] != resp_b.json()["agent_id"]


@pytest.mark.unit
async def test_reg07_missing_public_key(client):
    """REG-07: submitting only `name` returns 400 missing_field."""
    response = await client.post("/agents/register", json={"name": "Alice"})
    assert response.status_code == 400
    assert response.json()["error"] == "missing_field"


@pytest.mark.unit
async def test_reg08_null_required_fields(client):
    """REG-08: null `name`/`public_key` returns 400 missing_field."""
    response = await client.post("/agents/register", json={"name": None, "public_key": None})
    assert response.status_code == 400
    assert response.json()["error"] == "missing_field"


@pytest.mark.unit
async def test_reg09_wrong_field_types(client):
    """REG-09: non-string `name`/`public_key` returns 400 invalid_field_type."""
    response = await client.post("/agents/register", json={"name": 123, "public_key": True})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_field_type"


@pytest.mark.unit
@pytest.mark.parametrize("blank_name", ["", "   "])
async def test_reg10_empty_or_whitespace_name(client, blank_name):
    """REG-10: empty or whitespace-only `name` returns 400 invalid_name."""
    _, public_key = _generate_keypair()
    response = await client.post(
        "/agents/register", json={"name": blank_name, "public_key": public_key}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_name"


@pytest.mark.unit
async def test_reg11_invalid_key_prefix(client):
    """REG-11: a public_key with a non-ed25519 prefix returns 400 invalid_public_key."""
    _, valid_key = _generate_keypair()
    raw_key_b64 = valid_key.split(":", 1)[1]
    response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": f"rsa:{raw_key_b64}"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_public_key"


@pytest.mark.unit
async def test_reg12_invalid_key_base64_payload(client):
    """REG-12: a public_key whose payload is not valid base64 returns 400 invalid_public_key."""
    response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": "ed25519:%%%not-base64%%%"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_public_key"


@pytest.mark.unit
async def test_reg13_invalid_key_length_after_decode(client):
    """REG-13: a base64 payload that decodes to != 32 bytes returns 400 invalid_public_key."""
    short_key_b64 = base64.b64encode(os.urandom(16)).decode()
    response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": f"ed25519:{short_key_b64}"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_public_key"


@pytest.mark.unit
async def test_reg14_all_zero_key_is_rejected(client):
    """REG-14: an all-zero 32-byte key is rejected as a degenerate identity point."""
    zero_key_b64 = base64.b64encode(b"\x00" * 32).decode()
    response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": f"ed25519:{zero_key_b64}"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_public_key"


@pytest.mark.unit
async def test_reg15_mass_assignment_resistance(client):
    """REG-15: server-generated agent_id/registered_at win over client-supplied values,
    and unknown extra fields (e.g. is_admin) are silently ignored rather than reflected."""
    _, public_key = _generate_keypair()
    response = await client.post(
        "/agents/register",
        json={
            "name": "Alice",
            "public_key": public_key,
            "agent_id": "a-forged-id",
            "registered_at": "1970-01-01T00:00:00Z",
            "is_admin": True,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["agent_id"] != "a-forged-id"
    assert data["agent_id"].startswith("a-")
    assert data["registered_at"] != "1970-01-01T00:00:00Z"
    assert "is_admin" not in data


@pytest.mark.unit
async def test_reg18_oversized_request_body(client):
    """REG-18: a request body larger than the configured max size returns 413 payload_too_large."""
    max_body_size = get_settings().request.max_body_size
    _, public_key = _generate_keypair()
    oversized_name = "A" * (max_body_size + 1024)

    response = await client.post(
        "/agents/register",
        json={"name": oversized_name, "public_key": public_key},
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"
