"""Integration test fixtures — the real service app, backed by a real,
in-process DB Gateway.

Unlike tests/unit (which swaps the ledger for InMemoryLedgerStore), these
tests keep the production `LedgerDbClient` in place and redirect its
`GatewayClient` at a real, in-process DB Gateway ASGI app (a fresh temp
SQLite database per test, no network socket, no live-stack dependency) — the
same WP-04 contract-suite pattern used by
tests/unit/test_ledger_store_contract.py (which exercises LedgerDbClient
directly) and tests/integration/gateway_helpers.py at the repo root. Identity
and the platform signer stay mocked here (as in tests/unit) — cross-service
wiring with a live Identity is covered by the root cross-service and e2e
suites, not this per-service integration layer.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from db_gateway_service.app import create_app as create_gateway_app
from db_gateway_service.config import clear_settings_cache as clear_gateway_settings_cache
from db_gateway_service.core.state import init_app_state as init_gateway_state
from db_gateway_service.core.state import reset_app_state as reset_gateway_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from httpx import ASGITransport, AsyncClient
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.jws import JwsAdmissionError, ParsedJws, VerifiedJws, create_jws, parse_jws
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair

from central_bank_service.app import create_app
from central_bank_service.config import clear_settings_cache
from central_bank_service.core.lifespan import lifespan
from central_bank_service.core.state import get_app_state, reset_app_state

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from central_bank_service.services.ledger_db_client import LedgerDbClient

PLATFORM_AGENT_ID = "a-platform-test-id"

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"


class _GatewayAuth(httpx.Auth):
    """Sign fixture requests with the key for their persistence domain."""

    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Any:
        domain = request.url.path.split("/", maxsplit=2)[1]
        action = f"gateway.{domain}.{'read' if request.method == 'GET' else 'write'}"
        yield self._signers[domain].sign_request(request, action=action)


async def _register_platform_agent(agent: PlatformAgent) -> None:
    agent.agent_id = PLATFORM_AGENT_ID


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


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes_raw()
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


def _resource_for_payload(payload: dict[str, Any], agent_id: str) -> str:
    action = payload.get("action")
    if action == "create_account" or {"agent_id", "initial_balance"} <= payload.keys():
        resource = "POST /accounts"
    elif action == "escrow_lock" or {"agent_id", "task_id"} <= payload.keys():
        resource = "POST /escrow/lock"
    elif action == "credit" or "reference" in payload:
        resource = f"POST /accounts/{payload.get('account_id')}/credit"
    elif action == "get_balance":
        resource = f"GET /accounts/{payload.get('account_id') or agent_id}"
    elif action == "get_transactions":
        resource = f"GET /accounts/{payload.get('account_id') or agent_id}/transactions"
    elif action == "escrow_release" or "recipient_account_id" in payload:
        resource = f"POST /escrow/{payload.get('escrow_id')}/release"
    elif action == "escrow_split" or "worker_pct" in payload:
        resource = f"POST /escrow/{payload.get('escrow_id')}/split"
    else:
        resource = "POST /accounts"
    return resource


def make_jws_token(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    payload: dict[str, Any],
    *,
    resource: str | None = None,
) -> str:
    """Create a strict JWS token targeting the route exercised by the test."""
    selected_resource = resource or _resource_for_payload(payload, agent_id)
    if not isinstance(payload.get("action"), str):
        issued_at = int(time.time())
        header = {
            "alg": "EdDSA",
            "typ": "JWT",
            "kid": agent_id,
            "iat": issued_at,
            "exp": issued_at + 60,
            "aud": "central-bank",
            "resource": selected_resource,
        }
        header_segment = base64.urlsafe_b64encode(
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        ).rstrip(b"=")
        payload_segment = base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).rstrip(b"=")
        signing_input = header_segment + b"." + payload_segment
        signature = base64.urlsafe_b64encode(private_key.sign(signing_input)).rstrip(b"=")
        return b".".join((header_segment, payload_segment, signature)).decode("ascii")
    return create_jws(
        payload,
        private_key,
        kid=agent_id,
        audience="central-bank",
        resource=selected_resource,
        token_ttl_seconds=60,
    )


def _decode_jws(token: str) -> VerifiedJws:
    parsed: ParsedJws = parse_jws(token)
    return VerifiedJws(header=parsed.header, payload=parsed.payload)


def _make_delegating_verify_jws(state_ref: Any) -> Any:
    """A verify_jws mock that delegates to platform_agent.validate_certificate,
    mirroring tests/unit/routers/conftest.py's identity-client mock."""

    async def _delegating_verify_jws(token: str) -> VerifiedJws:
        try:
            return state_ref.platform_agent.validate_certificate(token)
        except InvalidSignature:
            raise InvalidSignature from None
        except JwsAdmissionError:
            raise

    return _delegating_verify_jws


