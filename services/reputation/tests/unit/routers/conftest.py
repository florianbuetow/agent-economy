"""Router test fixtures."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from cryptography.exceptions import InvalidSignature
from httpx import ASGITransport, AsyncClient
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair
from service_commons.exceptions import ServiceError

from reputation_service.app import create_app
from reputation_service.config import clear_settings_cache
from reputation_service.core.state import get_app_state, reset_app_state
from tests.fakes.sqlite_feedback_store import SqliteFeedbackStore
from tests.helpers import make_jws_token, make_mock_platform_agent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from fastapi import FastAPI
    from service_auth.jws import VerifiedJws


ALICE_ID = "a-alice-uuid"
BOB_ID = "a-bob-uuid"
PLATFORM_AGENT_ID = "a-platform-test-id"


async def _register_platform_agent(agent: PlatformAgent) -> None:
    """Register a fixture platform identity without an external Identity service."""
    agent.agent_id = PLATFORM_AGENT_ID


def _write_agent_config(tmp_path: Path) -> Path:
    """Provision a disposable platform identity for load-only startup."""
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
logging:
  level: "INFO"
  directory: "data/logs"
platform:
  agent_config_path: "{agent_config_path}"
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
request:
  max_body_size: 1048576
feedback:
  reveal_timeout_seconds: 86400
  max_comment_length: 256
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
    """Create an async HTTP test client with lifespan management and mock identity."""
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c,
    ):
        state = get_app_state()
        state.feedback_store = SqliteFeedbackStore(db_path=_isolate_test)
        await state.platform_agent.close()
        inject_mock_identity()
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


def _make_delegating_verify_jws(state_ref: object) -> Any:
    """Create a verify_jws mock that delegates to platform_agent.validate_certificate.

    This ensures tests that override validate_certificate (e.g. with InvalidSignature)
    will see the same behavior when verification goes through identity_client.verify_jws.

    Signature-related errors (InvalidSignature, ValueError) are mapped to valid=False.
    Connectivity errors (ConnectionError, TimeoutError, etc.) are raised as ServiceError(502).
    """

    async def _delegating_verify_jws(token: str) -> VerifiedJws:
        try:
            return state_ref.platform_agent.validate_certificate(token)
        except InvalidSignature:
            raise
        except Exception as exc:
            raise ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

    return _delegating_verify_jws


def inject_mock_identity(
    verify_response: dict[str, object] | None = None,
) -> None:
    """Inject a mock IdentityClient into AppState."""
    state = get_app_state()
    if verify_response is None:
        verify_response = _mock_verify_ok(ALICE_ID, _feedback_payload())

    payload = verify_response.get("payload")
    side_effect: Exception | None = None
    valid = verify_response.get("valid")
    if isinstance(valid, bool) and not valid:
        payload = None
        side_effect = InvalidSignature()
    elif isinstance(payload, dict) and (
        not isinstance(payload.get("action"), str) or not payload.get("action")
    ):
        payload = None
        side_effect = ServiceError(
            "invalid_payload",
            "Identity rejected malformed payload action",
            400,
            {},
        )
    elif valid is not True or not isinstance(payload, dict):
        payload = None
        side_effect = ServiceError(
            "identity_service_unavailable",
            "Identity verification response is malformed",
            502,
            {},
        )

    state.platform_agent = make_mock_platform_agent(
        verify_payload=payload if isinstance(payload, dict) else None,
        verify_side_effect=side_effect,
    )

    # Set up mock identity_client with delegating verify_jws
    mock_identity = AsyncMock()
    mock_identity.close = AsyncMock()
    mock_identity.verify_jws = AsyncMock(
        side_effect=_make_delegating_verify_jws(state),
    )
    state.identity_client = mock_identity
