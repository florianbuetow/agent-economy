"""Integration tests — the real task-board app driven end-to-end against a
real, live, in-process DB Gateway (the production `TaskDbClient` path,
T-071/GAP-F1). These tests exercise the HTTP transport in addition to the
Gateway persistence also used by the unit fixtures. Assertions read both
the task-board API and the gateway's SQLite file, including FK-enforced
relationships (poster/worker -> identity_agents, escrow_id -> bank_escrow).
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from db_gateway_service.task_time import utc_now_us

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token
from tests.integration.conftest import register_identity_agent
from tests.integration.mp05_posting_setup import (
    native_escrow_payload,
    native_task_payload,
    persist_lock_after_admission,
)
from tests.integration.test_mp07_native_public_lifecycle import (
    _create_zero_balance_account,
    _install_persisting_release,
)

if TYPE_CHECKING:
    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from tests.integration.conftest import _LiveGateway

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_embedded_gateway_holds_only_its_disposable_maintenance_lock(
    _gateway: _LiveGateway,
    gateway_db_path: str,
) -> None:
    expected = Path(gateway_db_path).parent / "maintenance.lock"

    assert _gateway.maintenance_lock_path == expected
    assert expected.is_file()


def _read_one(
    db_path: str, query: str, params: tuple[object, ...] = ()
) -> dict[str, object] | None:
    """Read a single SQLite row as a dict, straight from the gateway's own file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


async def _seed_funded_poster(
    gateway_client: httpx.AsyncClient,
    *,
    payer_id: str,
    amount: int,
) -> None:
    """Seed the identity and funded account required for native admission."""
    await register_identity_agent(gateway_client, payer_id)
    resp = await gateway_client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid.uuid4()}",
            "account_id": payer_id,
            "created_at": "2026-03-01T09:00:00Z",
            "balance": amount,
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-03-01T09:00:00Z",
                "agent_id": payer_id,
                "summary": f"Created account for {payer_id}",
                "payload": "{}",
            },
            "initial_credit": {
                "tx_id": f"tx-{uuid.uuid4()}",
                "amount": amount,
                "reference": "initial_balance",
                "timestamp": "2026-03-01T09:00:00Z",
            },
        },
    )
    assert resp.status_code == 201, resp.text