def _write_gateway_config(db_path: str) -> str:
    """Write a temp db-gateway config.yaml pointing at an isolated SQLite file."""
    content = f"""
service:
  name: "db-gateway-central-bank-integration-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18030
  log_level: "warning"

logging:
  level: "WARNING"
  directory: "data/logs"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "{_SCHEMA_PATH}"
  busy_timeout_ms: 5000
  journal_mode: "wal"

recovery:
  maintenance_lock_path: "{Path(db_path).parent / "maintenance.lock"}"
  backup_root: "{Path(db_path).parent / "backups"}"

request:
  max_body_size: 1048576
auth:
  audience: "db-gateway"
  max_lifetime_seconds: 300
  clock_skew_seconds: 0
  trusted_callers:
    gateway-identity:
      public_key_path: "{Path(db_path).parent / "gateway-identity.pub"}"
      capabilities:
        - "gateway.identity.read"
        - "gateway.identity.write"
    gateway-central-bank:
      public_key_path: "{Path(db_path).parent / "gateway-central-bank.pub"}"
      capabilities:
        - "gateway.bank.read"
        - "gateway.bank.write"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        return f.name


async def _build_gateway_app(db_path: str) -> tuple[FastAPI, DbWriter]:
    """Build a real, in-process DB Gateway ASGI app against a fresh SQLite file."""
    key_dir = Path(db_path).parent
    generate_keypair("gateway-identity", key_dir)
    generate_keypair("gateway-central-bank", key_dir)
    config_path = _write_gateway_config(db_path)
    reset_gateway_state()
    state = init_gateway_state()
    writer = DbWriter(
        db_path=db_path,
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=_SCHEMA_PATH.read_text(),
    )
    state.db_writer = writer
    state.db_reader = DbReader(db=writer.connection)

    with patch.dict("os.environ", {"CONFIG_PATH": config_path}):
        clear_gateway_settings_cache()
        gateway_app = create_gateway_app()
    # See services/identity/tests/integration/conftest.py for why the gateway's
    # settings cache is deliberately NOT cleared again here.
    Path(config_path).unlink(missing_ok=True)
    return gateway_app, writer


@pytest.fixture
def gateway_db_path(tmp_path: Path) -> str:
    """Path to the SQLite file backing the in-process DB Gateway for this test."""
    return str(tmp_path / "gateway.db")


@pytest.fixture
def platform_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate a platform keypair."""
    return _generate_keypair()


@pytest.fixture
def agent_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an agent keypair."""
    return _generate_keypair()


async def register_identity_agent(gateway_client: AsyncClient, agent_id: str) -> None:
    """Register `agent_id` as a real identity_agents row on the in-process gateway.

    `bank_accounts.account_id` has a FOREIGN KEY on `identity_agents.agent_id`
    in the real schema — every account-owning agent in these tests must exist
    there first, exactly like tests/unit/test_ledger_store_contract.py's
    `_register_agent` helper.
    """
    response = await gateway_client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{agent_id}",
            "registered_at": "2026-03-01T09:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-01T09:00:00Z",
                "agent_id": agent_id,
                "summary": f"{agent_id} registered",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text


@pytest.fixture
async def _gateway(gateway_db_path: str) -> AsyncIterator[FastAPI]:
    """The single in-process DB Gateway ASGI app backing this test — both the
    central-bank app's `state.ledger` and the `gateway_client` fixture share
    this one instance (and its one DbWriter/SQLite connection); building a
    second one against the same file would re-run the (non-idempotent)
    schema DDL against an already-initialized database."""
    gateway_app, gateway_writer = await _build_gateway_app(gateway_db_path)
    yield gateway_app
    gateway_writer.close()


@pytest.fixture
async def gateway_client(_gateway: FastAPI, gateway_db_path: str) -> AsyncIterator[AsyncClient]:
    """A client onto the same in-process DB Gateway app that backs this test's
    central-bank `state.ledger` — used to set up identity_agents rows the FK
    on bank_accounts requires."""
    key_dir = Path(gateway_db_path).parent
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-identity",
            private_key_path=key_dir / "gateway-identity.key",
            audience="db-gateway",
            token_ttl_seconds=300,
        ),
        config_dir=key_dir,
    )
    transport = ASGITransport(app=_gateway)
    async with AsyncClient(
        transport=transport,
        base_url="http://gateway-test",
        auth=_GatewayAuth({"identity": signer}),
    ) as c:
        yield c


@pytest.fixture
async def app(
    tmp_path: Path,
    _gateway: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    """Real central-bank app; its production LedgerDbClient is redirected at a
    real, in-process DB Gateway instead of the InMemoryLedgerStore fake unit
    tests use. Identity/platform-agent verification stays mocked."""
    agent_config_path = _write_agent_config(tmp_path)
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
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
platform:
  agent_config_path: "{agent_config_path}"
request:
  max_body_size: 1048576
db_gateway:
  url: "http://gateway-test"
  timeout_seconds: 10
  signing:
    kid: "gateway-central-bank"
    private_key_path: "gateway-central-bank.key"
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

    test_app = create_app()
    async with lifespan(test_app):
        state = get_app_state()
        assert state.ledger is not None
        await state.platform_agent.close()

        ledger = cast("LedgerDbClient", state.ledger)
        await ledger._gateway._client.aclose()
        ledger._gateway._client = AsyncClient(
            transport=ASGITransport(app=_gateway), base_url="http://gateway-test"
        )

        mock_identity = AsyncMock()
        mock_identity.close = AsyncMock()
        mock_identity.get_agent = AsyncMock(
            return_value={"agent_id": "a-test-agent", "name": "Test Agent"},
        )
        state.identity_client = mock_identity
        mock_platform = MagicMock()
        mock_platform.agent_id = PLATFORM_AGENT_ID
        mock_platform.validate_certificate = MagicMock(side_effect=_decode_jws)
        mock_platform.close = AsyncMock()
        state.platform_agent = mock_platform
        mock_identity.verify_jws = AsyncMock(side_effect=_make_delegating_verify_jws(state))

        yield test_app

    reset_app_state()
    clear_settings_cache()
    if old_config is None:
        os.environ.pop("CONFIG_PATH", None)
    else:
        os.environ["CONFIG_PATH"] = old_config


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
