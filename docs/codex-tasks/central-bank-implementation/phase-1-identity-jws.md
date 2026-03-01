# Phase 1 — Identity Service JWS Extension

## Overview

Extend the Identity service with a `POST /agents/verify-jws` endpoint that verifies JWS compact tokens signed with Ed25519/EdDSA. This endpoint is the foundation for all authenticated operations in the Central Bank (and other services).

## Working Directory

All commands run from `services/identity/`.

---

## Task A1: Add joserfc dependency

### Step 1.1: Add joserfc to dependencies

In `services/identity/pyproject.toml`, add `"joserfc>=1.0.0"` to the `dependencies` list, after `"cryptography>=44.0.0"`.

### Step 1.2: Install

```bash
cd services/identity && just init
```

Expected: dependencies install successfully, `uv.lock` updated.

### Step 1.3: Commit

```bash
git add services/identity/pyproject.toml services/identity/uv.lock
git commit -m "feat(identity): add joserfc dependency for JWS verification"
```

---

## Task A2: Write failing tests for POST /agents/verify-jws

### Step 2.1: Write the failing tests

Create `services/identity/tests/unit/routers/test_verify_jws.py`:

```python
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
    return jws.serialize_compact(protected, payload_bytes, key)


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
        token = jws.serialize_compact(protected, b'{"action":"test"}', key)

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
        token = jws.serialize_compact(protected, b"not json at all", key)

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
```

### Step 2.2: Run tests to verify they fail

```bash
cd services/identity && uv run pytest tests/unit/routers/test_verify_jws.py -v
```

Expected: FAIL — endpoint does not exist yet (404 or import errors)

### Step 2.3: Commit

```bash
git add services/identity/tests/unit/routers/test_verify_jws.py
git commit -m "test(identity): add failing tests for POST /agents/verify-jws"
```

---

## Task A3: Implement JWS verification in AgentRegistry

### Step 3.1: Add the `verify_jws` method to `AgentRegistry`

Modify `services/identity/src/identity_service/services/agent_registry.py`. Add this method to the `AgentRegistry` class, after the existing `verify_signature` method:

```python
def verify_jws(self, token: str) -> dict[str, object]:
    """
    Verify a JWS compact token and return the payload.

    Extracts the kid (agent_id) from the protected header, looks up the
    agent's public key, and verifies the EdDSA signature.

    Returns {"valid": True, "agent_id": "...", "payload": {...}} on success,
    or {"valid": False, "reason": "..."} on signature mismatch.

    Raises:
        ServiceError: INVALID_JWS if token is malformed, missing kid,
            wrong algorithm, or payload is not valid JSON.
        ServiceError: AGENT_NOT_FOUND if kid references unknown agent.
    """
    # Parse the JWS header without verifying signature yet
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ServiceError(
                "INVALID_JWS",
                "Token is not a valid JWS compact serialization",
                400,
                {},
            )

        # Decode protected header
        import base64 as b64mod
        # Add padding
        header_b64 = parts[0]
        padding = 4 - len(header_b64) % 4
        if padding != 4:
            header_b64 += "=" * padding
        header_bytes = b64mod.urlsafe_b64decode(header_b64)
        header = json.loads(header_bytes)
    except (ServiceError,):
        raise
    except Exception as exc:
        raise ServiceError(
            "INVALID_JWS",
            "Token is not a valid JWS compact serialization",
            400,
            {},
        ) from exc

    # Validate header fields
    alg = header.get("alg")
    if alg != "EdDSA":
        raise ServiceError(
            "INVALID_JWS",
            "Only EdDSA algorithm is supported",
            400,
            {},
        )

    kid = header.get("kid")
    if not kid or not isinstance(kid, str):
        raise ServiceError(
            "INVALID_JWS",
            "JWS header must contain a 'kid' field with the agent_id",
            400,
            {},
        )

    # Look up agent
    agent = self.get_agent(kid)
    if agent is None:
        raise ServiceError("AGENT_NOT_FOUND", "Agent not found", 404, {})

    # Extract raw public key bytes
    public_key_str: str = agent["public_key"]
    key_b64 = public_key_str.split(":", 1)[1]
    raw_public = base64.b64decode(key_b64)

    # Build OKP JWK for joserfc
    from joserfc.jwk import OKPKey

    jwk_dict = {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
    }
    public_jwk = OKPKey.import_key(jwk_dict)

    # Verify signature
    from joserfc import jws as jws_module
    from joserfc.errors import BadSignatureError

    try:
        obj = jws_module.deserialize_compact(token, public_jwk, algorithms=["EdDSA"])
    except BadSignatureError:
        return {"valid": False, "reason": "signature mismatch"}
    except Exception as exc:
        raise ServiceError(
            "INVALID_JWS",
            "Token verification failed",
            400,
            {},
        ) from exc

    # Decode payload as JSON
    try:
        payload = json.loads(obj.payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JWS",
            "JWS payload is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(payload, dict):
        raise ServiceError(
            "INVALID_JWS",
            "JWS payload must be a JSON object",
            400,
            {},
        )

    return {"valid": True, "agent_id": kid, "payload": payload}
```

