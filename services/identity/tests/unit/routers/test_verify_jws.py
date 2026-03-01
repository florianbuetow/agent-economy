"""Tests for POST /agents/verify-jws endpoint."""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from joserfc import jws
from joserfc.jwk import OKPKey


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


def _make_jws_token(private_key: Ed25519PrivateKey, agent_id: str, payload: dict) -> str:
    """Create a JWS compact token signed by the given private key."""
    raw_private = private_key.private_bytes_raw()
    raw_public = private_key.public_key().public_bytes_raw()
    jwk_dict = {
        "kty": "OKP",
        "crv": "Ed25519",
        "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
        "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
    }
    key = OKPKey.import_key(jwk_dict)
    protected = {"alg": "EdDSA", "kid": agent_id}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return jws.serialize_compact(protected, payload_bytes, key, algorithms=["EdDSA"])


async def _register_agent(client, name: str, public_key: str) -> str:
    """Register an agent and return agent_id."""
    resp = await client.post(
        "/agents/register",
        json={"name": name, "public_key": public_key},
    )
    assert resp.status_code == 201
    return resp.json()["agent_id"]


@pytest.mark.unit
class TestVerifyJWSValid:
    """Tests for valid JWS verification."""

    async def test_valid_jws_returns_payload(self, client):
        """POST /agents/verify-jws with valid token returns valid=true and payload."""
        private_key, public_key = _generate_keypair()
        agent_id = await _register_agent(client, "Alice", public_key)

        payload = {"action": "escrow_lock", "amount": 10}
        token = _make_jws_token(private_key, agent_id, payload)

        response = await client.post("/agents/verify-jws", json={"token": token})
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["agent_id"] == agent_id
        assert data["payload"]["action"] == "escrow_lock"
        assert data["payload"]["amount"] == 10

    async def test_empty_payload_is_valid(self, client):
        """POST /agents/verify-jws with empty payload object returns valid=true."""
        private_key, public_key = _generate_keypair()
        agent_id = await _register_agent(client, "Alice", public_key)

        token = _make_jws_token(private_key, agent_id, {})

        response = await client.post("/agents/verify-jws", json={"token": token})
        assert response.status_code == 200
        assert response.json()["valid"] is True
        assert response.json()["payload"] == {}


@pytest.mark.unit
class TestVerifyJWSInvalid:
    """Tests for invalid JWS tokens."""

    async def test_wrong_signer_returns_false(self, client):
        """JWS signed by wrong key returns valid=false."""
        _private_a, public_key_a = _generate_keypair()
        private_key_b, _public_key_b = _generate_keypair()
        agent_id = await _register_agent(client, "Alice", public_key_a)

        # Sign with B's key but claim to be agent A
        token = _make_jws_token(private_key_b, agent_id, {"action": "test"})

        response = await client.post("/agents/verify-jws", json={"token": token})
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert "reason" in data

    async def test_nonexistent_agent_returns_404(self, client):
        """JWS with kid pointing to unknown agent returns 404."""
        private_key, _public_key = _generate_keypair()
        token = _make_jws_token(
            private_key,
            "a-00000000-0000-0000-0000-000000000000",
            {"action": "test"},
        )

        response = await client.post("/agents/verify-jws", json={"token": token})
        assert response.status_code == 404
        assert response.json()["error"] == "AGENT_NOT_FOUND"

    async def test_malformed_token_returns_400(self, client):
        """Garbage token string returns 400 INVALID_JWS."""
        response = await client.post(
            "/agents/verify-jws",
            json={"token": "not.a.valid.jws.token"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_missing_token_field_returns_400(self, client):
        """Missing token field returns 400 MISSING_FIELD."""
        response = await client.post("/agents/verify-jws", json={})
        assert response.status_code == 400
        assert response.json()["error"] == "MISSING_FIELD"

    async def test_null_token_returns_400(self, client):
        """Null token field returns 400 MISSING_FIELD."""
        response = await client.post("/agents/verify-jws", json={"token": None})
        assert response.status_code == 400
        assert response.json()["error"] == "MISSING_FIELD"

    async def test_non_string_token_returns_400(self, client):
        """Non-string token returns 400 INVALID_FIELD_TYPE."""
        response = await client.post("/agents/verify-jws", json={"token": 12345})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_FIELD_TYPE"

    async def test_missing_kid_in_header_returns_400(self, client):
        """JWS without kid in header returns 400 INVALID_JWS."""
        private_key, public_key = _generate_keypair()
        await _register_agent(client, "Alice", public_key)

        # Build token without kid
        raw_private = private_key.private_bytes_raw()
        raw_public = private_key.public_key().public_bytes_raw()
        jwk_dict = {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
            "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
        }
        key = OKPKey.import_key(jwk_dict)
        protected = {"alg": "EdDSA"}  # no kid
        token = jws.serialize_compact(protected, b'{"action":"test"}', key, algorithms=["EdDSA"])

        response = await client.post("/agents/verify-jws", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_wrong_algorithm_returns_400(self, client):
        """JWS with non-EdDSA algorithm in header returns 400 INVALID_JWS."""
        response = await client.post(
            "/agents/verify-jws",
            json={"token": "eyJhbGciOiJIUzI1NiIsImtpZCI6ImEtdGVzdCJ9.e30.invalid"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_non_json_payload_returns_400(self, client):
        """JWS whose decoded payload is not valid JSON returns 400 INVALID_JWS."""
        private_key, public_key = _generate_keypair()
        agent_id = await _register_agent(client, "Alice", public_key)

        raw_private = private_key.private_bytes_raw()
        raw_public = private_key.public_key().public_bytes_raw()
        jwk_dict = {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
            "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
        }
        key = OKPKey.import_key(jwk_dict)
        protected = {"alg": "EdDSA", "kid": agent_id}
        token = jws.serialize_compact(protected, b"not json at all", key, algorithms=["EdDSA"])

        response = await client.post("/agents/verify-jws", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
class TestVerifyJWSContentType:
    """Tests for content-type and method validation."""

    async def test_wrong_content_type_returns_415(self, client):
        """POST /agents/verify-jws with text/plain returns 415."""
        response = await client.post(
            "/agents/verify-jws",
            content=b'{"token":"abc"}',
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 415
        assert response.json()["error"] == "UNSUPPORTED_MEDIA_TYPE"

    async def test_get_method_returns_405(self, client):
        """GET /agents/verify-jws returns 405."""
        response = await client.get("/agents/verify-jws")
        assert response.status_code == 405
        assert response.json()["error"] == "METHOD_NOT_ALLOWED"

    async def test_invalid_json_body_returns_400(self, client):
        """Malformed JSON body returns 400 INVALID_JSON."""
        response = await client.post(
            "/agents/verify-jws",
            content=b"{broken",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JSON"
