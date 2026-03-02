"""Board domain tests — Categories 7-10 (TASK, BID, TSTAT, ASSET)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_agent(
    client: TestClient,
    agent_id: str | None = None,
    name: str = "Test",
) -> str:
    aid = agent_id or f"a-{uuid4()}"
    client.post(
        "/identity/agents",
        json={
            "agent_id": aid,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        },
    )
    return aid


def _setup_funded_account(
    client: TestClient,
    agent_id: str | None = None,
    balance: int = 500,
) -> str:
    aid = _register_agent(client, agent_id)
    data: dict[str, Any] = {
        "account_id": aid,
        "balance": balance,
        "created_at": "2026-02-28T10:00:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        data["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-02-28T10:00:00Z",
        }
    client.post("/bank/accounts", json=data)
    return aid


def _lock_escrow(
    client: TestClient,
    payer_id: str,
    amount: int = 100,
    task_id: str | None = None,
) -> tuple[str, str]:
    tid = task_id or f"t-{uuid4()}"
    eid = f"esc-{uuid4()}"
    client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": eid,
            "payer_account_id": payer_id,
            "amount": amount,
            "task_id": tid,
            "created_at": "2026-02-28T10:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=tid),
        },
    )
    return eid, tid


def _create_task(
    client: TestClient,
    poster_id: str | None = None,
    escrow_id: str | None = None,
    task_id: str | None = None,
) -> tuple[str, str, str]:
    """Create a full task with all prerequisites. Returns (task_id, poster_id, escrow_id)."""
    pid = poster_id or _setup_funded_account(client)
    if escrow_id is None:
        eid, tid = _lock_escrow(client, pid)
    else:
        eid = escrow_id
        tid = task_id or f"t-{uuid4()}"
    actual_tid = task_id or tid
    client.post(
        "/board/tasks",
        json={
            "task_id": actual_tid,
            "poster_id": pid,
            "title": "Test Task",
            "spec": "Build a login page",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 86400,
            "deadline_seconds": 172800,
            "review_deadline_seconds": 43200,
            "bidding_deadline": "2026-03-01T10:00:00Z",
            "escrow_id": eid,
            "created_at": "2026-02-28T10:15:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=actual_tid),
        },
    )
    return actual_tid, pid, eid


def _submit_bid(
    client: TestClient,
    task_id: str,
    bidder_id: str,
    bid_id: str | None = None,
) -> str:
    bid = bid_id or f"bid-{uuid4()}"
    client.post(
        "/board/bids",
        json={
            "bid_id": bid,
            "task_id": task_id,
            "bidder_id": bidder_id,
            "proposal": "I will build it",
            "submitted_at": "2026-02-28T11:00:00Z",
            "event": make_event(source="board", event_type="bid.submitted", task_id=task_id),
        },
    )
    return bid


# ===================================================================
# Category 7: Task Creation (POST /board/tasks)
# ===================================================================


@pytest.mark.unit
class TestTaskCreation:
    """Task creation tests — TASK-01 through TASK-09."""

    def test_create_valid_task(self, app_with_writer: TestClient) -> None:
        """TASK-01: Create a valid task returns 201 with task_id and event_id."""
        pid = _setup_funded_account(app_with_writer)
        eid, tid = _lock_escrow(app_with_writer, pid)

        resp = app_with_writer.post(
            "/board/tasks",
            json={
                "task_id": tid,
                "poster_id": pid,
                "title": "Test Task",
                "spec": "Build a login page",
                "reward": 100,
                "status": "open",
                "bidding_deadline_seconds": 86400,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2026-03-01T10:00:00Z",
                "escrow_id": eid,
                "created_at": "2026-02-28T10:15:00Z",
                "event": make_event(source="board", event_type="task.created", task_id=tid),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "task_id" in data
        assert "event_id" in data
        assert data["task_id"] == tid

    def test_duplicate_task_id_rejected(self, app_with_writer: TestClient) -> None:
        """TASK-02: Duplicate task_id is rejected with 409 task_exists."""
        tid, pid, _eid = _create_task(app_with_writer)

        # Lock another escrow for the second task attempt
        eid2, _ = _lock_escrow(app_with_writer, pid)
        resp = app_with_writer.post(
            "/board/tasks",
            json={
                "task_id": tid,
                "poster_id": pid,
                "title": "Another Task",
                "spec": "Different spec",
                "reward": 200,
                "status": "open",
                "bidding_deadline_seconds": 86400,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2026-03-01T10:00:00Z",
                "escrow_id": eid2,
                "created_at": "2026-02-28T10:15:00Z",
                "event": make_event(source="board", event_type="task.created", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "task_exists"

    def test_fk_violation_poster_id(self, app_with_writer: TestClient) -> None:
        """TASK-03: Foreign key violation on poster_id returns 409."""
        pid = _setup_funded_account(app_with_writer)
        eid, tid = _lock_escrow(app_with_writer, pid)
        fake_poster = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/board/tasks",
            json={
                "task_id": tid,
                "poster_id": fake_poster,
                "title": "Test Task",
                "spec": "Build a login page",
                "reward": 100,
                "status": "open",
                "bidding_deadline_seconds": 86400,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2026-03-01T10:00:00Z",
                "escrow_id": eid,
                "created_at": "2026-02-28T10:15:00Z",
                "event": make_event(source="board", event_type="task.created", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_escrow_id(self, app_with_writer: TestClient) -> None:
        """TASK-04: Foreign key violation on escrow_id returns 409."""
        pid = _setup_funded_account(app_with_writer)
        fake_escrow = f"esc-{uuid4()}"
        tid = f"t-{uuid4()}"

        resp = app_with_writer.post(
            "/board/tasks",
            json={
                "task_id": tid,
                "poster_id": pid,
                "title": "Test Task",
                "spec": "Build a login page",
                "reward": 100,
                "status": "open",
                "bidding_deadline_seconds": 86400,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2026-03-01T10:00:00Z",
                "escrow_id": fake_escrow,
                "created_at": "2026-02-28T10:15:00Z",
                "event": make_event(source="board", event_type="task.created", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_missing_title(self, app_with_writer: TestClient) -> None:
        """TASK-05: Missing title returns 400 missing_field."""
        pid = _setup_funded_account(app_with_writer)
        eid, tid = _lock_escrow(app_with_writer, pid)

        resp = app_with_writer.post(
            "/board/tasks",
            json={
                "task_id": tid,
                "poster_id": pid,
                # title omitted
                "spec": "Build a login page",
                "reward": 100,
                "status": "open",
                "bidding_deadline_seconds": 86400,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2026-03-01T10:00:00Z",
                "escrow_id": eid,
                "created_at": "2026-02-28T10:15:00Z",
                "event": make_event(source="board", event_type="task.created", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_spec(self, app_with_writer: TestClient) -> None:
        """TASK-06: Missing spec returns 400 missing_field."""
        pid = _setup_funded_account(app_with_writer)
        eid, tid = _lock_escrow(app_with_writer, pid)

        resp = app_with_writer.post(
            "/board/tasks",
            json={
                "task_id": tid,
                "poster_id": pid,
                "title": "Test Task",
                # spec omitted
                "reward": 100,
                "status": "open",
                "bidding_deadline_seconds": 86400,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2026-03-01T10:00:00Z",
                "escrow_id": eid,
                "created_at": "2026-02-28T10:15:00Z",
                "event": make_event(source="board", event_type="task.created", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """TASK-07: Missing event returns 400 missing_field."""
        pid = _setup_funded_account(app_with_writer)
        eid, tid = _lock_escrow(app_with_writer, pid)

        resp = app_with_writer.post(
            "/board/tasks",
            json={
                "task_id": tid,
                "poster_id": pid,
                "title": "Test Task",
                "spec": "Build a login page",
                "reward": 100,
                "status": "open",
                "bidding_deadline_seconds": 86400,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2026-03-01T10:00:00Z",
                "escrow_id": eid,
                "created_at": "2026-02-28T10:15:00Z",
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_task_and_event_atomic(self, app_with_writer: TestClient, db_writer: DbWriter) -> None:
        """TASK-08: Task and event are written atomically."""
        tid, _pid, _eid = _create_task(app_with_writer)

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        task_row = conn.execute(
            "SELECT task_id FROM board_tasks WHERE task_id = ?", (tid,)
        ).fetchone()
        assert task_row is not None

        event_row = conn.execute(
            "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'task.created'",
            (tid,),
        ).fetchone()
        assert event_row is not None
        assert event_row[0] == "task.created"
        conn.close()

    def test_negative_reward_rejected(self, app_with_writer: TestClient) -> None:
        """TASK-09: Negative reward is rejected with 400 invalid_amount."""
        pid = _setup_funded_account(app_with_writer)
        eid, tid = _lock_escrow(app_with_writer, pid)

        resp = app_with_writer.post(
            "/board/tasks",
            json={
                "task_id": tid,
                "poster_id": pid,
                "title": "Test Task",
                "spec": "Build a login page",
                "reward": -1,
                "status": "open",
                "bidding_deadline_seconds": 86400,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2026-03-01T10:00:00Z",
                "escrow_id": eid,
                "created_at": "2026-02-28T10:15:00Z",
                "event": make_event(source="board", event_type="task.created", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_amount"


# ===================================================================
# Category 8: Bid Submission (POST /board/bids)
# ===================================================================


@pytest.mark.unit
class TestBidSubmission:
    """Bid submission tests — BID-01 through BID-07."""

    def test_submit_valid_bid(self, app_with_writer: TestClient) -> None:
        """BID-01: Submit a valid bid returns 201 with bid_id and event_id."""
        tid, _pid, _eid = _create_task(app_with_writer)
        bidder = _register_agent(app_with_writer, name="Bob")
        bid_id = f"bid-{uuid4()}"

        resp = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": bid_id,
                "task_id": tid,
                "bidder_id": bidder,
                "proposal": "I will build a responsive login page",
                "submitted_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="board", event_type="bid.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "bid_id" in data
        assert "event_id" in data
        assert data["bid_id"] == bid_id

    def test_duplicate_bid_rejected(self, app_with_writer: TestClient) -> None:
        """BID-02: Duplicate bid (same bidder, same task) is rejected with 409 bid_exists."""
        tid, _pid, _eid = _create_task(app_with_writer)
        bidder = _register_agent(app_with_writer, name="Bob")

        _submit_bid(app_with_writer, tid, bidder)

        resp = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": f"bid-{uuid4()}",
                "task_id": tid,
                "bidder_id": bidder,
                "proposal": "Different proposal",
                "submitted_at": "2026-02-28T11:30:00Z",
                "event": make_event(source="board", event_type="bid.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "bid_exists"

    def test_different_bidders_succeed(self, app_with_writer: TestClient) -> None:
        """BID-03: Different bidders on same task both succeed with 201."""
        tid, _pid, _eid = _create_task(app_with_writer)
        bob = _register_agent(app_with_writer, name="Bob")
        carol = _register_agent(app_with_writer, name="Carol")

        resp1 = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": f"bid-{uuid4()}",
                "task_id": tid,
                "bidder_id": bob,
                "proposal": "Bob proposes",
                "submitted_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="board", event_type="bid.submitted", task_id=tid),
            },
        )
        resp2 = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": f"bid-{uuid4()}",
                "task_id": tid,
                "bidder_id": carol,
                "proposal": "Carol proposes",
                "submitted_at": "2026-02-28T11:05:00Z",
                "event": make_event(source="board", event_type="bid.submitted", task_id=tid),
            },
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201

    def test_fk_violation_task_id(self, app_with_writer: TestClient) -> None:
        """BID-04: Foreign key violation on task_id returns 409."""
        bidder = _register_agent(app_with_writer)
        fake_task = f"t-{uuid4()}"

        resp = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": f"bid-{uuid4()}",
                "task_id": fake_task,
                "bidder_id": bidder,
                "proposal": "I will build it",
                "submitted_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="board", event_type="bid.submitted", task_id=fake_task),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_bidder_id(self, app_with_writer: TestClient) -> None:
        """BID-05: Foreign key violation on bidder_id returns 409."""
        tid, _pid, _eid = _create_task(app_with_writer)
        fake_bidder = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": f"bid-{uuid4()}",
                "task_id": tid,
                "bidder_id": fake_bidder,
                "proposal": "I will build it",
                "submitted_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="board", event_type="bid.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_missing_proposal(self, app_with_writer: TestClient) -> None:
        """BID-06: Missing proposal returns 400 missing_field."""
        tid, _pid, _eid = _create_task(app_with_writer)
        bidder = _register_agent(app_with_writer)

        resp = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": f"bid-{uuid4()}",
                "task_id": tid,
                "bidder_id": bidder,
                # proposal omitted
                "submitted_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="board", event_type="bid.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """BID-07: Missing event returns 400 missing_field."""
        tid, _pid, _eid = _create_task(app_with_writer)
        bidder = _register_agent(app_with_writer)

        resp = app_with_writer.post(
            "/board/bids",
            json={
                "bid_id": f"bid-{uuid4()}",
                "task_id": tid,
                "bidder_id": bidder,
                "proposal": "I will build it",
                "submitted_at": "2026-02-28T11:00:00Z",
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"


# ===================================================================
# Category 9: Task Status Update (POST /board/tasks/{task_id}/status)
# ===================================================================


@pytest.mark.unit
class TestTaskStatusUpdate:
    """Task status update tests — TSTAT-01 through TSTAT-14."""

    def test_update_to_accepted(self, app_with_writer: TestClient) -> None:
        """TSTAT-01: Update task to accepted returns 200 with status 'accepted'."""
        tid, _pid, _eid = _create_task(app_with_writer)
        worker = _register_agent(app_with_writer, name="Bob")
        bid_id = f"bid-{uuid4()}"

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "accepted",
                    "worker_id": worker,
                    "accepted_bid_id": bid_id,
                    "accepted_at": "2026-02-28T12:00:00Z",
                    "execution_deadline": "2026-03-02T06:36:00Z",
                },
                "event": make_event(source="board", event_type="task.accepted", task_id=tid),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert "status" in data
        assert "event_id" in data
        assert data["status"] == "accepted"

    def test_update_to_submitted(self, app_with_writer: TestClient) -> None:
        """TSTAT-02: Update task to submitted returns 200."""
        tid, _pid, _eid = _create_task(app_with_writer)
        worker = _register_agent(app_with_writer, name="Bob")

        # First accept
        app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "accepted",
                    "worker_id": worker,
                    "accepted_bid_id": f"bid-{uuid4()}",
                    "accepted_at": "2026-02-28T12:00:00Z",
                    "execution_deadline": "2026-03-02T06:36:00Z",
                },
                "event": make_event(source="board", event_type="task.accepted", task_id=tid),
            },
        )

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "submitted",
                    "submitted_at": "2026-02-28T14:00:00Z",
                    "review_deadline": "2026-03-01T02:00:00Z",
                },
                "event": make_event(source="board", event_type="task.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"

    def test_update_to_approved(self, app_with_writer: TestClient) -> None:
        """TSTAT-03: Update task to approved returns 200."""
        tid, _pid, _eid = _create_task(app_with_writer)
        worker = _register_agent(app_with_writer, name="Bob")

        # Accept then submit
        app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "accepted",
                    "worker_id": worker,
                    "accepted_bid_id": f"bid-{uuid4()}",
                    "accepted_at": "2026-02-28T12:00:00Z",
                    "execution_deadline": "2026-03-02T06:36:00Z",
                },
                "event": make_event(source="board", event_type="task.accepted", task_id=tid),
            },
        )
        app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "submitted",
                    "submitted_at": "2026-02-28T14:00:00Z",
                    "review_deadline": "2026-03-01T02:00:00Z",
                },
                "event": make_event(source="board", event_type="task.submitted", task_id=tid),
            },
        )

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "approved",
                    "approved_at": "2026-02-28T16:00:00Z",
                },
                "event": make_event(source="board", event_type="task.approved", task_id=tid),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_update_to_cancelled(self, app_with_writer: TestClient) -> None:
        """TSTAT-04: Update task to cancelled returns 200."""
        tid, _pid, _eid = _create_task(app_with_writer)

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "cancelled",
                    "cancelled_at": "2026-02-28T12:00:00Z",
                },
                "event": make_event(source="board", event_type="task.cancelled", task_id=tid),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_update_to_disputed(self, app_with_writer: TestClient) -> None:
        """TSTAT-05: Update task to disputed returns 200."""
        tid, _pid, _eid = _create_task(app_with_writer)
        worker = _register_agent(app_with_writer, name="Bob")

        # Accept then submit
        app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "accepted",
                    "worker_id": worker,
                    "accepted_bid_id": f"bid-{uuid4()}",
                    "accepted_at": "2026-02-28T12:00:00Z",
                    "execution_deadline": "2026-03-02T06:36:00Z",
                },
                "event": make_event(source="board", event_type="task.accepted", task_id=tid),
            },
        )
        app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "submitted",
                    "submitted_at": "2026-02-28T14:00:00Z",
                    "review_deadline": "2026-03-01T02:00:00Z",
                },
                "event": make_event(source="board", event_type="task.submitted", task_id=tid),
            },
        )

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "disputed",
                    "dispute_reason": "The login page does not validate email format",
                    "disputed_at": "2026-02-28T16:00:00Z",
                },
                "event": make_event(source="board", event_type="task.disputed", task_id=tid),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disputed"

    def test_update_to_ruled(self, app_with_writer: TestClient) -> None:
        """TSTAT-06: Update task to ruled returns 200."""
        tid, _pid, _eid = _create_task(app_with_writer)

        # Shortcut: go directly to disputed (gateway does not validate transitions)
        app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "disputed",
                    "dispute_reason": "Bad work",
                    "disputed_at": "2026-02-28T14:00:00Z",
                },
                "event": make_event(source="board", event_type="task.disputed", task_id=tid),
            },
        )

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "ruled",
                    "ruling_id": f"rul-{uuid4()}",
                    "worker_pct": 70,
                    "ruling_summary": "Spec was ambiguous about email validation",
                    "ruled_at": "2026-02-28T18:00:00Z",
                },
                "event": make_event(source="board", event_type="task.ruled", task_id=tid),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ruled"

    def test_update_to_expired(self, app_with_writer: TestClient) -> None:
        """TSTAT-07: Update task to expired returns 200."""
        tid, _pid, _eid = _create_task(app_with_writer)

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "expired",
                    "expired_at": "2026-03-01T10:00:00Z",
                },
                "event": make_event(source="board", event_type="task.expired", task_id=tid),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "expired"

    def test_task_not_found(self, app_with_writer: TestClient) -> None:
        """TSTAT-08: Updating non-existent task returns 404 task_not_found."""
        fake_task = f"t-{uuid4()}"

        resp = app_with_writer.post(
            f"/board/tasks/{fake_task}/status",
            json={
                "updates": {"status": "accepted"},
                "event": make_event(source="board", event_type="task.accepted", task_id=fake_task),
            },
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "task_not_found"

    def test_unknown_column_rejected(self, app_with_writer: TestClient) -> None:
        """TSTAT-09: Unknown column in updates is rejected with 400 invalid_field."""
        tid, _pid, _eid = _create_task(app_with_writer)

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "approved",
                    "nonexistent_column": "value",
                },
                "event": make_event(source="board", event_type="task.approved", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_field"

    def test_empty_updates(self, app_with_writer: TestClient) -> None:
        """TSTAT-10: Empty updates object returns 400 empty_updates."""
        tid, _pid, _eid = _create_task(app_with_writer)

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {},
                "event": make_event(source="board", event_type="task.approved", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "empty_updates"

    def test_missing_updates(self, app_with_writer: TestClient) -> None:
        """TSTAT-11: Missing updates object returns 400 missing_field."""
        tid, _pid, _eid = _create_task(app_with_writer)

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "event": make_event(source="board", event_type="task.approved", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """TSTAT-12: Missing event object returns 400 missing_field."""
        tid, _pid, _eid = _create_task(app_with_writer)

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {"status": "approved"},
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_multiple_fields_updated(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """TSTAT-13: Multiple fields updated in one call are all persisted."""
        tid, _pid, _eid = _create_task(app_with_writer)
        worker = _register_agent(app_with_writer, name="Bob")
        bid_id = f"bid-{uuid4()}"

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "accepted",
                    "worker_id": worker,
                    "accepted_bid_id": bid_id,
                    "accepted_at": "2026-02-28T12:00:00Z",
                    "execution_deadline": "2026-03-02T06:36:00Z",
                },
                "event": make_event(source="board", event_type="task.accepted", task_id=tid),
            },
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT status, worker_id, accepted_bid_id, accepted_at, execution_deadline "
            "FROM board_tasks WHERE task_id = ?",
            (tid,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "accepted"
        assert row[1] == worker
        assert row[2] == bid_id
        assert row[3] == "2026-02-28T12:00:00Z"
        assert row[4] == "2026-03-02T06:36:00Z"

    def test_gateway_does_not_validate_transitions(self, app_with_writer: TestClient) -> None:
        """TSTAT-14: Gateway does not validate status transitions (skip accepted/submitted)."""
        tid, _pid, _eid = _create_task(app_with_writer)

        resp = app_with_writer.post(
            f"/board/tasks/{tid}/status",
            json={
                "updates": {
                    "status": "approved",
                    "approved_at": "2026-02-28T16:00:00Z",
                },
                "event": make_event(source="board", event_type="task.approved", task_id=tid),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"


# ===================================================================
# Category 10: Asset Recording (POST /board/assets)
# ===================================================================


@pytest.mark.unit
class TestAssetRecording:
    """Asset recording tests — ASSET-01 through ASSET-07."""

    def test_record_valid_asset(self, app_with_writer: TestClient) -> None:
        """ASSET-01: Record a valid asset returns 201 with asset_id and event_id."""
        tid, _pid, _eid = _create_task(app_with_writer)
        uploader = _register_agent(app_with_writer, name="Worker")
        asset_id = f"asset-{uuid4()}"

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": asset_id,
                "task_id": tid,
                "uploader_id": uploader,
                "filename": "login-page.zip",
                "content_type": "application/zip",
                "size_bytes": 245760,
                "storage_path": f"data/assets/{tid}/login-page.zip",
                "uploaded_at": "2026-02-28T14:00:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=tid),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "asset_id" in data
        assert "event_id" in data
        assert data["asset_id"] == asset_id

    def test_duplicate_asset_id_rejected(self, app_with_writer: TestClient) -> None:
        """ASSET-02: Duplicate asset_id is rejected with 409 asset_exists."""
        tid, _pid, _eid = _create_task(app_with_writer)
        uploader = _register_agent(app_with_writer, name="Worker")
        asset_id = f"asset-{uuid4()}"

        app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": asset_id,
                "task_id": tid,
                "uploader_id": uploader,
                "filename": "file1.zip",
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": f"data/assets/{tid}/file1.zip",
                "uploaded_at": "2026-02-28T14:00:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=tid),
            },
        )

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": asset_id,
                "task_id": tid,
                "uploader_id": uploader,
                "filename": "file2.zip",
                "content_type": "application/zip",
                "size_bytes": 2048,
                "storage_path": f"data/assets/{tid}/file2.zip",
                "uploaded_at": "2026-02-28T14:30:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "asset_exists"

    def test_fk_violation_task_id(self, app_with_writer: TestClient) -> None:
        """ASSET-03: Foreign key violation on task_id returns 409."""
        uploader = _register_agent(app_with_writer)
        fake_task = f"t-{uuid4()}"

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": f"asset-{uuid4()}",
                "task_id": fake_task,
                "uploader_id": uploader,
                "filename": "file.zip",
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": f"data/assets/{fake_task}/file.zip",
                "uploaded_at": "2026-02-28T14:00:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=fake_task),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_uploader_id(self, app_with_writer: TestClient) -> None:
        """ASSET-04: Foreign key violation on uploader_id returns 409."""
        tid, _pid, _eid = _create_task(app_with_writer)
        fake_uploader = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": f"asset-{uuid4()}",
                "task_id": tid,
                "uploader_id": fake_uploader,
                "filename": "file.zip",
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": f"data/assets/{tid}/file.zip",
                "uploaded_at": "2026-02-28T14:00:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_missing_filename(self, app_with_writer: TestClient) -> None:
        """ASSET-05: Missing filename returns 400 missing_field."""
        tid, _pid, _eid = _create_task(app_with_writer)
        uploader = _register_agent(app_with_writer)

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": f"asset-{uuid4()}",
                "task_id": tid,
                "uploader_id": uploader,
                # filename omitted
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": f"data/assets/{tid}/file.zip",
                "uploaded_at": "2026-02-28T14:00:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """ASSET-06: Missing event returns 400 missing_field."""
        tid, _pid, _eid = _create_task(app_with_writer)
        uploader = _register_agent(app_with_writer)

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": f"asset-{uuid4()}",
                "task_id": tid,
                "uploader_id": uploader,
                "filename": "file.zip",
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": f"data/assets/{tid}/file.zip",
                "uploaded_at": "2026-02-28T14:00:00Z",
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_multiple_assets_same_task(self, app_with_writer: TestClient) -> None:
        """ASSET-07: Multiple assets for the same task succeed."""
        tid, _pid, _eid = _create_task(app_with_writer)
        uploader = _register_agent(app_with_writer, name="Worker")

        resp1 = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": f"asset-{uuid4()}",
                "task_id": tid,
                "uploader_id": uploader,
                "filename": "file1.zip",
                "content_type": "application/zip",
                "size_bytes": 1024,
                "storage_path": f"data/assets/{tid}/file1.zip",
                "uploaded_at": "2026-02-28T14:00:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=tid),
            },
        )
        resp2 = app_with_writer.post(
            "/board/assets",
            json={
                "asset_id": f"asset-{uuid4()}",
                "task_id": tid,
                "uploader_id": uploader,
                "filename": "file2.zip",
                "content_type": "application/zip",
                "size_bytes": 2048,
                "storage_path": f"data/assets/{tid}/file2.zip",
                "uploaded_at": "2026-02-28T14:30:00Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=tid),
            },
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201
