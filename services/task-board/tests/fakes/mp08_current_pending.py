"""Self-contained live Board helpers for MP-08 current-V5 behavioral proofs."""

from __future__ import annotations

import asyncio
import base64
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import httpx
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, classify_schema
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_time import utc_now_us
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair as provision_keypair

from task_board_service.app import create_app
from task_board_service.config import clear_settings_cache
from task_board_service.core.lifespan import lifespan
from task_board_service.core.state import get_app_state, reset_app_state
from task_board_service.services.posting_recovery import PostingRecovery
from tests.helpers import make_jws_token, verify_compact_jws
from tests.integration.mp05_posting_setup import (
    native_escrow_payload,
    native_task_payload,
    persist_lock_after_admission,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    import pytest
    from httpx import AsyncClient

    from task_board_service.services.task_db_client import TaskDbClient


PLATFORM_AGENT_ID = "a-platform-test-id"
_ORIGINAL_POSTING_RUN_BATCH = PostingRecovery.run_batch


@dataclass(frozen=True)
class LiveBoard:
    client: httpx.AsyncClient
    bank: AsyncMock
    completed_ticks: asyncio.Queue[None]

    async def wait_for_completed_tick(self, *, timeout_seconds: float) -> None:
        await asyncio.wait_for(self.completed_ticks.get(), timeout=timeout_seconds)

    async def wait_for_read_only_predicate(
        self,
        predicate: Callable[[], bool],
        *,
        max_completed_ticks: int,
        timeout_per_tick_seconds: float,
    ) -> bool:
        """Give background work bounded completed cycles; predicate performs reads only."""
        for _tick in range(max_completed_ticks):
            await self.wait_for_completed_tick(timeout_seconds=timeout_per_tick_seconds)
            if predicate():
                return True
        return False


@dataclass(frozen=True, slots=True)
class SubmittedTask:
    task_id: str
    poster_id: str
    worker_id: str
    escrow_id: str
    accepted_bid_id: str
    accepted_bid_amount: int
    reward: int


def generate_test_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, f"ed25519:{base64.b64encode(public_bytes).decode()}"


def read_rows(
    db_path: str,
    query: str,
    parameters: tuple[object, ...] = (),
) -> tuple[tuple[object, ...], ...]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return tuple(tuple(row) for row in connection.execute(query, parameters).fetchall())
    finally:
        connection.close()


async def _register_identity_agent(gateway_client: AsyncClient, agent_id: str) -> None:
    response = await gateway_client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{uuid.uuid4()}",
            "registered_at": "2026-10-22T05:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-10-22T05:00:00Z",
                "agent_id": agent_id,
                "summary": f"Registered {agent_id}",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text


async def post_signed(
    client: AsyncClient,
    *,
    path: str,
    private_key: Ed25519PrivateKey,
    signer_id: str,
    payload: dict[str, Any],
) -> httpx.Response:
    return await client.post(path, json={"token": make_jws_token(private_key, signer_id, payload)})


async def create_submitted_task(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_key: Ed25519PrivateKey,
    worker_key: Ed25519PrivateKey,
) -> SubmittedTask:
    """Create one real native task with a future review cutoff and locked reward."""
    task_id = f"t-{uuid.uuid4()}"
    poster_id = f"a-mp08-poster-{uuid.uuid4()}"
    worker_id = f"a-mp08-worker-{uuid.uuid4()}"
    escrow_id = f"esc-{uuid.uuid4()}"
    reward = 100
    await _register_identity_agent(gateway_client, poster_id)
    await _register_identity_agent(gateway_client, worker_id)
    funded = await gateway_client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid.uuid4()}",
            "account_id": poster_id,
            "balance": reward,
            "created_at": "2026-10-22T05:00:00Z",
            "initial_credit": {
                "tx_id": f"tx-{uuid.uuid4()}",
                "amount": reward,
                "reference": "initial_balance",
                "timestamp": "2026-10-22T05:00:00Z",
            },
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-10-22T05:00:00Z",
                "agent_id": poster_id,
                "summary": "Created funded MP-08 poster",
                "payload": "{}",
            },
        },
    )
    assert funded.status_code == 201, funded.text
    worker_account = await gateway_client.post(
        "/bank/accounts",
        json={
            "account_id": worker_id,
            "balance": 0,
            "created_at": "2026-10-22T05:00:01Z",
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-10-22T05:00:01Z",
                "agent_id": worker_id,
                "summary": "Created MP-08 worker",
                "payload": "{}",
            },
        },
    )
    assert worker_account.status_code == 201, worker_account.text
    payload = native_task_payload(
        task_id=task_id,
        poster_id=poster_id,
        title="MP-08 stranded financial effect",
        spec="Recover the immutable financial effect without a public task read.",
        reward=reward,
        bidding_deadline_seconds=86_400,
        acceptance_window_seconds=86_400,
        execution_deadline_seconds=86_400,
        review_deadline_seconds=86_400,
    )

    async def persist_lock(escrow_token: str) -> dict[str, Any]:
        assert escrow_token
        return await persist_lock_after_admission(
            gateway_client,
            task_payload=payload,
            escrow_id=escrow_id,
            clock_us=utc_now_us,
        )

    state = get_app_state()
    assert state.central_bank_client is not None
    state.central_bank_client.escrow_lock = AsyncMock(side_effect=persist_lock)
    created = await client.post(
        "/tasks",
        json={
            "task_token": make_jws_token(poster_key, poster_id, payload),
            "escrow_token": make_jws_token(
                poster_key,
                poster_id,
                native_escrow_payload(payload),
                audience="central-bank",
                resource="POST /escrow/lock",
            ),
        },
    )
    assert created.status_code == 201, created.text
    bid = await post_signed(
        client,
        path=f"/tasks/{task_id}/bids",
        private_key=worker_key,
        signer_id=worker_id,
        payload={
            "action": "submit_bid",
            "operation_id": f"op-{uuid.uuid4()}",
            "expected_lifecycle_version": 0,
            "task_id": task_id,
            "bidder_id": worker_id,
            "amount": 100,
        },
    )
    assert bid.status_code == 201, bid.text
    bid_id = str(bid.json()["bid_id"])
    accepted = await post_signed(
        client,
        path=f"/tasks/{task_id}/bids/{bid_id}/accept",
        private_key=poster_key,
        signer_id=poster_id,
        payload={
            "action": "accept_bid",
            "operation_id": f"op-{uuid.uuid4()}",
            "expected_lifecycle_version": 0,
            "task_id": task_id,
            "bid_id": bid_id,
            "poster_id": poster_id,
        },
    )
    assert accepted.status_code == 200, accepted.text
    store = cast("TaskDbClient", get_app_state().store)
    store.insert_asset(
        {
            "asset_id": f"asset-{uuid.uuid4()}",
            "task_id": task_id,
            "uploader_id": worker_id,
            "filename": "work.txt",
            "content_type": "text/plain",
            "size_bytes": 4,
            "storage_path": f"assets/{task_id}/work.txt",
            "content_hash": "c" * 64,
            "uploaded_at": "2026-10-22T05:00:02Z",
        },
        expected_lifecycle_version=1,
        max_files_per_task=10,
    )
    submitted = await post_signed(
        client,
        path=f"/tasks/{task_id}/submit",
        private_key=worker_key,
        signer_id=worker_id,
        payload={
            "action": "submit_deliverable",
            "operation_id": f"op-{uuid.uuid4()}",
            "expected_lifecycle_version": 1,
            "task_id": task_id,
            "worker_id": worker_id,
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"
    assert submitted.json()["review_deadline"] is not None
    return SubmittedTask(task_id, poster_id, worker_id, escrow_id, bid_id, 90, reward)


def persisting_release(
    gateway_client: AsyncClient,
    *,
    setup: SubmittedTask,
    operation_id: str,
) -> AsyncMock:
    """Persist one exact release receipt only when the Board delivers its stored intent."""

    async def release(*args: object, **kwargs: object) -> dict[str, Any]:
        assert args == ()
        assert kwargs == {
            "escrow_id": setup.escrow_id,
            "recipient_account_id": setup.worker_id,
            "operation_id": operation_id,
            "source_reference": f"task:{setup.task_id}:approved",
        }
        response = await gateway_client.post(
            "/bank/escrow/release",
            json={
                "operation_id": operation_id,
                "escrow_id": setup.escrow_id,
                "recipient_account_id": setup.worker_id,
                "source_reference": f"task:{setup.task_id}:approved",
                "tx_id": f"tx-{uuid.uuid4()}",
                "resolved_at": "2026-10-22T05:00:03Z",
                "event": {
                    "event_source": "central-bank",
                    "event_type": "escrow.released",
                    "timestamp": "2026-10-22T05:00:03Z",
                    "task_id": setup.task_id,
                    "agent_id": setup.worker_id,
                    "summary": "Released MP-08 escrow",
                    "payload": "{}",
                },
            },
        )
        assert response.status_code == 200, response.text
        return cast("dict[str, Any]", response.json())

    return AsyncMock(side_effect=release)


def financial_controls(db_path: str) -> tuple[int, int, int]:
    """Assert exact V6 ownership/audits and return conservation components."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V6
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_task_contracts(connection).contracts_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        balances = int(
            connection.execute("SELECT COALESCE(SUM(balance),0) FROM bank_accounts").fetchone()[0]
        )
        locked = int(
            connection.execute(
                "SELECT COALESCE(SUM(amount),0) FROM bank_escrow WHERE status='locked'"
            ).fetchone()[0]
        )
        receipts = int(
            connection.execute("SELECT COUNT(*) FROM bank_operation_receipts").fetchone()[0]
        )
        return balances, locked, receipts
    finally:
        connection.close()


def write_legacy_history(
    db_path: str,
    statements: tuple[tuple[str, tuple[object, ...]], ...],
) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for sql, parameters in statements:
            connection.execute(sql, parameters)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _write_agent_config(runtime_dir: Path) -> Path:
    keys_dir = runtime_dir / "agent-keys"
    if not (keys_dir / "platform.key").exists():
        provision_keypair("platform", keys_dir)
    if not (keys_dir / "operator.pub").exists():
        provision_keypair("operator", keys_dir)
    roster_path = runtime_dir / "roster.yaml"
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
    agent_config_path = runtime_dir / "agents.yaml"
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
                "data": {"keys_dir": str(keys_dir), "roster_path": str(roster_path)},
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


@asynccontextmanager
async def running_board(
    *,
    runtime_dir: Path,
    gateway_base_url: str,
    monkeypatch: pytest.MonkeyPatch,
    escrow_release: AsyncMock | None,
) -> AsyncIterator[LiveBoard]:
    """Start one real Task Board lifespan against the caller-owned live Gateway."""
    agent_config_path = _write_agent_config(runtime_dir)
    config_path = runtime_dir / f"task-board-mp08-{uuid.uuid4()}.yaml"
    config_path.write_text(
        f"""
service:
  name: "task-board-mp08-current-gap"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8003
  log_level: "warning"
logging:
  level: "WARNING"
  directory: "{runtime_dir / "logs" / "task-board"}"
database:
  path: "{runtime_dir / "unused-board.db"}"
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
  escrow_settle_path: "/escrow/settle"
  timeout_seconds: 10
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
  agent_config_path: "{agent_config_path}"
asset_quota:
  max_total_bytes_per_agent: 536870912
  max_total_bytes: 4294967296
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
  maintenance_lock_path: "{runtime_dir / "task-board-maintenance.lock"}"
posting_recovery:
  interval_seconds: 0.05
  batch_size: 100
  diagnostic_limit: 100
financial_effect_recovery:
  interval_seconds: 2.0
  max_delivery_attempts_per_cycle: 10
limits:
  max_file_size: 10485760
  max_assets_per_task: 20
db_gateway:
  url: "{gateway_base_url}"
  timeout_seconds: 10
  signing:
    kid: "gateway-task-board"
    private_key_path: "gateway-task-board.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
deadline_evaluation:
  evaluation_interval_seconds: 1
  actor_id: "system-task-board-deadline"
"""
    )
    old_config = os.environ.get("CONFIG_PATH")
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    reset_app_state()
    monkeypatch.setattr(PlatformAgent, "register", _register_test_platform)
    bank = AsyncMock()
    bank.close = AsyncMock()
    bank.escrow_lock = AsyncMock(return_value={"status": "locked"})
    bank.escrow_release = (
        escrow_release
        if escrow_release is not None
        else AsyncMock(return_value={"status": "released"})
    )
    bank.escrow_split = AsyncMock(return_value={"status": "split"})
    identity = AsyncMock()
    identity.close = AsyncMock()
    identity.verify_jws = AsyncMock(side_effect=verify_compact_jws)

    def bank_factory(**_kwargs: object) -> AsyncMock:
        return bank

    def identity_factory(**_kwargs: object) -> AsyncMock:
        return identity

    monkeypatch.setattr("task_board_service.core.lifespan.BankClient", bank_factory)
    monkeypatch.setattr("task_board_service.core.lifespan.IdentityClient", identity_factory)
    completed_ticks: asyncio.Queue[None] = asyncio.Queue()

    async def observed_run_batch(self: PostingRecovery) -> None:
        await _ORIGINAL_POSTING_RUN_BATCH(self)
        completed_ticks.put_nowait(None)

    monkeypatch.setattr(PostingRecovery, "run_batch", observed_run_batch)
    app = create_app()
    try:
        async with lifespan(app):
            state = get_app_state()
            assert state.task_manager is not None
            assert state.token_validator is not None
            assert state.escrow_coordinator is not None
            assert state.central_bank_client is bank
            assert state.identity_client is identity
            assert cast("Any", state.task_manager)._central_bank_client is bank
            assert cast("Any", state.token_validator)._identity_client is identity
            assert cast("Any", state.escrow_coordinator)._central_bank_client is bank

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield LiveBoard(client=client, bank=bank, completed_ticks=completed_ticks)
    finally:
        reset_app_state()
        clear_settings_cache()
        config_path.unlink(missing_ok=True)
        if old_config is None:
            os.environ.pop("CONFIG_PATH", None)
        else:
            os.environ["CONFIG_PATH"] = old_config
