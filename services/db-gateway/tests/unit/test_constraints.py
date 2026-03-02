"""Tests for optional write constraints on DB Gateway endpoints."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


def _register_agent(client: TestClient, *, name: str) -> str:
    agent_id = f"a-{uuid4()}"
    response = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-03-01T09:00:00Z",
            "event": make_event(),
        },
    )
    assert response.status_code == 201
    return agent_id


def _create_funded_account(client: TestClient, *, name: str, balance: int) -> str:
    agent_id = _register_agent(client, name=name)
    payload: dict[str, Any] = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2026-03-01T09:05:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        payload["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-03-01T09:05:00Z",
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
            "created_at": "2026-03-01T09:10:00Z",
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
            "title": "Constraint test task",
            "spec": "Test optimistic concurrency",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 7200,
            "review_deadline_seconds": 1800,
            "bidding_deadline": "2026-03-01T10:00:00Z",
            "escrow_id": escrow_id,
            "created_at": "2026-03-01T09:12:00Z",
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
class TestConstraintEnforcement:
    """Constraint behavior for task status, escrow, and cross-table writes."""

    def test_update_task_with_valid_constraint(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """Update succeeds when the current row matches constraints."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        response = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "accepted"},
                "constraints": {"status": "open"},
                "event": make_event(source="board", event_type="task.accepted", task_id=task_id),
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

        row = _read_one(db_writer, "SELECT status FROM board_tasks WHERE task_id = ?", (task_id,))
        assert row is not None
        assert row["status"] == "accepted"

    def test_update_task_with_violated_constraint(self, app_with_writer: TestClient) -> None:
        """Update fails with 409 when constraints do not match current state."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        first = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "accepted"},
                "event": make_event(source="board", event_type="task.accepted", task_id=task_id),
            },
        )
        assert first.status_code == 200

        second = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "submitted"},
                "constraints": {"status": "open"},
                "event": make_event(source="board", event_type="task.submitted", task_id=task_id),
            },
        )
        assert second.status_code == 409
        assert second.json()["error"] == "constraint_violation"

    def test_constraint_violation_error_format(self, app_with_writer: TestClient) -> None:
        """Constraint errors include table/field/expected/actual details."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "accepted"},
                "event": make_event(source="board", event_type="task.accepted", task_id=task_id),
            },
        )
        response = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "submitted"},
                "constraints": {"status": "open"},
                "event": make_event(source="board", event_type="task.submitted", task_id=task_id),
            },
        )
        body = response.json()
        assert response.status_code == 409
        assert body["error"] == "constraint_violation"
        assert body["details"]["table"] == "board_tasks"
        assert body["details"]["constraint"] == "status"
        assert body["details"]["expected"] == "open"
        assert body["details"]["actual"] == "accepted"

    def test_update_without_constraints(self, app_with_writer: TestClient) -> None:
        """Existing behavior still works when constraints are omitted."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        response = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "accepted"},
                "event": make_event(source="board", event_type="task.accepted", task_id=task_id),
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

    def test_empty_constraints_object(self, app_with_writer: TestClient) -> None:
        """Empty constraints object is treated as no constraints."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        response = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "accepted"},
                "constraints": {},
                "event": make_event(source="board", event_type="task.accepted", task_id=task_id),
            },
        )
        assert response.status_code == 200

    def test_escrow_release_constraint(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """Escrow release supports row constraints."""
        payer_id = _create_funded_account(app_with_writer, name="Payer", balance=500)
        recipient_id = _create_funded_account(app_with_writer, name="Recipient", balance=0)
        task_id, escrow_id = _create_task(app_with_writer, poster_id=payer_id)

        response = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_id,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-03-01T09:30:00Z",
                "constraints": {"status": "locked"},
                "event": make_event(source="bank", event_type="escrow.released", task_id=task_id),
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "released"

        row = _read_one(
            db_writer,
            "SELECT status FROM bank_escrow WHERE escrow_id = ?",
            (escrow_id,),
        )
        assert row is not None
        assert row["status"] == "released"

    def test_cross_table_bid_constraint(self, app_with_writer: TestClient) -> None:
        """Bid submission can enforce task-state constraints."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        bidder_id = _register_agent(app_with_writer, name="Bidder")
        task_id, _ = _create_task(app_with_writer, poster_id=poster_id)

        app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "accepted"},
                "event": make_event(source="board", event_type="task.accepted", task_id=task_id),
            },
        )

        response = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": f"bid-{uuid4()}",
                "task_id": task_id,
                "bidder_id": bidder_id,
                "proposal": "Late bid",
                "amount": 55,
                "submitted_at": "2026-03-01T09:31:00Z",
                "constraints": {"status": "open"},
                "event": make_event(source="board", event_type="bid.submitted", task_id=task_id),
            },
        )
        assert response.status_code == 409
        assert response.json()["error"] == "constraint_violation"
