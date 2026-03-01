"""Integration test fixtures."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from reputation_service.app import create_app
from reputation_service.config import clear_settings_cache
from reputation_service.core.state import reset_app_state

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _isolate_test(tmp_path: Path) -> Iterator[None]:
    """Isolate each test with its own temp database and config."""
    db_path = str(tmp_path / "test.db")
    config_content = f"""\
service:
  name: "reputation"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8004
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
request:
  max_body_size: 1048576
database:
  path: "{db_path}"
feedback:
  reveal_timeout_seconds: 86400
  max_comment_length: 256
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)

    old_config = os.environ.get("CONFIG_PATH")
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    reset_app_state()

    yield

    if old_config is None:
        os.environ.pop("CONFIG_PATH", None)
    else:
        os.environ["CONFIG_PATH"] = old_config
    clear_settings_cache()
    reset_app_state()


@pytest.fixture
def app() -> FastAPI:
    """Create the FastAPI application."""
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP test client with lifespan management."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