class TestTaskLifecycleGatewayBacked:
    async def test_full_lifecycle_persists_through_real_gateway(
        self,
        client: httpx.AsyncClient,
        gateway_client: httpx.AsyncClient,
        poster_keypair: tuple[Ed25519PrivateKey, str],
        worker_keypair: tuple[Ed25519PrivateKey, str],
        gateway_db_path: str,
    ) -> None:
        """Create -> bid -> accept -> submit -> approve, verified against the
        real gateway-backed TaskDbClient at every step."""
        poster_id = "a-poster-integration"
        worker_id = "a-worker-integration"
        task_id = f"t-{uuid.uuid4()}"
        escrow_id = f"esc-{uuid.uuid4()}"

        await register_identity_agent(gateway_client, worker_id)
        await _create_zero_balance_account(gateway_client, account_id=worker_id)
        await _seed_funded_poster(gateway_client, payer_id=poster_id, amount=100)
        task_payload = native_task_payload(
            task_id=task_id,
            poster_id=poster_id,
            title="Integration test task",
            spec="Prove the real gateway-backed path end to end",
            reward=100,
            bidding_deadline_seconds=3600,
            acceptance_window_seconds=1800,
            execution_deadline_seconds=86400,
            review_deadline_seconds=86400,
        )
        state = get_app_state()
        assert state.central_bank_client is not None

        async def persist_lock(escrow_token: str) -> dict[str, Any]:
            assert escrow_token
            return await persist_lock_after_admission(
                gateway_client,
                task_payload=task_payload,
                escrow_id=escrow_id,
                clock_us=utc_now_us,
            )

        state.central_bank_client.escrow_lock = AsyncMock(side_effect=persist_lock)
        if state.escrow_coordinator is not None:
            state.escrow_coordinator._central_bank_client = state.central_bank_client

        task_token = make_jws_token(poster_keypair[0], poster_id, task_payload)
        escrow_token = make_jws_token(
            poster_keypair[0],
            poster_id,
            native_escrow_payload(task_payload),
            audience="central-bank",
            resource="POST /escrow/lock",
        )
        create_resp = await client.post(
            "/tasks", json={"task_token": task_token, "escrow_token": escrow_token}
        )
        assert create_resp.status_code == 201, create_resp.text
        created_task = create_resp.json()["result"]
        assert created_task["status"] == "open"
        assert created_task["lifecycle_version"] == 0

        row = _read_one(
            gateway_db_path,
            "SELECT poster_id, escrow_id, status FROM board_tasks WHERE task_id = ?",
            (task_id,),
        )
        assert row is not None
        assert row["poster_id"] == poster_id
        assert row["escrow_id"] == escrow_id
        assert row["status"] == "open"

        bid_payload = {
            "action": "submit_bid",
            "operation_id": f"op-{uuid.uuid4()}",
            "expected_lifecycle_version": created_task["lifecycle_version"],
            "task_id": task_id,
            "bidder_id": worker_id,
            "amount": 100,
        }
        bid_token = make_jws_token(worker_keypair[0], worker_id, bid_payload)
        bid_resp = await client.post(f"/tasks/{task_id}/bids", json={"token": bid_token})
        assert bid_resp.status_code == 201, bid_resp.text
        bid_id = bid_resp.json()["bid_id"]

        bid_row = _read_one(
            gateway_db_path,
            "SELECT bidder_id, amount FROM board_bids WHERE bid_id = ?",
            (bid_id,),
        )
        assert bid_row is not None
        assert bid_row["bidder_id"] == worker_id

        accept_payload = {
            "action": "accept_bid",
            "operation_id": f"op-{uuid.uuid4()}",
            "expected_lifecycle_version": created_task["lifecycle_version"],
            "task_id": task_id,
            "bid_id": bid_id,
            "poster_id": poster_id,
        }
        accept_token = make_jws_token(poster_keypair[0], poster_id, accept_payload)
        accept_resp = await client.post(
            f"/tasks/{task_id}/bids/{bid_id}/accept", json={"token": accept_token}
        )
        assert accept_resp.status_code == 200, accept_resp.text
        accepted_task = accept_resp.json()
        assert accepted_task["status"] == "accepted"
        assert accepted_task["lifecycle_version"] == 1

        accepted_row = _read_one(
            gateway_db_path,
            "SELECT worker_id, status FROM board_tasks WHERE task_id = ?",
            (task_id,),
        )
        assert accepted_row is not None
        assert accepted_row["worker_id"] == worker_id
        assert accepted_row["status"] == "accepted"

        upload_payload = {"action": "upload_asset", "task_id": task_id}
        upload_token = make_jws_token(worker_keypair[0], worker_id, upload_payload)
        upload_resp = await client.post(
            f"/tasks/{task_id}/assets",
            headers={"Authorization": f"Bearer {upload_token}"},
            files={"file": ("deliverable.txt", b"the deliverable content", "text/plain")},
        )
        assert upload_resp.status_code == 201, upload_resp.text

        submit_payload = {
            "action": "submit_deliverable",
            "operation_id": f"op-{uuid.uuid4()}",
            "expected_lifecycle_version": accepted_task["lifecycle_version"],
            "task_id": task_id,
            "worker_id": worker_id,
        }
        submit_token = make_jws_token(worker_keypair[0], worker_id, submit_payload)
        submit_resp = await client.post(f"/tasks/{task_id}/submit", json={"token": submit_token})
        assert submit_resp.status_code == 200, submit_resp.text
        submitted_task = submit_resp.json()
        assert submitted_task["status"] == "submitted"
        assert submitted_task["lifecycle_version"] == 2

        approve_operation_id = f"op-{uuid.uuid4()}"
        source_reference = f"task:{task_id}:approved"
        release = _install_persisting_release(
            gateway_client,
            db_path=gateway_db_path,
            task_id=task_id,
            command="approve_task",
            operation_id=approve_operation_id,
            source_status="submitted",
            source_version=submitted_task["lifecycle_version"],
            escrow_id=escrow_id,
            recipient_id=worker_id,
            source_reference=source_reference,
        )
        approve_payload = {
            "action": "approve_task",
            "operation_id": approve_operation_id,
            "expected_lifecycle_version": submitted_task["lifecycle_version"],
            "task_id": task_id,
            "poster_id": poster_id,
        }
        approve_token = make_jws_token(poster_keypair[0], poster_id, approve_payload)
        approve_resp = await client.post(f"/tasks/{task_id}/approve", json={"token": approve_token})
        assert approve_resp.status_code == 200, approve_resp.text
        approved_task = approve_resp.json()
        assert approved_task["status"] == "approved"
        assert approved_task["lifecycle_version"] == 3
        release.assert_awaited_once_with(
            escrow_id=escrow_id,
            recipient_account_id=worker_id,
            operation_id=approve_operation_id,
            source_reference=source_reference,
        )

        final_row = _read_one(
            gateway_db_path,
            "SELECT status, approved_at FROM board_tasks WHERE task_id = ?",
            (task_id,),
        )
        assert final_row is not None
        assert final_row["status"] == "approved"
        assert final_row["approved_at"] is not None

        receipt = _read_one(
            gateway_db_path,
            "SELECT operation_kind, source_reference FROM bank_operation_receipts "
            "WHERE operation_id = ?",
            (approve_operation_id,),
        )
        assert receipt == {
            "operation_kind": "escrow_release",
            "source_reference": source_reference,
        }
        worker_account = _read_one(
            gateway_db_path,
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            (worker_id,),
        )
        assert worker_account == {"balance": 100}
        approval_event = _read_one(
            gateway_db_path,
            "SELECT COUNT(*) AS count FROM events "
            "WHERE task_id = ? AND event_source = 'board' AND event_type = 'task.approved'",
            (task_id,),
        )
        assert approval_event == {"count": 1}
