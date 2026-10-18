"""Router test fixtures — app with lifespan and async client."""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from service_auth.jws import TokenPolicy
from service_auth.signing import generate_keypair

from identity_service.app import create_app
from identity_service.config import clear_settings_cache, get_settings
from identity_service.core.lifespan import lifespan
from identity_service.core.state import get_app_state, reset_app_state
from identity_service.services.agent_registry import AgentRegistry
from tests.fakes.in_memory_agent_store import InMemoryAgentStore


@pytest.fixture
async def app(tmp_path):
    """Create a test app with a temporary database."""
    db_path = tmp_path / "test.db"
    generate_keypair("gateway-identity", tmp_path)
    config_content = f"""
service:
  name: "identity"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8001
  log_level: "info"
logging:
  level: "WARNING"
  directory: "{tmp_path / "logs" / "identity"}"
crypto:
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
token_validation:
  max_lifetime_seconds: 300
  clock_skew_seconds: 0
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
  signing:
    kid: "gateway-identity"
    private_key_path: "gateway-identity.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        settings = get_settings()
        state = get_app_state()
        fake_store = InMemoryAgentStore(db_path=str(db_path))
        state.registry = AgentRegistry(
            store=fake_store,
            public_key_prefix=settings.crypto.public_key_prefix,
            public_key_bytes=settings.crypto.public_key_bytes,
            signature_bytes=settings.crypto.signature_bytes,
            token_policy=TokenPolicy(
                max_lifetime_seconds=settings.token_validation.max_lifetime_seconds,
                clock_skew_seconds=settings.token_validation.clock_skew_seconds,
            ),
        )
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
