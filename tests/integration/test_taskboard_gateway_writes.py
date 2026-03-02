"""Integration tests: Task Board writes via DB Gateway to economy.db."""

from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from tests.integration.conftest import read_one
from tests.integration.gateway_helpers import create_gateway_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _identity_payload(agent_id: str, name: str, timestamp: str) -> dict[str, object]:
    key_seed = agent_id.replace("a-", "").upper().ljust(43, "Y")
    return {
        "agent_id": agent_id,
        "name": name,
        "public_key": f"ed25519:{key_seed}=",
        "registered_at": timestamp,
        "event": {
            "event_source": "identity",
            "event_type": "agent.registered",
            "timestamp": timestamp,
            "agent_id": agent_id,
            "summary": f"{name} registered",
            "payload": json.dumps({"agent_name": name}),
        },
    }


async def _setup_agents_and_accounts(client: httpx.AsyncClient) -> None:
    for agent_id, name in [("a-poster", "Poster"), ("a-worker", "Worker")]:
        identity_response = await client.post(
            "/identity/agents",
            json=_identity_payload(agent_id, name, "2026-03-02T04:20:00Z"),
        )
        assert identity_response.status_code == 201

        account_response = await client.post(
            "/bank/accounts",
            json={
                "account_id": agent_id,
                "balance": 5000,
                "created_at": "2026-03-02T04:20:01Z",
                "initial_credit": {
                    "tx_id": f"tx-init-{agent_id}",
                    "amount": 5000,
                    "reference": "initial_balance",
                    "timestamp": "2026-03-02T04:20:01Z",
                },
                "event": {
                    "event_source": "bank",
                    "event_type": "account.created",
                    "timestamp": "2026-03-02T04:20:01Z",
                    "agent_id": agent_id,
                    "summary": f"Account created for {name}",
                    "payload": "{}",
                },
            },
        )
        assert account_response.status_code == 201


async def _create_task_with_escrow(
    client: httpx.AsyncClient,
    task_id: str,
    poster_id: str,
    reward: int,
) -> None:
    lock_response = await client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": f"esc-{task_id}",
            "payer_account_id": poster_id,
            "amount": reward,
            "task_id": task_id,
            "created_at": "2026-03-02T04:21:00Z",
            "tx_id": f"tx-escrow-{task_id}",
            "event": {
                "event_source": "bank",
                "event_type": "escrow.locked",
                "timestamp": "2026-03-02T04:21:00Z",
                "agent_id": poster_id,
                "task_id": task_id,
                "summary": f"Escrow locked for {task_id}",
                "payload": json.dumps({"escrow_id": f"esc-{task_id}", "amount": reward}),
            },
        },
    )
    assert lock_response.status_code == 201

    task_response = await client.post(
        "/board/tasks",
        json={
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "Test Task",
            "spec": "Implement something useful",
            "reward": reward,
            "status": "open",
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 7200,
            "review_deadline_seconds": 3600,
            "bidding_deadline": "2026-03-02T05:21:00Z",
            "bid_count": 0,
            "escrow_pending": 0,
            "escrow_id": f"esc-{task_id}",
            "created_at": "2026-03-02T04:21:00Z",
            "event": {
                "event_source": "board",
                "event_type": "task.created",
                "timestamp": "2026-03-02T04:21:00Z",
                "agent_id": poster_id,
                "task_id": task_id,
                "summary": "Task created: Test Task",
                "payload": json.dumps({"title": "Test Task", "reward": reward}),
            },
        },
    )
    assert task_response.status_code == 201


@pytest_asyncio.fixture
async def gw_client(initialized_db: str):
    client = create_gateway_client(initialized_db)
    async with client:
        await _setup_agents_and_accounts(client)
        yield client