Add these imports at the top of the file (if not already present):
- `import json`

### Step 3.2: Run tests

```bash
cd services/identity && uv run pytest tests/unit/routers/test_verify_jws.py -v
```

Expected: Still fails — the router endpoint doesn't exist yet.

### Step 3.3: Commit

```bash
git add services/identity/src/identity_service/services/agent_registry.py
git commit -m "feat(identity): add verify_jws method to AgentRegistry"
```

---

## Task A4: Add verify-jws router endpoint and wire middleware

### Step 4.1: Add the POST /agents/verify-jws endpoint

In `services/identity/src/identity_service/routers/agents.py`, add the new endpoint. It must go AFTER `verify_signature` and BEFORE the method-not-allowed handlers:

```python
@router.post("/agents/verify-jws")
async def verify_jws(request: Request) -> dict[str, object]:
    """Verify a JWS compact token."""
    body = await request.body()
    data = _parse_json_body(body)
    _validate_required_fields(data, ["token"])
    _validate_string_fields(data, ["token"])

    state = get_app_state()
    if state.registry is None:
        msg = "Registry not initialized"
        raise RuntimeError(msg)

    return state.registry.verify_jws(data["token"])
```

Also add a method-not-allowed handler for verify-jws (BEFORE the `/agents/{agent_id}` route):

```python
@router.api_route(
    "/agents/verify-jws",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def verify_jws_method_not_allowed(_request: Request) -> None:
    """Reject wrong methods on /agents/verify-jws."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})
```

### Step 4.2: Update middleware whitelist

In `services/identity/src/identity_service/core/middleware.py`, add the new endpoint to the middleware whitelist:

Change:
```python
if (method, path) not in {
    ("POST", "/agents/register"),
    ("POST", "/agents/verify"),
}:
```

To:
```python
if (method, path) not in {
    ("POST", "/agents/register"),
    ("POST", "/agents/verify"),
    ("POST", "/agents/verify-jws"),
}:
```

### Step 4.3: Run tests

```bash
cd services/identity && uv run pytest tests/unit/routers/test_verify_jws.py -v
```

Expected: All tests PASS

### Step 4.4: Run full test suite

```bash
cd services/identity && just test-unit
```

Expected: All existing tests still pass, plus new tests pass.

### Step 4.5: Run CI

```bash
cd services/identity && just ci-quiet
```

Expected: All checks pass.

### Step 4.6: Commit

```bash
git add services/identity/src/identity_service/routers/agents.py services/identity/src/identity_service/core/middleware.py
git commit -m "feat(identity): add POST /agents/verify-jws endpoint"
```

---

## Verification

After completing all A-tasks:

```bash
cd services/identity && just ci-quiet
```

All CI checks must pass, including the new verify-jws tests.
