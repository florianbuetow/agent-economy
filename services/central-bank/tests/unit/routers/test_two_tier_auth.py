"""Two-tier auth rollout (WP-03) — central-bank platform ops.

These tests use REAL service-auth JWS tokens and a REAL ``PlatformAgent`` (not the
payload-decoding mock in ``conftest.py``), because the behavior under test is exactly
local cryptographic verification:

* Platform ops (``credit``, escrow ``release``, escrow ``split``) must verify locally
  via ``PlatformAgent.validate_certificate`` and therefore succeed while the Identity
  service is unreachable.
* Agent ops (``escrow_lock``) must still route verification through Identity and fail
  cleanly (502 envelope) when Identity is down.
* On the platform ops, a malformed payload must beat the 403 authorization error
  (T-033 / GAP-A14 precedence fix).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from service_auth.config import AgentConfig
from service_auth.platform import PlatformAgent
from service_auth.signing import create_jws
from service_clients.identity import IdentityClient

from central_bank_service.app import create_app
from central_bank_service.config import clear_settings_cache
from central_bank_service.core.lifespan import lifespan
from central_bank_service.core.state import get_app_state, reset_app_state
from tests.fakes.in_memory_ledger_store import InMemoryLedgerStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# A localhost port that refuses connections — verification that reaches Identity dies.
DEAD_IDENTITY_URL = "http://127.0.0.1:1"
PLATFORM_ID = "a-platform-real"


def _real_platform_agent() -> tuple[PlatformAgent, Ed25519PrivateKey]:
    """Build a real PlatformAgent with a fresh keypair; return (agent, private_key)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    config = AgentConfig(
        name="platform",
        private_key=private_key,
        public_key=public_key,
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


def _write_config(tmp_path: Any) -> str:
    config_content = f"""
service:
  name: "central-bank"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8002
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
identity:
  base_url: "{DEAD_IDENTITY_URL}"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 1
platform:
  agent_config_path: ""
request:
  max_body_size: 1048576
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return str(config_path)


async def _make_client(
    tmp_path: Any,
    *,
    identity_client: Any,
) -> tuple[AsyncClient, Any, Ed25519PrivateKey, Any]:
    """Start an in-process app with a real platform agent and the given identity client."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path)
    os.environ["CONFIG_PATH"] = config_path

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    manager = lifespan(test_app)
    await manager.__aenter__()

    state = get_app_state()
    state.ledger = InMemoryLedgerStore(db_path=db_path)
    platform_agent, private_key = _real_platform_agent()
    state.platform_agent = platform_agent
    state.identity_client = identity_client

    transport = ASGITransport(app=test_app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, state, private_key, manager


async def _teardown(client: AsyncClient, manager: Any) -> None:
    await client.aclose()
    await manager.__aexit__(None, None, None)
    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


@pytest.fixture
async def identity_down(tmp_path: Any) -> AsyncIterator[tuple[AsyncClient, Any, Ed25519PrivateKey]]:
    """App whose Identity client is a real client pointed at a dead port."""
    identity_client = IdentityClient(
        base_url=DEAD_IDENTITY_URL,
        get_agent_path="/agents",
        verify_jws_path="/agents/verify-jws",
        timeout_seconds=1,
    )
    client, state, private_key, manager = await _make_client(
        tmp_path, identity_client=identity_client
    )
    try:
        yield client, state, private_key
    finally:
        await _teardown(client, manager)


@pytest.fixture
async def identity_up_nonplatform(
    tmp_path: Any,
) -> AsyncIterator[tuple[AsyncClient, Any]]:
    """App whose Identity client verifies a token as a *non-platform* agent.

    Simulates a reachable Identity so the *current* (pre-migration) code exhibits its
    inverted precedence: it authenticates via Identity, sees a non-platform signer, and
    raises 403 before validating the payload.
    """

    async def _verify_jws(_token: str) -> dict[str, Any]:
        return {"valid": True, "agent_id": "a-eve", "payload": {}}

    identity_client = AsyncMock()
    identity_client.verify_jws = AsyncMock(side_effect=_verify_jws)
    identity_client.close = AsyncMock()
    client, _state, _private_key, manager = await _make_client(
        tmp_path, identity_client=identity_client
    )
    try:
        yield client, _state
    finally:
        await _teardown(client, manager)


@pytest.mark.unit
async def test_platform_credit_succeeds_when_identity_down(identity_down: Any) -> None:
    """A platform-signed credit succeeds via local verification while Identity is down."""
    client, state, private_key = identity_down
    await state.ledger.create_account("a-worker", 0)

    token = create_jws(
        {"action": "credit", "account_id": "a-worker", "amount": 50, "reference": "r-1"},
        private_key,
        kid=PLATFORM_ID,
    )
    resp = await client.post("/accounts/a-worker/credit", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["balance_after"] == 50


@pytest.mark.unit
async def test_platform_release_succeeds_when_identity_down(identity_down: Any) -> None:
    """A platform-signed escrow release succeeds via local verification while Identity is down."""
    client, state, private_key = identity_down
    await state.ledger.create_account("a-payer", 100)
    await state.ledger.create_account("a-worker", 0)
    locked = await state.ledger.escrow_lock("a-payer", 40, "t-1")
    escrow_id = locked["escrow_id"]

    token = create_jws(
        {"action": "escrow_release", "escrow_id": escrow_id, "recipient_account_id": "a-worker"},
        private_key,
        kid=PLATFORM_ID,
    )
    resp = await client.post(f"/escrow/{escrow_id}/release", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["status"] == "released"
    assert resp.json()["amount"] == 40


@pytest.mark.unit
async def test_platform_split_succeeds_when_identity_down(identity_down: Any) -> None:
    """A platform-signed escrow split succeeds via local verification while Identity is down."""
    client, state, private_key = identity_down
    await state.ledger.create_account("a-poster", 100)
    await state.ledger.create_account("a-worker", 0)
    locked = await state.ledger.escrow_lock("a-poster", 100, "t-2")
    escrow_id = locked["escrow_id"]

    token = create_jws(
        {
            "action": "escrow_split",
            "escrow_id": escrow_id,
            "worker_account_id": "a-worker",
            "worker_pct": 40,
            "poster_account_id": "a-poster",
        },
        private_key,
        kid=PLATFORM_ID,
    )
    resp = await client.post(f"/escrow/{escrow_id}/split", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["worker_amount"] == 40
    assert resp.json()["poster_amount"] == 60


@pytest.mark.unit
async def test_agent_escrow_lock_fails_cleanly_when_identity_down(identity_down: Any) -> None:
    """An agent op still routes via Identity and fails cleanly (502) when Identity is down."""
    client, state, _private_key = identity_down
    await state.ledger.create_account("a-alice", 100)

    agent_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {"action": "escrow_lock", "agent_id": "a-alice", "amount": 10, "task_id": "t-9"},
        agent_key,
        kid="a-alice",
    )
    resp = await client.post("/escrow/lock", json={"token": token})

    assert resp.status_code == 502
    assert resp.json()["error"] == "identity_service_unavailable"


@pytest.mark.unit
async def test_credit_malformed_payload_beats_forbidden(identity_up_nonplatform: Any) -> None:
    """T-033: a malformed credit payload returns the payload error, not 403, even for a
    non-platform signer."""
    client, state = identity_up_nonplatform
    await state.ledger.create_account("a-worker", 0)

    non_platform_key = Ed25519PrivateKey.generate()
    # "reference" missing → malformed payload.
    token = create_jws(
        {"action": "credit", "account_id": "a-worker", "amount": 10},
        non_platform_key,
        kid="a-eve",
    )
    resp = await client.post("/accounts/a-worker/credit", json={"token": token})

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_payload"


@pytest.mark.unit
async def test_credit_wellformed_nonplatform_is_forbidden(identity_down: Any) -> None:
    """A well-formed but non-platform-signed credit is rejected with 403 (local verify)."""
    client, state, _private_key = identity_down
    await state.ledger.create_account("a-worker", 0)

    non_platform_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {"action": "credit", "account_id": "a-worker", "amount": 10, "reference": "r-2"},
        non_platform_key,
        kid="a-eve",
    )
    resp = await client.post("/accounts/a-worker/credit", json={"token": token})

    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"
