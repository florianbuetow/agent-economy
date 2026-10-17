"""Integration test fixtures — the real service app, backed by a real,
live, in-process DB Gateway.

Court's production `DisputeDbClient` uses a synchronous `httpx.Client`
(reputation/court sync-by-deviation, plan §5.0 exception #4), so it cannot
be redirected at an in-process ASGI app via `httpx.ASGITransport` (that
transport only implements the async request interface). Instead, this suite
boots a real DB Gateway ASGI app under `uvicorn`, on its own thread with its
own event loop, on a random local port, backed by a fresh temp SQLite file
per test — see services/task-board/tests/integration/conftest.py for why a
same-loop `asyncio.create_task` would deadlock a synchronous client calling
back into its own test process.

The platform agent (task-board/reputation proxy) and judges stay mocked
here (as in tests/unit) — cross-service wiring with live services is
covered by the root cross-service and e2e suites, not this per-service
integration layer.
"""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from db_gateway_service.app import create_app as create_gateway_app
from db_gateway_service.config import clear_settings_cache as clear_gateway_settings_cache
from db_gateway_service.core.state import init_app_state as init_gateway_state
from db_gateway_service.core.state import reset_app_state as reset_gateway_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from httpx import ASGITransport, AsyncClient
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair

from court_service.app import create_app
from court_service.config import clear_settings_cache
from court_service.core.state import get_app_state, reset_app_state
from court_service.judges import MockJudge
from tests.helpers import make_mock_platform_agent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

PLATFORM_AGENT_ID = "a-platform-test-id"

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"


class _GatewayAuth(httpx.Auth):
    """Sign fixture requests with distinct domain capability keys."""

    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> object:
        route_domain = request.url.path.split("/", maxsplit=2)[1]
        action_domain = "board" if route_domain == "board" else route_domain
        action = f"gateway.{action_domain}.{'read' if request.method == 'GET' else 'write'}"
        yield self._signers[route_domain].sign_request(request, action=action)


def _free_port() -> int:
    """Bind to port 0 to get an OS-assigned free local port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _write_gateway_config(db_path: str) -> str:
    """Write a temp db-gateway config.yaml pointing at an isolated SQLite file."""
    content = f"""
service:
  name: "db-gateway-court-integration-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18032
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
    gateway-task-board:
      public_key_path: "{Path(db_path).parent / "gateway-task-board.pub"}"
      capabilities:
        - "gateway.board.read"
        - "gateway.board.write"
    gateway-court:
      public_key_path: "{Path(db_path).parent / "gateway-court.pub"}"
      capabilities:
        - "gateway.court.read"
        - "gateway.court.write"
        - "gateway.board.task.read"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        return f.name


class _LiveGateway:
    """A real DB Gateway ASGI app served by uvicorn on a random local port,
    started/stopped around a single test."""

    def __init__(self, base_url: str, server: uvicorn.Server, thread: threading.Thread) -> None:
        self.base_url = base_url
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


async def _start_live_gateway(db_path: str) -> tuple[_LiveGateway, DbWriter]:
    """Build and serve a real DB Gateway ASGI app, on its own thread with its
    own event loop, against a fresh SQLite file."""
    key_dir = Path(db_path).parent
    for kid in (
        "gateway-identity",
        "gateway-central-bank",
        "gateway-task-board",
        "gateway-court",
    ):
        generate_keypair(kid, key_dir)
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

    return _LiveGateway(base_url, server, thread), writer


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
    """A client onto the live DB Gateway backing this test's court `state.store`
    — used to seed the identity/bank/board rows court_claims' FKs require."""
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


async def seed_task(
    gateway_client: httpx.AsyncClient,
    *,
    task_id: str,
    poster_id: str,
    worker_id: str,
) -> str:
    """Seed the full real-gateway FK chain a court claim requires:
    identity_agents (poster, worker) -> bank_accounts -> bank_escrow ->
    board_tasks. Returns the escrow_id used."""
    await register_identity_agent(gateway_client, poster_id)
    await register_identity_agent(gateway_client, worker_id)

    escrow_id = f"esc-{uuid.uuid4()}"
    resp = await gateway_client.post(
        "/bank/accounts",
        json={
            "account_id": poster_id,
            "created_at": "2026-03-01T09:00:00Z",
            "balance": 100,
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-03-01T09:00:00Z",
                "agent_id": poster_id,
                "summary": f"Created account for {poster_id}",
                "payload": "{}",
            },
            "initial_credit": {
                "tx_id": f"tx-{uuid.uuid4()}",
                "amount": 100,
                "reference": "initial_balance",
                "timestamp": "2026-03-01T09:00:00Z",
            },
        },
    )
    assert resp.status_code == 201, resp.text

    resp = await gateway_client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": 100,
            "task_id": task_id,
            "created_at": "2026-03-01T09:00:01Z",
            "tx_id": f"tx-{uuid.uuid4()}",
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.locked",
                "timestamp": "2026-03-01T09:00:01Z",
                "agent_id": poster_id,
                "summary": f"Escrow locked for {task_id}",
                "payload": "{}",
            },
        },
    )
    assert resp.status_code == 201, resp.text

    resp = await gateway_client.post(
        "/board/tasks",
        json={
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "Court integration test task",
            "spec": "Prove the real gateway-backed dispute path end to end",
            "reward": 100,
            "status": "submitted",
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
            "bidding_deadline": "2026-03-01T10:00:00Z",
            "escrow_id": escrow_id,
            "created_at": "2026-03-01T09:00:00Z",
            "event": {
                "event_source": "board",
                "event_type": "task.created",
                "timestamp": "2026-03-01T09:00:00Z",
                "task_id": task_id,
                "agent_id": poster_id,
                "summary": "Court integration test task created",
                "payload": "{}",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return escrow_id


class _NoDeliverableFetcher:
    """Default deliverable fetcher for integration tests: no on-platform assets."""

    async def fetch(self, task_id: str) -> list[str]:
        _ = task_id
        return []

    async def close(self) -> None:
        """Match the production fetcher lifecycle interface."""


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


@pytest.fixture
async def app(
    tmp_path: Path,
    _gateway: _LiveGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    """Real court app; its production DisputeDbClient talks to a real, live,
    fully-isolated in-process DB Gateway instead of the InMemoryDisputeStore
    fake unit tests use. The platform agent and judges stay mocked."""
    agent_config_path = _write_agent_config(tmp_path)
    config_content = f"""\
service:
  name: "court"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8005
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
  agent_config_path: "{agent_config_path}"
request:
  max_body_size: 1048576
disputes:
  rebuttal_deadline_seconds: 86400
  max_claim_length: 10000
  max_rebuttal_length: 10000
  feedback_extremely_satisfied_cutoff: 80
  feedback_satisfied_cutoff: 40
  feedback_comment_max_length: 256
judges:
  panel_size: 1
  mock_worker_pct: 50
  max_deliverable_bytes: 65536
  judges:
    - id: "judge-0"
      provider: "mock"
      model: "test-model"
db_gateway:
  url: "{_gateway.base_url}"
  timeout_seconds: 10
  signing:
    kid: "gateway-court"
    private_key_path: "gateway-court.key"
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
    async with test_app.router.lifespan_context(test_app):
        state = get_app_state()
        await state.platform_agent.close()

        state.platform_agent = make_mock_platform_agent(agent_id=PLATFORM_AGENT_ID)
        state.judges = [MockJudge(judge_id="judge-0", fixed_worker_pct=70, reasoning="Test")]
        state.deliverable_fetcher = _NoDeliverableFetcher()

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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
