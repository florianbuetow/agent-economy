"""Integration test fixtures — the real service app, backed by a real,
live, in-process DB Gateway.

Task-board's production `TaskDbClient` uses a synchronous `httpx.Client`
(client consolidation to the shared async `GatewayClient` was deferred —
see plan §5 WP-04 deviation notes), so it cannot be redirected at an
in-process ASGI app via `httpx.ASGITransport` the way identity's
`AgentDbClient` and central-bank's `LedgerDbClient` are (that transport only
implements the async request interface). Instead, this suite boots a real
DB Gateway ASGI app under `uvicorn` on a random local port, backed by a
fresh temp SQLite file per test — a genuine live server, but fully isolated
and torn down after each test, no dependency on the shared dev stack.

Central Bank, Identity, and the platform agent stay mocked here (as in
tests/unit) — cross-service wiring with live services is covered by the
root cross-service and e2e suites, not this per-service integration layer.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import socket
import tempfile
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import uvicorn
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from db_gateway_service.app import create_app as create_gateway_app
from db_gateway_service.config import clear_settings_cache as clear_gateway_settings_cache
from db_gateway_service.core.state import init_app_state as init_gateway_state
from db_gateway_service.core.state import reset_app_state as reset_gateway_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from httpx import ASGITransport, AsyncClient
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.jws import JwsAdmissionError, VerifiedJws
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair as provision_keypair
from service_commons.exceptions import ServiceError

from task_board_service.app import create_app
from task_board_service.config import clear_settings_cache, get_settings
from task_board_service.core.lifespan import lifespan
from task_board_service.core.state import get_app_state, reset_app_state
from task_board_service.services.financial_effect_recovery import FinancialEffectRecovery
from tests.helpers import make_jws_token as make_strict_jws_token
from tests.helpers import verify_compact_jws

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

PLATFORM_AGENT_ID = "a-platform-test-id"

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"


class _GatewayAuth(httpx.Auth):
    """Sign fixture requests with distinct domain capability keys."""

    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Any:
        route_domain = request.url.path.split("/", maxsplit=2)[1]
        action_domain = "board" if route_domain == "board" else route_domain
        action = f"gateway.{action_domain}.{'read' if request.method == 'GET' else 'write'}"
        yield self._signers[route_domain].sign_request(request, action=action)


def _write_platform_agent_config(tmp_path: Path) -> Path:
    keys_dir = tmp_path / "agent-keys"
    provision_keypair("platform", keys_dir)
    provision_keypair("operator", keys_dir)
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "platform": {"name": "Platform", "type": "platform"},
                    "operator": {"name": "UI Operator", "type": "operator"},
                }
            }
        )
    )
    agent_config_path = tmp_path / "agents.yaml"
    agent_config_path.write_text(
        yaml.safe_dump(
            {
                "platform": {
                    "identity_url": "http://localhost:8001",
                    "bank_url": "http://localhost:8002",
                    "task_board_url": "http://localhost:8003",
                    "reputation_url": "http://localhost:8004",
                    "court_url": "http://localhost:8005",
                },
                "data": {
                    "keys_dir": str(keys_dir),
                    "roster_path": str(roster_path),
                },
                "signing": {
                    "token_ttl_seconds": 300,
                    "max_lifetime_seconds": 300,
                    "clock_skew_seconds": 0,
                },
            }
        )
    )
    return agent_config_path


async def _register_test_platform(agent: PlatformAgent) -> dict[str, str]:
    agent.agent_id = PLATFORM_AGENT_ID
    return {"agent_id": PLATFORM_AGENT_ID}


def _free_port() -> int:
    """Bind to port 0 to get an OS-assigned free local port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


def make_jws_token(private_key: Ed25519PrivateKey, agent_id: str, payload: dict[str, Any]) -> str:
    """Create a strict Task Board token signed by the given private key."""
    return make_strict_jws_token(private_key, agent_id, payload)


