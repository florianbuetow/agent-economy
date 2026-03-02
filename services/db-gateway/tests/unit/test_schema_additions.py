"""Tests for DB schema additions in board/court domains."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


def _register_agent(client: TestClient, name: str = "Agent") -> str:
    agent_id = f"a-{uuid4()}"
    response = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-03-01T10:00:00Z",
            "event": make_event(),
        },
    )
    assert response.status_code == 201
    return agent_id


def _create_funded_account(
    client: TestClient,
    *,
    name: str,
    balance: int,
) -> str:
    agent_id = _register_agent(client, name=name)
    payload: dict[str, Any] = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2026-03-01T10:05:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        payload["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-03-01T10:05:00Z",
        }

    response = client.post("/bank/accounts", json=payload)
    assert response.status_code == 201
    return agent_id


def _create_task(client: TestClient, *, poster_id: str) -> tuple[str, str]:
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    lock_response = client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": 100,
            "task_id": task_id,
            "created_at": "2026-03-01T10:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=task_id),
        },
    )
    assert lock_response.status_code == 201

    task_response = client.post(
        "/board/tasks",
        json={
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "Schema test task",
            "spec": "Validate schema additions",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 7200,
            "review_deadline_seconds": 1800,
            "bidding_deadline": "2026-03-01T11:00:00Z",
            "escrow_id": escrow_id,
            "created_at": "2026-03-01T10:12:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=task_id),
        },
    )
    assert task_response.status_code == 201
    return task_id, escrow_id


def _read_one(db_writer: DbWriter, query: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
    conn = sqlite3.connect(db_writer._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(query, params).fetchone()
    conn.close()
    return row


@pytest.mark.unit
class TestSchemaAdditions:
    """Validate new nullable/default columns introduced for DB gateway parity."""

    def test_submit_bid_with_amount(self, app_with_writer: TestClient, db_writer: DbWriter) -> None:
        """board_bids accepts amount and persists it."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        bidder_id = _register_agent(app_with_writer, name="Bidder")
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        bid_id = f"bid-{uuid4()}"
        response = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": bid_id,
                "task_id": task_id,
                "bidder_id": bidder_id,
                "proposal": "Implement with tests",
                "amount": 125,
                "submitted_at": "2026-03-01T10:20:00Z",
                "event": make_event(source="board", event_type="bid.submitted", task_id=task_id),
            },
        )
        assert response.status_code == 201

        row = _read_one(
            db_writer,
            "SELECT amount FROM board_bids WHERE bid_id = ?",
            (bid_id,),
        )
        assert row is not None
        assert row["amount"] == 125

    def test_submit_bid_without_amount_uses_zero(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """board_bids keeps compatibility when amount is omitted."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        bidder_id = _register_agent(app_with_writer, name="Bidder")
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        bid_id = f"bid-{uuid4()}"
        response = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": bid_id,
                "task_id": task_id,
                "bidder_id": bidder_id,
                "proposal": "Compatible bid payload",
                "submitted_at": "2026-03-01T10:21:00Z",
                "event": make_event(source="board", event_type="bid.submitted", task_id=task_id),
            },
        )
        assert response.status_code == 201

        row = _read_one(
            db_writer,
            "SELECT amount FROM board_bids WHERE bid_id = ?",
            (bid_id,),
        )
        assert row is not None
        assert row["amount"] == 0

    def test_record_asset_with_content_hash(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """board_assets accepts optional content_hash."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        asset_id = f"asset-{uuid4()}"
        response = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": asset_id,
                "task_id": task_id,
                "uploader_id": poster_id,
                "filename": "artifact.zip",
                "content_type": "application/zip",
                "size_bytes": 2048,
                "storage_path": "/tmp/artifact.zip",
                "content_hash": "sha256:abc123",
                "uploaded_at": "2026-03-01T10:25:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=task_id),
            },
        )
        assert response.status_code == 201

        row = _read_one(
            db_writer,
            "SELECT content_hash FROM board_assets WHERE asset_id = ?",
            (asset_id,),
        )
        assert row is not None
        assert row["content_hash"] == "sha256:abc123"

    def test_record_asset_without_content_hash(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """board_assets stores NULL when content_hash is omitted."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        asset_id = f"asset-{uuid4()}"
        response = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": asset_id,
                "task_id": task_id,
                "uploader_id": poster_id,
                "filename": "artifact.zip",
                "content_type": "application/zip",
                "size_bytes": 2048,
                "storage_path": "/tmp/artifact.zip",
                "uploaded_at": "2026-03-01T10:26:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=task_id),
            },
        )
        assert response.status_code == 201

        row = _read_one(
            db_writer,
            "SELECT content_hash FROM board_assets WHERE asset_id = ?",
            (asset_id,),
        )
        assert row is not None
        assert row["content_hash"] is None

    def test_create_task_defaults_bid_count_and_escrow_pending(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """board_tasks stores default bid_count/escrow_pending values."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        row = _read_one(
            db_writer,
            "SELECT bid_count, escrow_pending FROM board_tasks WHERE task_id = ?",
            (task_id,),
        )
        assert row is not None
        assert row["bid_count"] == 0
        assert row["escrow_pending"] == 0

    def test_file_claim_with_rebuttal_deadline(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """court_claims accepts and persists rebuttal_deadline."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        worker_id = _register_agent(app_with_writer, name="Worker")
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        claim_id = f"clm-{uuid4()}"
        response = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": task_id,
                "claimant_id": poster_id,
                "respondent_id": worker_id,
                "reason": "Deliverable mismatch",
                "status": "filed",
                "rebuttal_deadline": "2026-03-02T06:30:00Z",
                "filed_at": "2026-03-01T10:30:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
            },
        )
        assert response.status_code == 201

        row = _read_one(
            db_writer,
            "SELECT rebuttal_deadline FROM court_claims WHERE claim_id = ?",
            (claim_id,),
        )
        assert row is not None
        assert row["rebuttal_deadline"] == "2026-03-02T06:30:00Z"
