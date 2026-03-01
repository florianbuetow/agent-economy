"""Unit tests for agent endpoints."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


@pytest.mark.unit
async def test_register_valid_agent(client):
    """POST /agents/register with valid data returns 201."""
    _, public_key = _generate_keypair()
    response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )
    assert response.status_code == 201

    data = response.json()
    assert data["name"] == "Alice"
    assert data["public_key"] == public_key
    assert data["agent_id"].startswith("a-")
    assert "registered_at" in data


@pytest.mark.unit
async def test_register_missing_name(client):
    """POST /agents/register without name returns 400 MISSING_FIELD."""
    _, public_key = _generate_keypair()
    response = await client.post(
        "/agents/register",
        json={"public_key": public_key},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "MISSING_FIELD"


@pytest.mark.unit
async def test_register_duplicate_key(client):
    """POST /agents/register with duplicate key returns 409."""
    _, public_key = _generate_keypair()
    response1 = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )
    assert response1.status_code == 201

    response2 = await client.post(
        "/agents/register",
        json={"name": "Eve", "public_key": public_key},
    )
    assert response2.status_code == 409
    assert response2.json()["error"] == "PUBLIC_KEY_EXISTS"


@pytest.mark.unit
async def test_get_agent(client):
    """GET /agents/{agent_id} returns the agent record."""
    _, public_key = _generate_keypair()
    reg_response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )
    agent_id = reg_response.json()["agent_id"]

    response = await client.get(f"/agents/{agent_id}")
    assert response.status_code == 200
    assert response.json()["agent_id"] == agent_id
    assert response.json()["public_key"] == public_key


@pytest.mark.unit
async def test_get_agent_not_found(client):
    """GET /agents/{agent_id} with unknown ID returns 404."""
    response = await client.get("/agents/a-00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"] == "AGENT_NOT_FOUND"


@pytest.mark.unit
async def test_list_agents_empty(client):
    """GET /agents on fresh system returns empty list."""
    response = await client.get("/agents")
    assert response.status_code == 200
    assert response.json() == {"agents": []}


@pytest.mark.unit
async def test_list_agents_omits_public_key(client):
    """GET /agents does not include public_key in items."""
    _, public_key = _generate_keypair()
    await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )

    response = await client.get("/agents")
    assert response.status_code == 200
    agents = response.json()["agents"]
    assert len(agents) == 1
    assert "public_key" not in agents[0]


@pytest.mark.unit
async def test_verify_valid_signature(client):
    """POST /agents/verify with correct signature returns valid=true."""
    private_key, public_key = _generate_keypair()
    reg_response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )
    agent_id = reg_response.json()["agent_id"]

    payload = b"hello world"
    payload_b64 = base64.b64encode(payload).decode()
    signature = private_key.sign(payload)
    signature_b64 = base64.b64encode(signature).decode()

    response = await client.post(
        "/agents/verify",
        json={
            "agent_id": agent_id,
            "payload": payload_b64,
            "signature": signature_b64,
        },
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["agent_id"] == agent_id


@pytest.mark.unit
async def test_verify_wrong_signature(client):
    """POST /agents/verify with wrong signature returns valid=false."""
    _private_key_a, public_key_a = _generate_keypair()
    private_key_b, _ = _generate_keypair()

    reg_response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key_a},
    )
    agent_id = reg_response.json()["agent_id"]

    payload = b"hello world"
    payload_b64 = base64.b64encode(payload).decode()
    wrong_sig = private_key_b.sign(payload)
    signature_b64 = base64.b64encode(wrong_sig).decode()

    response = await client.post(
        "/agents/verify",
        json={
            "agent_id": agent_id,
            "payload": payload_b64,
            "signature": signature_b64,
        },
    )
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["reason"] == "signature mismatch"


@pytest.mark.unit
async def test_register_invalid_json(client):
    """POST /agents/register with broken JSON returns 400 INVALID_JSON."""
    response = await client.post(
        "/agents/register",
        content=b"{broken",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_JSON"


@pytest.mark.unit
async def test_register_wrong_content_type(client):
    """POST /agents/register with text/plain returns 415."""
    response = await client.post(
        "/agents/register",
        content=b'{"name":"Alice"}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "UNSUPPORTED_MEDIA_TYPE"


@pytest.mark.unit
async def test_method_not_allowed_register(client):
    """GET /agents/register returns 405."""
    response = await client.get("/agents/register")
    assert response.status_code == 405
    assert response.json()["error"] == "METHOD_NOT_ALLOWED"