def _write_gateway_config(db_path: str) -> str:
    """Write a temp db-gateway config.yaml pointing at an isolated SQLite file."""
    content = f"""
service:
  name: "db-gateway-task-board-integration-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18031
  log_level: "warning"

logging:
  level: "WARNING"
  directory: "{Path(db_path).parent / "logs" / "db-gateway"}"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "{_SCHEMA_PATH}"
  busy_timeout_ms: 5000
  journal_mode: "wal"

request:
  max_body_size: 1048576
recovery:
  maintenance_lock_path: "{Path(db_path).parent / "maintenance.lock"}"
  backup_root: "{Path(db_path).parent / "backups"}"
board_authority:
  platform_public_key_path: "{Path(db_path).parent / "agent-keys" / "platform.pub"}"
  deadline_actor_id: "system-task-board-deadline"
board_effect_recovery:
  current_task_board_caller_kid: "gateway-task-board"
  max_attempts_per_delivery: 8
  initial_backoff_seconds: 2
  max_backoff_seconds: 60
  lease_duration_seconds: 45
  evidence_interval_seconds: 60
  gateway_timeout_seconds: 10
  bank_timeout_seconds: 10
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
    gateway-task-board:
      public_key_path: "{Path(db_path).parent / "gateway-task-board.pub"}"
      capabilities:
        - "gateway.board.read"
        - "gateway.board.write"
    gateway-court:
      public_key_path: "{Path(db_path).parent / "gateway-court.pub"}"
      capabilities:
        - "gateway.court.write"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        return f.name


class _LiveGateway:
    """A real DB Gateway ASGI app served by uvicorn on a random local port,
    started/stopped around a single test."""

    def __init__(
        self,
        base_url: str,
        server: uvicorn.Server,
        thread: threading.Thread,
        maintenance_lock_path: Path,
    ) -> None:
        self.base_url = base_url
        self.maintenance_lock_path = maintenance_lock_path
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


async def _start_live_gateway(db_path: str) -> tuple[_LiveGateway, DbWriter]:
    """Build and serve a real DB Gateway ASGI app, on its own thread with its
    own event loop, against a fresh SQLite file.

    A same-loop `asyncio.create_task(server.serve())` would deadlock:
    TaskDbClient makes *synchronous* (blocking) httpx calls from inside an
    async route handler, so a server sharing the test's event loop could
    never get scheduled to answer its own client's request. Running the
    server on a separate OS thread (its own loop) — same trick a real,
    separate service process gets for free — avoids that entirely.
    `check_same_thread=False` on the gateway's sqlite3 connection (GAP-C7
    era) makes sharing the DbWriter across threads safe.
    """
    key_dir = Path(db_path).parent
    provision_keypair("platform", key_dir / "agent-keys")
    provision_keypair("gateway-identity", key_dir)
    provision_keypair("gateway-central-bank", key_dir)
    provision_keypair("gateway-task-board", key_dir)
    provision_keypair("gateway-court", key_dir)
    config_path = _write_gateway_config(db_path)
    reset_gateway_state()
    state = init_gateway_state()
    writer = DbWriter(
        db_path=db_path,
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=_SCHEMA_PATH.read_text(),
        current_task_board_caller_kid="gateway-task-board",  # type: ignore[call-arg]
    )
    state.db_writer = writer
    state.db_reader = DbReader(db=writer.connection)

    with patch.dict("os.environ", {"CONFIG_PATH": config_path}):
        clear_gateway_settings_cache()
        gateway_app = create_gateway_app()
    # See services/identity/tests/integration/conftest.py for why the gateway's
    # settings cache is deliberately NOT cleared again here.
    Path(config_path).unlink(missing_ok=True)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(gateway_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()

    async with httpx.AsyncClient() as probe:
        for _ in range(100):
            if server.started:
                try:
                    resp = await probe.get(f"{base_url}/health", timeout=0.5)
                    if resp.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
            await asyncio.sleep(0.05)
        else:
            msg = "In-process DB Gateway did not become ready in time"
            raise RuntimeError(msg)

    return _LiveGateway(base_url, server, thread, key_dir / "maintenance.lock"), writer


@pytest.fixture
def gateway_db_path(tmp_path: Path) -> str:
    """Path to the SQLite file backing the in-process DB Gateway for this test."""
    return str(tmp_path / "gateway.db")


@pytest.fixture
async def _gateway(gateway_db_path: str) -> AsyncIterator[_LiveGateway]:
    live_gateway, writer = await _start_live_gateway(gateway_db_path)
    yield live_gateway
    live_gateway.stop()
    writer.close()
    reset_gateway_state()


@pytest.fixture
async def gateway_client(
    _gateway: _LiveGateway, gateway_db_path: str
) -> AsyncIterator[httpx.AsyncClient]:
    """A client onto the live DB Gateway backing this test's task-board
    `state.store` — used to set up identity_agents rows the FK on
    board_tasks/bids etc. requires."""
    key_dir = Path(gateway_db_path).parent
    signers = {
        domain: GatewayRequestSigner(
            GatewaySignerConfig(
                kid=kid,
                private_key_path=key_dir / f"{kid}.key",
                audience="db-gateway",
                token_ttl_seconds=300,
            ),
            config_dir=key_dir,
        )
        for domain, kid in {
            "identity": "gateway-identity",
            "bank": "gateway-central-bank",
            "board": "gateway-task-board",
            "court": "gateway-court",
        }.items()
    }
    async with httpx.AsyncClient(
        base_url=_gateway.base_url,
        auth=_GatewayAuth(signers),
    ) as c:
        yield c


async def register_identity_agent(gateway_client: httpx.AsyncClient, agent_id: str) -> None:
    """Register `agent_id` as a real identity_agents row on the live gateway."""
    response = await gateway_client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{uuid.uuid4()}",
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
def platform_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate a platform keypair."""
    return _generate_keypair()


@pytest.fixture
def poster_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate a poster keypair."""
    return _generate_keypair()


@pytest.fixture
def worker_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate a worker keypair."""
    return _generate_keypair()


