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
import sqlite3
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from db_gateway_service import task_time
from db_gateway_service.app import create_app as create_gateway_app
from db_gateway_service.config import clear_settings_cache as clear_gateway_settings_cache
from db_gateway_service.core.state import init_app_state as init_gateway_state
from db_gateway_service.core.state import reset_app_state as reset_gateway_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_time import epoch_us_to_utc_text, parse_utc_text_to_epoch_us
from httpx import ASGITransport, AsyncClient
from service_auth.financial_identity import operation_id_for
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair
from service_auth.task_postings import TaskPostingIntent

from court_service.app import create_app
from court_service.config import clear_settings_cache
from court_service.core.state import get_app_state, reset_app_state
from court_service.judges import MockJudge
from tests.helpers import make_mock_platform_agent, make_ruling_acknowledgment

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

PLATFORM_AGENT_ID = "a-platform-test-id"

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"
_FIXED_GATEWAY_NOW = "2026-03-01T09:30:00Z"
_FIXED_GATEWAY_NOW_US = parse_utc_text_to_epoch_us(_FIXED_GATEWAY_NOW)


@dataclass(frozen=True)
class SeededTask:
    """Persisted native task history awaiting its matching Court claim."""

    task_id: str
    escrow_id: str
    dispute_operation_id: str
    reason: str


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
  directory: "{Path(db_path).parent / "logs" / "db-gateway"}"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "{_SCHEMA_PATH}"
  busy_timeout_ms: 5000
  journal_mode: "wal"

recovery:
  maintenance_lock_path: "{Path(db_path).parent / "maintenance.lock"}"
  backup_root: "{Path(db_path).parent / "backups"}"

board_authority:
  platform_public_key_path: "{Path(db_path).parent / "platform.pub"}"
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

    def __init__(
        self,
        base_url: str,
        db_path: str,
        server: uvicorn.Server,
        thread: threading.Thread,
    ) -> None:
        self.base_url = base_url
        self.db_path = db_path
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
        "platform",
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
        current_task_board_caller_kid="gateway-task-board",
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

    return _LiveGateway(base_url, db_path, server, thread), writer


@pytest.fixture
def gateway_db_path(tmp_path: Path) -> str:
    """Path to the SQLite file backing the in-process DB Gateway for this test."""
    return str(tmp_path / "gateway.db")


@pytest.fixture
async def _gateway(
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_LiveGateway]:
    monkeypatch.setattr(task_time, "utc_now_us", lambda: _FIXED_GATEWAY_NOW_US)
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


async def _publish_native_task(
    gateway_client: httpx.AsyncClient,
    *,
    task_id: str,
    poster_id: str,
    escrow_id: str,
) -> None:
    intent = TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid.uuid4()}",
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
            "title": "Court integration test task",
            "spec": "Prove the real gateway-backed dispute path end to end",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "acceptance_window_seconds": 1800,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
    )
    admitted = await gateway_client.post("/board/task-publications", json=intent.as_json())
    assert admitted.status_code == 202, admitted.text
    admission = admitted.json()
    assert admission["publication_state"] == "awaiting_lock"
    assert admission["operation_id"] == intent.operation_id
    assert admission["task_id"] == task_id
    assert admission["lock_operation_id"] == intent.lock_operation_id
    lock_timestamp = epoch_us_to_utc_text(parse_utc_text_to_epoch_us(admission["admitted_at"]))

    lock_response = await gateway_client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": intent.lock_operation_id,
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": intent.reward,
            "task_id": task_id,
            "created_at": lock_timestamp,
            "tx_id": f"tx-{uuid.uuid4()}",
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.locked",
                "timestamp": lock_timestamp,
                "agent_id": poster_id,
                "task_id": task_id,
                "summary": f"Escrow locked for {task_id}",
                "payload": "{}",
            },
        },
    )
    assert lock_response.status_code == 201, lock_response.text
    lock_envelope = lock_response.json()
    assert lock_envelope["outcome"] == "native_receipt"
    assert lock_envelope["receipt"]["operation_id"] == intent.lock_operation_id
    assert lock_envelope["receipt"]["operation_kind"] == "escrow_lock"
    assert lock_envelope["receipt"]["source_reference"] == task_id
    assert lock_envelope["current_escrow"]["escrow_id"] == escrow_id
    assert lock_envelope["current_escrow"]["task_id"] == task_id
    assert lock_envelope["current_escrow"]["payer_account_id"] == poster_id
    assert lock_envelope["current_escrow"]["amount"] == intent.reward
    assert lock_envelope["current_escrow"]["status"] == "locked"
    assert lock_envelope["current_escrow"]["created_at"] == lock_timestamp

    reconciled = await gateway_client.post(
        f"/board/task-publications/{intent.operation_id}/reconcile",
        json={},
    )
    assert reconciled.status_code == 201, reconciled.text
    publication = reconciled.json()
    assert publication["publication_state"] == "published"
    assert publication["operation_id"] == intent.operation_id
    assert publication["task_id"] == task_id
    assert publication["lock_operation_id"] == intent.lock_operation_id
    assert publication["escrow_id"] == escrow_id
    result = publication["result"]
    assert result["task_id"] == task_id
    assert result["poster_id"] == poster_id
    assert result["title"] == intent.title
    assert result["spec"] == intent.spec
    assert result["reward"] == intent.reward
    assert result["status"] == "open"
    assert isinstance(result["event_id"], int)


