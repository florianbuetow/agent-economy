"""Router test fixtures — app with lifespan and async client."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from identity_service.app import create_app
from identity_service.config import clear_settings_cache
from identity_service.core.lifespan import lifespan
from identity_service.core.state import reset_app_state


@pytest.fixture
async def app(tmp_path):
    """Create a test app with a temporary database."""
    db_path = tmp_path / "test.db"
    config_content = f"""
service:
  name: "identity"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8001
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
database:
  path: "{db_path}"
crypto:
  algorithm: "ed25519"
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
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