class TestTaskBoardGatewayWrites:
    async def test_create_task_writes_board_task_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        await _create_task_with_escrow(gw_client, "t-test-001", "a-poster", 200)

        row = read_one(
            initialized_db,
            "SELECT task_id, poster_id, title, spec, reward, status, escrow_id "
            "FROM board_tasks WHERE task_id = ?",
            ("t-test-001",),
        )
        assert row is not None
        assert row["poster_id"] == "a-poster"
        assert row["title"] == "Test Task"
        assert row["spec"] == "Implement something useful"
        assert row["reward"] == 200
        assert row["status"] == "open"
        assert row["escrow_id"] == "esc-t-test-001"

    async def test_submit_bid_writes_bid_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        await _create_task_with_escrow(gw_client, "t-bid-001", "a-poster", 200)

        response = await gw_client.post(
            "/board/bids",
            json={
                "bid_id": "bid-001",
                "task_id": "t-bid-001",
                "bidder_id": "a-worker",
                "proposal": "I can do this well",
                "amount": 180,
                "submitted_at": "2026-03-02T04:25:00Z",
                "constraints": {"status": "open"},
                "event": {
                    "event_source": "board",
                    "event_type": "bid.submitted",
                    "timestamp": "2026-03-02T04:25:00Z",
                    "agent_id": "a-worker",
                    "task_id": "t-bid-001",
                    "summary": "Bid submitted",
                    "payload": json.dumps({"bid_id": "bid-001", "amount": 180}),
                },
            },
        )
        assert response.status_code == 201

        bid = read_one(
            initialized_db,
            "SELECT bid_id, task_id, bidder_id, proposal, amount FROM board_bids WHERE bid_id = ?",
            ("bid-001",),
        )
        assert bid is not None
        assert bid["task_id"] == "t-bid-001"
        assert bid["bidder_id"] == "a-worker"
        assert bid["proposal"] == "I can do this well"
        assert bid["amount"] == 180

    async def test_update_task_status_writes_updated_fields(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        await _create_task_with_escrow(gw_client, "t-status-001", "a-poster", 300)

        response = await gw_client.post(
            "/board/tasks/t-status-001/status",
            json={
                "updates": {
                    "status": "accepted",
                    "worker_id": "a-worker",
                    "accepted_bid_id": "bid-accepted-001",
                    "accepted_at": "2026-03-02T04:30:00Z",
                    "execution_deadline": "2026-03-02T06:30:00Z",
                },
                "constraints": {"status": "open"},
                "event": {
                    "event_source": "board",
                    "event_type": "task.accepted",
                    "timestamp": "2026-03-02T04:30:00Z",
                    "agent_id": "a-poster",
                    "task_id": "t-status-001",
                    "summary": "Task accepted",
                    "payload": json.dumps({"worker_id": "a-worker"}),
                },
            },
        )
        assert response.status_code == 200

        task = read_one(
            initialized_db,
            "SELECT status, worker_id, accepted_bid_id, accepted_at, execution_deadline "
            "FROM board_tasks WHERE task_id = ?",
            ("t-status-001",),
        )
        assert task is not None
        assert task["status"] == "accepted"
        assert task["worker_id"] == "a-worker"
        assert task["accepted_bid_id"] == "bid-accepted-001"
        assert task["accepted_at"] == "2026-03-02T04:30:00Z"

    async def test_record_asset_writes_asset_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        await _create_task_with_escrow(gw_client, "t-asset-001", "a-poster", 200)

        response = await gw_client.post(
            "/board/assets",
            json={
                "asset_id": "asset-001",
                "task_id": "t-asset-001",
                "uploader_id": "a-worker",
                "filename": "deliverable.zip",
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": "/data/assets/asset-001",
                "content_hash": "sha256:abc123",
                "uploaded_at": "2026-03-02T04:35:00Z",
                "event": {
                    "event_source": "board",
                    "event_type": "asset.uploaded",
                    "timestamp": "2026-03-02T04:35:00Z",
                    "agent_id": "a-worker",
                    "task_id": "t-asset-001",
                    "summary": "Asset uploaded",
                    "payload": json.dumps({"filename": "deliverable.zip"}),
                },
            },
        )
        assert response.status_code == 201

        asset = read_one(
            initialized_db,
            "SELECT filename, content_type, size_bytes, storage_path, content_hash "
            "FROM board_assets WHERE asset_id = ?",
            ("asset-001",),
        )
        assert asset is not None
        assert asset["filename"] == "deliverable.zip"
        assert asset["content_type"] == "application/zip"
        assert asset["size_bytes"] == 1024
        assert asset["storage_path"] == "/data/assets/asset-001"
        assert asset["content_hash"] == "sha256:abc123"
