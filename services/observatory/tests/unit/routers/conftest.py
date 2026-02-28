"""Router test fixtures -- app with lifespan and async client."""

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observatory_service.app import create_app
from observatory_service.config import clear_settings_cache
from observatory_service.core.lifespan import lifespan
from observatory_service.core.state import reset_app_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_config(tmp_path: Path, db_path: str) -> Path:
    """Write a test config.yaml pointing at the given database path."""
    config_content = f"""
service:
  name: "observatory"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8006
  log_level: "info"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "{db_path}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  dist_path: "{tmp_path}/dist"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return config_path


async def _make_app(config_path: Path):
    """Create a FastAPI test app using the given config file."""
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        yield test_app

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


# ---------------------------------------------------------------------------
# Original fixtures (backward compatibility)
# ---------------------------------------------------------------------------
@pytest.fixture
async def app(tmp_path):
    """Create a test app with temporary config (empty database)."""
    config_path = _write_config(tmp_path, f"{tmp_path}/test.db")
    async for test_app in _make_app(config_path):
        yield test_app


@pytest.fixture
async def client(app):
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Seeded fixtures — pre-populated standard economy
# ---------------------------------------------------------------------------
@pytest.fixture
async def seeded_app(seeded_db_path, tmp_path):
    """Create a FastAPI app with config pointing to the seeded database."""
    config_path = _write_config(tmp_path, str(seeded_db_path))
    async for test_app in _make_app(config_path):
        yield test_app


@pytest.fixture
async def seeded_client(seeded_app):
    """Async HTTP client backed by the seeded standard economy database."""
    transport = ASGITransport(app=seeded_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Empty fixtures — schema only, no data
# ---------------------------------------------------------------------------
@pytest.fixture
async def empty_app(empty_db_path, tmp_path):
    """Create a FastAPI app with config pointing to an empty (schema-only) database."""
    config_path = _write_config(tmp_path, str(empty_db_path))
    async for test_app in _make_app(config_path):
        yield test_app


@pytest.fixture
async def empty_client(empty_app):
    """Async HTTP client backed by an empty (schema-only) database."""
    transport = ASGITransport(app=empty_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
