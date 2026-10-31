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
from service_auth.jws import JwsProtectedHeader, TokenPolicy, VerifiedJws
from service_auth.platform import PlatformAgent
from service_auth.signing import create_jws, generate_keypair
from service_clients.identity import IdentityClient

from central_bank_service.app import create_app
from central_bank_service.config import clear_settings_cache
from central_bank_service.core.lifespan import lifespan
from central_bank_service.core.state import get_app_state, reset_app_state
from tests.fakes.in_memory_ledger_store import InMemoryLedgerStore

from .conftest import make_operation_id

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

# A localhost port that refuses connections — verification that reaches Identity dies.
DEAD_IDENTITY_URL = "http://127.0.0.1:1"
PLATFORM_ID = "a-platform-real"


async def _register_fixture_platform(agent: PlatformAgent) -> None:
    """Register a disposable platform identity without network I/O."""
    agent.agent_id = PLATFORM_ID


def _write_agent_config(tmp_path: Path) -> Path:
    """Provision the platform pair required by load-only startup."""
    keys_dir = tmp_path / "keys"
    generate_keypair("platform", keys_dir)
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text('agents:\n  platform:\n    name: "Platform"\n')
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        f"""\
platform:
  identity_url: "{DEAD_IDENTITY_URL}"
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
        token_ttl_seconds=60,
        token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0),
    )
    agent = PlatformAgent(config)
    agent.agent_id = PLATFORM_ID
    return agent, private_key


def _write_config(tmp_path: Any) -> str:
    agent_config_path = _write_agent_config(tmp_path)
    generate_keypair("gateway-central-bank", tmp_path)
    config_content = f"""
service:
  name: "central-bank"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8002
  log_level: "info"
telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
logging:
  level: "WARNING"
  directory: "{tmp_path / "logs" / "central-bank"}"
identity:
  base_url: "{DEAD_IDENTITY_URL}"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 1
platform:
  agent_config_path: "{agent_config_path}"
