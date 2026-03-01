"""Router test fixtures."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from reputation_service.app import create_app
from reputation_service.config import clear_settings_cache
from reputation_service.core.state import get_app_state, reset_app_state
from tests.helpers import make_jws_token, make_mock_identity_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from fastapi import FastAPI


ALICE_ID = "a-alice-uuid"
BOB_ID = "a-bob-uuid"


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
  directory: "data/logs"
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
    """Create an async HTTP test client with lifespan management and mock identity."""
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c,
    ):
        yield c


def _feedback_payload(**overrides: object) -> dict[str, object]:
    """Return a valid JWS payload for feedback."""
    base: dict[str, object] = {
        "action": "submit_feedback",
        "task_id": "task-1",
        "from_agent_id": ALICE_ID,
        "to_agent_id": BOB_ID,
        "category": "delivery_quality",
        "rating": "satisfied",
        "comment": "Good work",
    }
    base.update(overrides)
    return base


def _token_body(
    payload: dict[str, object] | None = None,
    kid: str = ALICE_ID,
) -> dict[str, object]:
    """Wrap a payload in a JWS token body for POST /feedback."""
    if payload is None:
        payload = _feedback_payload()
    return {"token": make_jws_token(payload, kid=kid)}


def _mock_verify_ok(
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Return a successful verify_jws response."""
    return {"valid": True, "agent_id": agent_id, "payload": payload}


def inject_mock_identity(
    verify_response: dict[str, object] | None = None,
) -> None:
    """Inject a mock IdentityClient into AppState."""
    state = get_app_state()
    if verify_response is None:
        verify_response = _mock_verify_ok(ALICE_ID, _feedback_payload())
    state.identity_client = make_mock_identity_client(verify_response=verify_response)
