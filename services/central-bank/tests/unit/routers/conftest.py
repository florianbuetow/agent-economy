"""Router test fixtures with mocked Identity service."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from httpx import ASGITransport, AsyncClient
from joserfc import jws
from joserfc.jwk import OKPKey
from service_commons.exceptions import ServiceError

from central_bank_service.app import create_app
from central_bank_service.config import clear_settings_cache
from central_bank_service.core.lifespan import lifespan
from central_bank_service.core.state import get_app_state, reset_app_state
from tests.fakes.in_memory_ledger_store import InMemoryLedgerStore

PLATFORM_AGENT_ID = "a-platform-test-id"


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


def make_jws_token(private_key: Ed25519PrivateKey, agent_id: str, payload: dict[str, Any]) -> str:
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


def _decode_jws_payload(token: str) -> dict[str, Any]:
    """Decode the base64url payload from a compact JWS token."""
    parts = token.split(".")
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _make_delegating_verify_jws(state_ref: Any) -> Any:
    """Create a verify_jws mock that delegates to platform_agent.validate_certificate.

    This ensures tests that override validate_certificate (e.g. with InvalidSignature)
    will see the same behavior when verification goes through identity_client.verify_jws.

    Signature-related errors (InvalidSignature, ValueError) are mapped to valid=False.
    Connectivity errors (ConnectionError, TimeoutError, etc.) are raised as ServiceError(502)
    to simulate what the real IdentityClient would do.
    """

    async def _delegating_verify_jws(token: str) -> dict[str, Any]:
        parts = token.split(".")
        header_b64 = parts[0]
        padded_header = header_b64 + "=" * (-len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(padded_header))
        agent_id = header.get("kid", "")

        try:
            payload = state_ref.platform_agent.validate_certificate(token)
        except (InvalidSignature, ValueError):
            return {"valid": False, "reason": "signature mismatch"}
        except Exception as exc:
            raise ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

        return {"valid": True, "agent_id": agent_id, "payload": payload}

    return _delegating_verify_jws


@pytest.fixture
def platform_keypair():
    """Generate a platform keypair."""
    return _generate_keypair()


@pytest.fixture
def agent_keypair():
    """Generate an agent keypair."""
    return _generate_keypair()


@pytest.fixture
async def app(tmp_path):
    """Create a test app with a temporary database and mocked Identity client."""
    db_path = tmp_path / "test.db"
    config_content = f"""
service:
  name: "central-bank"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8002
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
database:
  path: "{db_path}"
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
request:
  max_body_size: 1048576
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        # Replace runtime clients with mocks
        state = get_app_state()
        state.ledger = InMemoryLedgerStore(db_path=str(db_path))
        mock_identity = AsyncMock()
        mock_identity.close = AsyncMock()
        mock_identity.get_agent = AsyncMock(
            return_value={"agent_id": "a-test-agent", "name": "Test Agent"},
        )
        state.identity_client = mock_identity
        mock_platform = MagicMock()
        mock_platform.agent_id = PLATFORM_AGENT_ID
        mock_platform.validate_certificate = MagicMock(side_effect=_decode_jws_payload)
        mock_platform.close = AsyncMock()
        state.platform_agent = mock_platform
        mock_identity.verify_jws = AsyncMock(side_effect=_make_delegating_verify_jws(state))

        yield test_app

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


@pytest.fixture
async def client(app):
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
