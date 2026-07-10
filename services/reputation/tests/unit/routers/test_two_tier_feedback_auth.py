"""Two-tier auth rollout (WP-03) — reputation feedback.

Platform-signed (``force_visible``) feedback must verify locally via the lib
``PlatformAgent`` and therefore succeed while the Identity service is unreachable, while
ordinary agent feedback still routes verification through Identity and fails cleanly.

Uses REAL service-auth tokens and a REAL ``PlatformAgent`` (no payload-decode mock) and a
REAL ``IdentityClient`` pointed at a dead port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from service_auth.config import AgentConfig
from service_auth.platform import PlatformAgent
from service_auth.signing import create_jws
from service_clients.identity import IdentityClient

from reputation_service.app import create_app
from reputation_service.core.state import get_app_state
from tests.fakes.sqlite_feedback_store import SqliteFeedbackStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

DEAD_IDENTITY_URL = "http://127.0.0.1:1"
PLATFORM_ID = "a-platform-real"


def _real_platform_agent() -> tuple[PlatformAgent, Ed25519PrivateKey]:
    private_key = Ed25519PrivateKey.generate()
    config = AgentConfig(
        name="platform",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url=DEAD_IDENTITY_URL,
        bank_url="http://localhost:8002",
        task_board_url="http://localhost:8003",
        reputation_url="http://localhost:8004",
        court_url="http://localhost:8005",
        token_ttl_seconds=None,
    )
    agent = PlatformAgent(config)
    agent.agent_id = PLATFORM_ID
    return agent, private_key


@pytest.fixture
async def platform_client(
    tmp_path: Any,
) -> AsyncIterator[tuple[AsyncClient, Any, Ed25519PrivateKey]]:
    """In-process app with a real platform agent and a dead Identity client."""
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        state = get_app_state()
        state.feedback_store = SqliteFeedbackStore(db_path=str(tmp_path / "rep.db"))
        agent, private_key = _real_platform_agent()
        state.platform_agent = agent
        state.identity_client = IdentityClient(
            base_url=DEAD_IDENTITY_URL,
            get_agent_path="/agents",
            verify_jws_path="/agents/verify-jws",
            timeout_seconds=1,
        )
        yield client, state, private_key


@pytest.mark.unit
async def test_platform_feedback_succeeds_when_identity_down(platform_client: Any) -> None:
    """Platform-signed feedback verifies locally and is force-visible while Identity is down."""
    client, _state, private_key = platform_client

    payload = {
        "action": "submit_feedback",
        "task_id": "task-1",
        "from_agent_id": PLATFORM_ID,
        "to_agent_id": "a-worker",
        "category": "delivery_quality",
        "rating": "satisfied",
        "comment": "Court ruling feedback",
    }
    token = create_jws(payload, private_key, kid=PLATFORM_ID)
    resp = await client.post("/feedback", json={"token": token})

    assert resp.status_code == 201
    body = resp.json()
    assert body["from_agent_id"] == PLATFORM_ID
    assert body["to_agent_id"] == "a-worker"
    assert body["visible"] is True


@pytest.mark.unit
async def test_agent_feedback_fails_cleanly_when_identity_down(platform_client: Any) -> None:
    """Ordinary agent feedback still routes via Identity and fails cleanly (502) when down."""
    client, _state, _private_key = platform_client

    agent_key = Ed25519PrivateKey.generate()
    payload = {
        "action": "submit_feedback",
        "task_id": "task-2",
        "from_agent_id": "a-alice",
        "to_agent_id": "a-bob",
        "category": "delivery_quality",
        "rating": "satisfied",
        "comment": "nice",
    }
    token = create_jws(payload, agent_key, kid="a-alice")
    resp = await client.post("/feedback", json={"token": token})

    assert resp.status_code == 502
    assert resp.json()["error"] == "identity_service_unavailable"
