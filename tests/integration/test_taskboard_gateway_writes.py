"""Integration tests: Task Board writes via DB Gateway to economy.db."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from tests.integration.conftest import read_one
from tests.integration.gateway_helpers import create_gateway_client
from tests.integration.native_task_fixtures import (
    accept_native_bid,
    freeze_task_clock,
    publish_open_task,
    submit_native_bid,
)

if TYPE_CHECKING:
    import httpx

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
    accounts = [
        ("a-poster", "Poster", "op-00000000-0000-4000-8000-000000000101"),
        ("a-worker", "Worker", "op-00000000-0000-4000-8000-000000000102"),
    ]
    for agent_id, name, operation_id in accounts:
        identity_response = await client.post(
            "/identity/agents",
            json=_identity_payload(agent_id, name, "2026-03-02T04:20:00Z"),
        )
        assert identity_response.status_code == 201

        account_response = await client.post(
            "/bank/accounts",
            json={
                "operation_id": operation_id,
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
        account_envelope = account_response.json()
        assert account_envelope["outcome"] == "native_receipt"
        assert account_envelope["receipt"]["operation_id"] == operation_id
        assert account_envelope["receipt"]["result"]["balance_after"] == 5000
        assert account_envelope["current_escrow"] is None


async def _create_task_with_escrow(
    client: httpx.AsyncClient,
    task_id: str,
    poster_id: str,
    reward: int,
) -> None:
    task_response = await publish_open_task(
        client,
        task_id=task_id,
        poster_id=poster_id,
        title="Test Task",
        spec="Implement something useful",
        reward=reward,
        escrow_id=f"esc-{task_id}",
        bidding_deadline_seconds=3600,
        acceptance_window_seconds=1800,
        execution_deadline_seconds=7200,
        review_deadline_seconds=3600,
    )
    assert task_response.status_code == 201


@pytest_asyncio.fixture
async def gw_client(initialized_db: str, monkeypatch: pytest.MonkeyPatch):
    freeze_task_clock(monkeypatch, "2026-03-02T04:30:00Z")
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
        await _create_task_with_escrow(
            gw_client, "t-00000000-0000-4000-8000-000000000401", "a-poster", 200
        )

        row = read_one(
            initialized_db,
            "SELECT task_id, poster_id, title, spec, reward, status, escrow_id "
            "FROM board_tasks WHERE task_id = ?",
            ("t-00000000-0000-4000-8000-000000000401",),
        )
        assert row is not None
        assert row["poster_id"] == "a-poster"
        assert row["title"] == "Test Task"
        assert row["spec"] == "Implement something useful"
        assert row["reward"] == 200
        assert row["status"] == "open"
        assert row["escrow_id"] == "esc-t-00000000-0000-4000-8000-000000000401"

    async def test_submit_bid_writes_bid_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        await _create_task_with_escrow(
            gw_client, "t-00000000-0000-4000-8000-000000000402", "a-poster", 200
        )

        response = await submit_native_bid(
            gw_client,
            operation_id="op-00000000-0000-4000-8000-000000000104",
            task_id="t-00000000-0000-4000-8000-000000000402",
            bid_id="bid-001",
            bidder_id="a-worker",
            amount=180,
        )
        assert response.status_code == 200

        bid = read_one(
            initialized_db,
            "SELECT bid_id, task_id, bidder_id, proposal, amount FROM board_bids WHERE bid_id = ?",
            ("bid-001",),
        )
        assert bid is not None
        assert bid["task_id"] == "t-00000000-0000-4000-8000-000000000402"
        assert bid["bidder_id"] == "a-worker"
        assert bid["proposal"] == "180"
        assert bid["amount"] == 180

    async def test_update_task_status_writes_updated_fields(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        await _create_task_with_escrow(
            gw_client, "t-00000000-0000-4000-8000-000000000403", "a-poster", 300
        )

        bid_response = await submit_native_bid(
            gw_client,
            operation_id="op-00000000-0000-4000-8000-000000000105",
            task_id="t-00000000-0000-4000-8000-000000000403",
            bid_id="bid-accepted-001",
            bidder_id="a-worker",
            amount=275,
        )
        assert bid_response.status_code == 200
        response = await accept_native_bid(
            gw_client,
            operation_id="op-00000000-0000-4000-8000-000000000106",
            task_id="t-00000000-0000-4000-8000-000000000403",
            bid_id="bid-accepted-001",
            poster_id="a-poster",
            worker_id="a-worker",
            escrow_id="esc-t-00000000-0000-4000-8000-000000000403",
            reward=300,
            bid_amount=275,
        )
        assert response.status_code == 200

        task = read_one(
            initialized_db,
            "SELECT status, worker_id, accepted_bid_id, accepted_at, execution_deadline "
            "FROM board_tasks WHERE task_id = ?",
            ("t-00000000-0000-4000-8000-000000000403",),
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
        await _create_task_with_escrow(
            gw_client, "t-00000000-0000-4000-8000-000000000404", "a-poster", 200
        )
        bid = await submit_native_bid(
            gw_client,
            operation_id="op-00000000-0000-4000-8000-000000000107",
            task_id="t-00000000-0000-4000-8000-000000000404",
            bid_id="bid-asset-001",
            bidder_id="a-worker",
            amount=180,
        )
        assert bid.status_code == 200, bid.text
        accepted = await accept_native_bid(
            gw_client,
            operation_id="op-00000000-0000-4000-8000-000000000108",
            task_id="t-00000000-0000-4000-8000-000000000404",
            bid_id="bid-asset-001",
            poster_id="a-poster",
            worker_id="a-worker",
            escrow_id="esc-t-00000000-0000-4000-8000-000000000404",
            reward=200,
            bid_amount=180,
        )
        assert accepted.status_code == 200, accepted.text

        response = await gw_client.post(
            "/board/assets",
            json={
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
                "asset_id": "asset-001",
                "task_id": "t-00000000-0000-4000-8000-000000000404",
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
                    "task_id": "t-00000000-0000-4000-8000-000000000404",
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