@pytest.fixture
async def app(
    tmp_path: Path,
    _gateway: _LiveGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    """Real task-board app; its production TaskDbClient talks to a real, live,
    fully-isolated in-process DB Gateway over HTTP. Unit fixtures exercise the
    same persistence without HTTP. Central Bank/Identity/platform-agent stay mocked."""
    db_path = tmp_path / "test.db"
    agent_config_path = _write_platform_agent_config(tmp_path)
    config_content = f"""
service:
  name: "task-board"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8003
  log_level: "info"
logging:
  level: "WARNING"
  directory: "{tmp_path / "logs" / "task-board"}"
database:
  path: "{db_path}"
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/release"
  escrow_split_path: "/escrow/split"
  timeout_seconds: 10
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
  agent_config_path: "{agent_config_path}"
asset_upload:
  stream_chunk_bytes: 262144
  max_multipart_overhead_bytes: 1048576
  max_header_count: 8
  max_header_size: 4224
  max_boundary_bytes: 70
  max_display_filename_utf8_bytes: 255
request:
  max_body_size: 1048576
recovery:
  maintenance_lock_path: "{tmp_path / "maintenance.lock"}"
posting_recovery:
  interval_seconds: 2
  batch_size: 100
  diagnostic_limit: 100
financial_effect_recovery:
  interval_seconds: 2.0
  max_delivery_attempts_per_cycle: 10
limits:
  max_file_size: 10485760
  max_assets_per_task: 20
db_gateway:
  url: "{_gateway.base_url}"
  timeout_seconds: 10
  signing:
    kid: "gateway-task-board"
    private_key_path: "gateway-task-board.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
deadline_evaluation:
  evaluation_interval_seconds: 3600
  actor_id: "system-task-board-deadline"
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    old_config = os.environ.get("CONFIG_PATH")
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()
    monkeypatch.setattr(PlatformAgent, "register", _register_test_platform)

    test_app = create_app()
    async with lifespan(test_app):
        state = get_app_state()
        assert state.financial_effect_recovery_task is not None
        state.financial_effect_recovery_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.financial_effect_recovery_task

        mock_platform = MagicMock()
        mock_platform.agent_id = PLATFORM_AGENT_ID
        mock_platform.validate_certificate = MagicMock(side_effect=verify_compact_jws)
        mock_platform.file_claim = AsyncMock(
            return_value={"dispute_id": "disp-1", "status": "rebuttal_pending"}
        )
        mock_platform.close = AsyncMock()
        state.platform_agent = mock_platform

        mock_bank = AsyncMock()
        mock_bank.close = AsyncMock()
        mock_bank.escrow_lock = AsyncMock(
            return_value={"escrow_id": f"esc-{uuid.uuid4()}", "status": "locked"}
        )
        mock_bank.escrow_release = AsyncMock(return_value={"status": "released"})
        mock_bank.escrow_split = AsyncMock(return_value={"status": "split"})
        state.central_bank_client = mock_bank

        mock_identity = AsyncMock()
        mock_identity.close = AsyncMock()
        mock_identity.verify_jws = AsyncMock(side_effect=_make_delegating_verify_jws(state))
        state.identity_client = mock_identity

        if state.task_manager is not None:
            state.task_manager._central_bank_client = mock_bank
        if state.token_validator is not None:
            state.token_validator._platform_agent = mock_platform
            state.token_validator._identity_client = mock_identity
        if state.escrow_coordinator is not None:
            state.escrow_coordinator._central_bank_client = mock_bank

        assert state.store is not None
        settings = get_settings()
        replacement_recovery = FinancialEffectRecovery(
            store=state.store,
            bank=mock_bank,
            worker_id=None,
            max_delivery_attempts_per_cycle=(
                settings.financial_effect_recovery.max_delivery_attempts_per_cycle
            ),
        )
        state.financial_effect_recovery = replacement_recovery
        replacement_recovery_task = asyncio.create_task(
            replacement_recovery.run_periodic(
                interval_seconds=settings.financial_effect_recovery.interval_seconds
            )
        )
        state.financial_effect_recovery_task = replacement_recovery_task
        try:
            yield test_app
        finally:
            replacement_recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await replacement_recovery_task

    reset_app_state()
    clear_settings_cache()
    if old_config is None:
        os.environ.pop("CONFIG_PATH", None)
    else:
        os.environ["CONFIG_PATH"] = old_config


def _decode_jws_payload(token: str) -> dict[str, Any]:
    """Decode the base64url payload from a compact JWS token, unverified — the
    mock platform agent stands in for real signature verification here."""
    parts = token.split(".")
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _make_delegating_verify_jws(state_ref: Any) -> Any:
    """A verify_jws mock that delegates to platform_agent.validate_certificate,
    mirroring tests/unit/routers/conftest.py's identity-client mock."""

    async def _delegating_verify_jws(token: str) -> VerifiedJws:
        try:
            verified = state_ref.platform_agent.validate_certificate(token)
            action = verified.payload.get("action")
            if not isinstance(action, str) or not action:
                raise ServiceError(
                    "invalid_payload",
                    "Identity rejected malformed payload action",
                    400,
                    {},
                )
            return verified
        except InvalidSignature:
            raise
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc

    return _delegating_verify_jws


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