async def seed_task(
    gateway_client: httpx.AsyncClient,
    *,
    task_id: str,
    poster_id: str,
    worker_id: str,
    reason: str,
) -> SeededTask:
    """Seed a real native task history through a pending dispute claim."""
    await register_identity_agent(gateway_client, poster_id)
    await register_identity_agent(gateway_client, worker_id)

    escrow_id = f"esc-{uuid.uuid4()}"
    resp = await gateway_client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid.uuid4()}",
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

    await _publish_native_task(
        gateway_client,
        task_id=task_id,
        poster_id=poster_id,
        escrow_id=escrow_id,
    )

    bid_id = f"bid-{uuid.uuid4()}"
    await _complete_task_operation(
        gateway_client,
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid.uuid4()}",
            "task_id": task_id,
            "command": "submit_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": worker_id,
            "arguments": {"bid_id": bid_id, "bidder_id": worker_id, "amount": 90},
        },
        expected_status="open",
        expected_version=0,
    )
    await _complete_task_operation(
        gateway_client,
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid.uuid4()}",
            "task_id": task_id,
            "command": "accept_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "arguments": {
                "bid_id": bid_id,
                "worker_id": worker_id,
                "escrow_id": escrow_id,
                "reward": 100,
                "bid_amount": 90,
            },
        },
        expected_status="accepted",
        expected_version=1,
    )

    asset_id = f"asset-{uuid.uuid4()}"
    resp = await gateway_client.post(
        "/board/assets",
        json={
            "admission": {
                "expected_status": "accepted",
                "expected_lifecycle_version": 1,
                "max_files_per_task": 10,
            },
            "asset_id": asset_id,
            "task_id": task_id,
            "uploader_id": worker_id,
            "filename": "court-integration-result.txt",
            "content_type": "text/plain",
            "size_bytes": 6,
            "storage_path": f"assets/{task_id}/{asset_id}",
            "content_hash": "a" * 64,
            "uploaded_at": _FIXED_GATEWAY_NOW,
            "event": {
                "event_source": "board",
                "event_type": "asset.uploaded",
                "timestamp": _FIXED_GATEWAY_NOW,
                "task_id": task_id,
                "agent_id": worker_id,
                "summary": f"Worker asset for {task_id}",
                "payload": "{}",
            },
        },
    )
    assert resp.status_code == 201, resp.text

    await _complete_task_operation(
        gateway_client,
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid.uuid4()}",
            "task_id": task_id,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": worker_id,
            "arguments": {"worker_id": worker_id},
        },
        expected_status="submitted",
        expected_version=2,
    )

    dispute_operation_id = f"op-{uuid.uuid4()}"
    resp = await gateway_client.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": dispute_operation_id,
            "task_id": task_id,
            "command": "dispute_task",
            "expected_status": "submitted",
            "expected_lifecycle_version": 2,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "arguments": {
                "poster_id": poster_id,
                "worker_id": worker_id,
                "escrow_id": escrow_id,
                "reward": 100,
                "reason": reason,
            },
        },
    )
    assert resp.status_code == 202, resp.text
    pending = resp.json()
    assert pending["operation_state"] == "claimed"
    assert pending["effect_kind"] == "court_claim"
    assert pending["effect_state"] == "pending"
    return SeededTask(task_id, escrow_id, dispute_operation_id, reason)