request:
  max_body_size: 1048576
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
  signing:
    kid: "gateway-central-bank"
    private_key_path: "gateway-central-bank.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
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
async def identity_down(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, Any, Ed25519PrivateKey]]:
    """App whose Identity client is a real client pointed at a dead port."""
    monkeypatch.setattr(PlatformAgent, "register", _register_fixture_platform)
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
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, Any]]:
    """App whose Identity client verifies a token as a *non-platform* agent.

    Simulates a reachable Identity so the *current* (pre-migration) code exhibits its
    inverted precedence: it authenticates via Identity, sees a non-platform signer, and
    raises 403 before validating the payload.
    """

    monkeypatch.setattr(PlatformAgent, "register", _register_fixture_platform)

    async def _verify_jws(_token: str) -> VerifiedJws:
        return VerifiedJws(
            header=JwsProtectedHeader(
                alg="EdDSA",
                typ="JWT",
                kid="a-eve",
                iat=1,
                exp=2,
                aud="central-bank",
                resource="POST /accounts/a-worker/credit",
            ),
            payload={"action": "credit", "account_id": "a-worker", "amount": 10},
        )

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
    await state.ledger.create_account("a-worker", 0, None)

    operation_id = make_operation_id()
    token = create_jws(
        {
            "action": "credit",
            "account_id": "a-worker",
            "amount": 50,
            "reference": "r-1",
            "operation_id": operation_id,
        },
        private_key,
        kid=PLATFORM_ID,
        audience="central-bank",
        resource="POST /accounts/a-worker/credit",
        token_ttl_seconds=60,
    )
    resp = await client.post("/accounts/a-worker/credit", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["receipt"]["operation_id"] == operation_id
    assert resp.json()["receipt"]["result"]["balance_after"] == 50


@pytest.mark.unit
async def test_platform_release_succeeds_when_identity_down(identity_down: Any) -> None:
    """A platform-signed escrow release succeeds via local verification while Identity is down."""
    client, state, private_key = identity_down
    await state.ledger.create_account("a-payer", 100, make_operation_id())
    await state.ledger.create_account("a-worker", 0, None)
    locked = await state.ledger.escrow_lock("a-payer", 40, "t-1", make_operation_id())
    escrow_id = locked["receipt"]["escrow_id"]

    operation_id = make_operation_id()
    token = create_jws(
        {
            "action": "escrow_release",
            "escrow_id": escrow_id,
            "recipient_account_id": "a-worker",
            "operation_id": operation_id,
            "source_reference": "task:t-1:approved",
        },
        private_key,
        kid=PLATFORM_ID,
        audience="central-bank",
        resource=f"POST /escrow/{escrow_id}/release",
        token_ttl_seconds=60,
    )
    resp = await client.post(f"/escrow/{escrow_id}/release", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["receipt"]["operation_id"] == operation_id
    assert resp.json()["receipt"]["legs"][0]["amount"] == 40
    assert resp.json()["current_escrow"]["status"] == "released"


@pytest.mark.unit
async def test_platform_split_succeeds_when_identity_down(identity_down: Any) -> None:
    """A platform-signed escrow split succeeds via local verification while Identity is down."""
    client, state, private_key = identity_down
    await state.ledger.create_account("a-poster", 100, make_operation_id())
    await state.ledger.create_account("a-worker", 0, None)
    locked = await state.ledger.escrow_lock("a-poster", 100, "t-2", make_operation_id())
    escrow_id = locked["receipt"]["escrow_id"]

    operation_id = make_operation_id()
    token = create_jws(
        {
            "action": "escrow_split",
            "escrow_id": escrow_id,
            "worker_account_id": "a-worker",
            "worker_pct": 40,
            "poster_account_id": "a-poster",
            "operation_id": operation_id,
            "source_reference": "task:t-2:ruling:r-one",
        },
        private_key,
        kid=PLATFORM_ID,
        audience="central-bank",
        resource=f"POST /escrow/{escrow_id}/split",
        token_ttl_seconds=60,
    )
    resp = await client.post(f"/escrow/{escrow_id}/split", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["receipt"]["operation_id"] == operation_id
    assert resp.json()["receipt"]["legs"][0]["amount"] == 40
    assert resp.json()["receipt"]["legs"][1]["amount"] == 60


@pytest.mark.unit
async def test_agent_escrow_lock_fails_cleanly_when_identity_down(identity_down: Any) -> None:
    """An agent op still routes via Identity and fails cleanly (502) when Identity is down."""
    client, state, _private_key = identity_down
    await state.ledger.create_account("a-alice", 100, make_operation_id())

    agent_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {
            "action": "escrow_lock",
            "agent_id": "a-alice",
            "amount": 10,
            "task_id": "t-9",
            "operation_id": make_operation_id(),
        },
        agent_key,
        kid="a-alice",
        audience="central-bank",
        resource="POST /escrow/lock",
        token_ttl_seconds=60,
    )
    resp = await client.post("/escrow/lock", json={"token": token})

    assert resp.status_code == 502
    assert resp.json()["error"] == "identity_service_unavailable"


@pytest.mark.unit
async def test_credit_invalid_signature_precedes_domain_payload(
    identity_up_nonplatform: Any,
) -> None:
    """A non-platform signature is forbidden before domain-field validation."""
    client, state = identity_up_nonplatform
    await state.ledger.create_account("a-worker", 0, None)

    non_platform_key = Ed25519PrivateKey.generate()
    # "reference" missing → malformed payload.
    token = create_jws(
        {"action": "credit", "account_id": "a-worker", "amount": 10},
        non_platform_key,
        kid="a-eve",
        audience="central-bank",
        resource="POST /accounts/a-worker/credit",
        token_ttl_seconds=60,
    )
    resp = await client.post("/accounts/a-worker/credit", json={"token": token})

    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


@pytest.mark.unit
async def test_credit_wellformed_nonplatform_is_forbidden(identity_down: Any) -> None:
    """A well-formed but non-platform-signed credit is rejected with 403 (local verify)."""
    client, state, _private_key = identity_down
    await state.ledger.create_account("a-worker", 0, None)

    non_platform_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {
            "action": "credit",
            "account_id": "a-worker",
            "amount": 10,
            "reference": "r-2",
            "operation_id": make_operation_id(),
        },
        non_platform_key,
        kid="a-eve",
        audience="central-bank",
        resource="POST /accounts/a-worker/credit",
        token_ttl_seconds=60,
    )
    resp = await client.post("/accounts/a-worker/credit", json={"token": token})

    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"
