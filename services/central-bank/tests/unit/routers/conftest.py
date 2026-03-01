"""Router test fixtures with mocked Identity service."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from httpx import ASGITransport, AsyncClient
from joserfc import jws
from joserfc.jwk import OKPKey

from central_bank_service.app import create_app
from central_bank_service.config import clear_settings_cache
from central_bank_service.core.lifespan import lifespan
from central_bank_service.core.state import get_app_state, reset_app_state

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
  host: "0.0.0.0"
  port: 8002
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
database:
  path: "{db_path}"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
request:
  max_body_size: 1048576
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        # Replace the real Identity client with a mock
        state = get_app_state()
        mock_identity = AsyncMock()
        mock_identity.close = AsyncMock()

        # Default: verify_jws succeeds by actually checking the token structure
        # Tests will configure specific behaviors
        state.identity_client = mock_identity

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
