"""Integration test fixtures."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair

from reputation_service.app import create_app
from reputation_service.config import clear_settings_cache
from reputation_service.core.state import get_app_state, reset_app_state
from tests.fakes.fake_task_board import default_task_board
from tests.fakes.sqlite_feedback_store import SqliteFeedbackStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from fastapi import FastAPI


async def _register_platform_agent(agent: PlatformAgent) -> None:
    agent.agent_id = "a-platform-test-id"


def _write_agent_config(tmp_path: Path) -> Path:
    keys_dir = tmp_path / "keys"
    generate_keypair("platform", keys_dir)
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text('agents:\n  platform:\n    name: "Platform"\n')
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        f"""\
platform:
  identity_url: "http://localhost:8001"
  bank_url: "http://localhost:8002"
  task_board_url: "http://localhost:8003"
  reputation_url: "http://localhost:8004"
  court_url: "http://localhost:8005"
data:
  keys_dir: "{keys_dir}"
  roster_path: "{roster_path}"
signing:
  token_ttl_seconds: 60
  max_lifetime_seconds: 60
  clock_skew_seconds: 0
""",
    )
    return config_path


@pytest.fixture(autouse=True)
def _isolate_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Isolate each test with its own temp database and config.

    Yields the temp sqlite path so `client()` can back the test-only fake
    feedback store with it — reputation reads no local database.path in
    production (GAP-C8/exception #15), so it no longer flows through Settings.
    """
    db_path = str(tmp_path / "test.db")
    generate_keypair("gateway-reputation", tmp_path)
    agent_config_path = _write_agent_config(tmp_path)
    config_content = f"""\
service:
  name: "reputation"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8004
  log_level: "info"
telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
logging:
  level: "INFO"
  directory: "{tmp_path / "logs" / "reputation"}"
platform:
  agent_config_path: "{agent_config_path}"
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
task_board:
  base_url: "http://localhost:8003"
  timeout_seconds: 10
request:
  max_body_size: 1048576
feedback:
  reveal_timeout_seconds: 86400
  max_comment_length: 256
  reveal_sweep_interval_seconds: 60
quality:
  operator_name: "operator"
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
  signing:
    kid: "gateway-reputation"
    private_key_path: "gateway-reputation.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)

    old_config = os.environ.get("CONFIG_PATH")
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    reset_app_state()
    monkeypatch.setattr(PlatformAgent, "register", _register_platform_agent)

    yield db_path

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
async def client(app: FastAPI, _isolate_test: str) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP test client with lifespan management."""
    async with app.router.lifespan_context(app):
        state = get_app_state()
        state.feedback_store = SqliteFeedbackStore(db_path=_isolate_test)
        state.task_board_client = default_task_board()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
