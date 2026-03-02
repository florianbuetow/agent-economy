"""Integration test fixtures with seeded SQLite database."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from ui_service.app import create_app
from ui_service.config import clear_settings_cache
from ui_service.core.lifespan import lifespan
from ui_service.core.state import reset_app_state

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"
INTEGRATION_TESTS_DIR = Path(__file__).resolve().parent

if str(INTEGRATION_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_TESTS_DIR))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create and seed a SQLite database, returning its path."""
    from helpers import insert_seed_data  # noqa: PLC0415

    db_file = tmp_path / "economy.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("PRAGMA journal_mode=WAL")
    insert_seed_data(conn)
    conn.close()
    return db_file


@pytest.fixture
async def app(db_path: Path, tmp_path: Path):
    """Create test app pointing at the seeded database."""
    web_dir = tmp_path / "web"
    web_dir.mkdir(exist_ok=True)
    (web_dir / "index.html").write_text("<html><body>Test</body></html>")

    config_content = f"""\
service:
  name: "ui"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
logging:
  level: "WARNING"
  directory: "{tmp_path / "logs"}"
database:
  path: "{db_path}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "{web_dir}"
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
    """Async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def write_db(db_path: Path):
    """Writable connection to the same DB for staleness tests."""
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    await conn.close()