async def _complete_task_operation(
    gateway_client: httpx.AsyncClient,
    command: dict[str, Any],
    *,
    expected_status: str,
    expected_version: int,
) -> dict[str, Any]:
    response = await gateway_client.post("/board/task-operations", json=command)
    assert response.status_code == 200, response.text
    operation = response.json()
    assert operation["operation_state"] == "completed"
    assert operation["effect_kind"] == "none"
    assert operation["effect_state"] == "none"
    assert operation["result"]["status"] == expected_status
    assert operation["result"]["lifecycle_version"] == expected_version
    return operation


async def finalize_task_dispute(
    gateway_client: httpx.AsyncClient,
    seeded: SeededTask,
) -> dict[str, Any]:
    """Finalize the pending native dispute from the persisted Court claim."""
    response = await gateway_client.post(
        f"/board/task-operations/{seeded.dispute_operation_id}/finalize",
        json={},
    )
    assert response.status_code == 200, response.text
    completed = response.json()
    assert completed["operation_state"] == "completed"
    assert completed["effect_state"] == "succeeded"
    assert completed["result"]["status"] == "disputed"
    assert completed["result"]["lifecycle_version"] == 3
    return completed


class _NoDeliverableFetcher:
    """Default deliverable fetcher for integration tests: no on-platform assets."""

    async def fetch(self, task_id: str) -> list[str]:
        _ = task_id
        return []

    async def close(self) -> None:
        """Match the production fetcher lifecycle interface."""


async def _register_platform_agent(agent: PlatformAgent) -> None:
    agent.agent_id = PLATFORM_AGENT_ID


def _persisted_task_projection(db_path: str, task_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT t.task_id, t.poster_id, t.worker_id, t.escrow_id, t.title, t.spec, "
            "t.reward, t.status, c.lifecycle_version FROM board_tasks AS t "
            "JOIN board_task_contracts AS c ON c.task_id=t.task_id WHERE t.task_id=?",
            (task_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AssertionError("Platform fixture requires a persisted task")
    projected = dict(row)
    projected["deliverables"] = []
    return projected


def _persisted_claim_parties(db_path: str, claim_id: str) -> dict[str, object]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT c.claimant_id, c.respondent_id, t.escrow_id, t.reward "
            "FROM court_claims AS c JOIN board_tasks AS t ON t.task_id=c.task_id "
            "WHERE c.claim_id=?",
            (claim_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AssertionError("Ruling fixture requires a persisted Court claim")
    return dict(row)


def _write_agent_config(tmp_path: Path) -> Path:
    keys_dir = tmp_path
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
  directory: "{tmp_path / "logs" / "court"}"
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
  judging_lease_seconds: 60
  reclaim_interval_seconds: 3600
evidence:
  max_total_bytes: 262144
  max_file_bytes: 65536
  allowed_extensions: [".txt", ".py", ".json", ".csv", ".md"]
  allowed_content_types: ["text/plain", "application/json", "text/csv"]
judges:
  panel_size: 1
  mock_worker_pct: 50
  mock_spec_quality: satisfied
  mock_delivery_quality: satisfied
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

        async def _get_task(task_id: str) -> dict[str, Any]:
            return _persisted_task_projection(_gateway.db_path, task_id)

        async def _record_ruling(
            _task_id: str,
            payload: dict[str, object],
        ) -> dict[str, Any]:
            parties = _persisted_claim_parties(_gateway.db_path, str(payload["ruling_id"]))
            return make_ruling_acknowledgment(
                payload,
                poster_id=str(parties["claimant_id"]),
                worker_id=str(parties["respondent_id"]),
                escrow_id=str(parties["escrow_id"]),
                reward=int(parties["reward"]),
                event_id=1,
            )

        state.platform_agent.get_task.side_effect = _get_task
        state.platform_agent.record_ruling.side_effect = _record_ruling
        state.judges = [
            MockJudge(
                judge_id="judge-0",
                fixed_worker_pct=70,
                spec_quality="satisfied",
                delivery_quality="satisfied",
                reasoning="Test",
            )
        ]
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
