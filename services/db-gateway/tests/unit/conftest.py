"""Unit test fixtures."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from db_gateway_service.config import clear_settings_cache
from db_gateway_service.core.state import init_app_state, reset_app_state
from db_gateway_service.services.db_writer import DbWriter

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """Reset app state and settings cache between tests."""
    yield
    reset_app_state()
    clear_settings_cache()


@pytest.fixture
def db_writer(initialized_db: str) -> Iterator[DbWriter]:
    """Create a DbWriter with an initialized test database."""
    writer = DbWriter(
        db_path=initialized_db,
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=None,
    )
    yield writer
    writer.close()


@pytest.fixture
def app_with_writer(db_writer: DbWriter) -> Iterator[TestClient]:
    """Create a FastAPI test client with a real DbWriter."""
    state = init_app_state()
    state.db_writer = db_writer

    from db_gateway_service.app import create_app

    # Override settings to avoid needing config.yaml
    os.environ["CONFIG_PATH"] = _create_test_config(db_writer._db_path)
    clear_settings_cache()

    app = create_app()
    with TestClient(app) as client:
        # Re-inject the db_writer since lifespan creates a new one
        state = init_app_state()
        state.db_writer = db_writer
        yield client

    if "CONFIG_PATH" in os.environ:
        config_path = os.environ.pop("CONFIG_PATH")
        with contextlib.suppress(OSError):
            Path(config_path).unlink()


def _create_test_config(db_path: str) -> str:
    """Write a temporary config.yaml for testing."""
    config_content = f"""
service:
  name: "db-gateway-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18006
  log_level: "warning"

logging:
  level: "WARNING"
  directory: "data/logs"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "../../docs/specifications/schema.sql"
  busy_timeout_ms: 5000
  journal_mode: "wal"

request:
  max_body_size: 1048576
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        return f.name
