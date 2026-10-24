"""Board domain tests — Categories 7-10 (TASK, BID, TSTAT, ASSET)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from service_auth.financial_identity import operation_id_for

from db_gateway_service.core.state import get_app_state
from tests.conftest import make_event
from tests.fakes.native_board_history import (
    ACCEPTANCE_CUTOFF_US,
    BIDDING_CUTOFF_US,
    FIXED_NOW_TEXT,
    FIXED_NOW_US,
    BusinessClock,
    TaskGraph,
    accept_bid,
    create_account,
    create_task,
    file_claim,
    finalize,
    lock_escrow,
    operation_body,
    prepare_submitted,
    publish_task,
    register_agent,
    register_worker,
    settle_release,
    settle_split,
    submit_bid,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fixed_gateway_business_clock(monkeypatch: pytest.MonkeyPatch) -> BusinessClock:
    """Hold lifecycle time fixed without changing real JWT signing time."""
    clock = BusinessClock()
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock)
    return clock


def _register_agent(
    client: TestClient,
    agent_id: str | None = None,
    name: str = "Test",
) -> str:
    return register_agent(client, agent_id=agent_id, name=name)


def _setup_funded_account(
    client: TestClient,
    agent_id: str | None = None,
    balance: int = 500,
) -> str:
    account_id = _register_agent(client, agent_id)
    create_account(client, account_id, balance=balance)
    return account_id


def _lock_escrow(
    client: TestClient,
    payer_id: str,
    amount: int = 100,
    task_id: str | None = None,
) -> tuple[str, str]:
    return lock_escrow(client, payer_id, task_id=task_id, reward=amount)


def _create_task(
    client: TestClient,
    poster_id: str | None = None,
    escrow_id: str | None = None,
    task_id: str | None = None,
) -> tuple[str, str, str]:
    """Create a full task with all prerequisites. Returns (task_id, poster_id, escrow_id)."""
    pid = poster_id or _setup_funded_account(client)
    eid = escrow_id or f"esc-{uuid4()}"
    actual_tid = task_id or f"t-{uuid4()}"
    publish_task(
        client,
        task_id=actual_tid,
        poster_id=pid,
        escrow_id=eid,
        title="Test Task",
        spec="Build a login page",
        reward=100,
        bidding_deadline_seconds=43200,
        acceptance_window_seconds=3600,
        execution_deadline_seconds=172800,
        review_deadline_seconds=43200,
        lock_timestamp="2099-01-01T11:59:02Z",
    )
    return actual_tid, pid, eid


def _create_accepted_asset_task(client: TestClient) -> tuple[str, str]:
    task_id, poster_id, escrow_id = _create_task(client)
    worker_id = _register_agent(client, name="Worker")
    graph = TaskGraph(task_id=task_id, poster_id=poster_id, escrow_id=escrow_id)
    bid_id, bid = submit_bid(client, graph, worker_id, amount=50)
    assert bid.status_code == 200, bid.text
    accepted = accept_bid(client, graph, worker_id, bid_id, bid_amount=50)
    assert accepted.status_code == 200, accepted.text
    return task_id, worker_id


def _cancel_command(graph: TaskGraph) -> dict[str, object]:
    """Build one valid native cancellation for structural-negative tests."""
    return operation_body(
        operation_id=f"op-{uuid4()}",
        task_id=graph.task_id,
        command="cancel_task",
        expected_status="open",
        expected_lifecycle_version=0,
        actor_kind="agent",
        actor_id=graph.poster_id,
        arguments={
            "poster_id": graph.poster_id,
            "escrow_id": graph.escrow_id,
            "reward": 100,
            "source_reference": f"task:{graph.task_id}:cancelled",
            "cutoff_us": BIDDING_CUTOFF_US,
        },
    )


def _database_snapshot(writer: DbWriter) -> tuple[str, ...]:
    """Capture all durable rows so rejection tests prove atomic no-op behavior."""
    return tuple(writer.connection.iterdump())


def _native_publication_payload(
    *,
    task_id: str,
    poster_id: str,
    operation_id: str,
    title: str,
    spec: str,
    reward: int,
) -> dict[str, object]:
    """Build a valid native publication intent with ordinary task terms."""
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": task_id,
        "actor_kind": "agent",
        "actor_id": poster_id,
        "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
        "title": title,
        "spec": spec,
        "reward": reward,
        "bidding_deadline_seconds": 43200,
        "acceptance_window_seconds": 3600,
        "execution_deadline_seconds": 172800,
        "review_deadline_seconds": 43200,
    }


# ===================================================================
# Category 7: Task Creation (POST /board/tasks)
# ===================================================================


@pytest.mark.unit
class TestTaskCreation:
    """Task creation tests — TASK-01 through TASK-09."""

    def test_create_valid_task(
        self,
        app_with_writer: TestClient,
    ) -> None:
        """TASK-01: Native publication returns task and event facts with exact cutoffs."""
        pid = _setup_funded_account(app_with_writer)
        tid = f"t-{uuid4()}"
        eid = f"esc-{uuid4()}"

        resp = publish_task(
            app_with_writer,
            task_id=tid,
            poster_id=pid,
            escrow_id=eid,
            title="Test Task",
            spec="Build a login page",
            reward=100,
            bidding_deadline_seconds=43200,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=172800,
            review_deadline_seconds=43200,
            lock_timestamp=FIXED_NOW_TEXT,
        )
        assert resp.status_code == 201
        envelope = resp.json()
        assert envelope["publication_state"] == "published"
        data = envelope["result"]
        assert data is not None
        assert "task_id" in data
        assert "event_id" in data
        assert data["task_id"] == tid
        assert tuple(
            data[field]
            for field in (
                "lifecycle_version",
                "timing_contract_version",
                "acceptance_window_seconds",
                "bidding_cutoff_us",
                "acceptance_cutoff_us",
            )
        ) == (
            0,
            2,
            3600,
            BIDDING_CUTOFF_US,
            ACCEPTANCE_CUTOFF_US,
        )

    def test_duplicate_task_id_rejected(self, app_with_writer: TestClient) -> None:
        """TASK-02: A fresh intent cannot reuse an already published task ID."""
        tid, pid, _eid = _create_task(app_with_writer)
        writer = get_app_state().db_writer
        assert writer is not None
        before = _database_snapshot(writer)
        duplicate = _native_publication_payload(
            task_id=tid,
            poster_id=pid,
            operation_id=operation_id_for(
                "gateway-native-http-fixture",
                (tid, "duplicate-attempt"),
            ),
            title="Another Task",
            spec="Different spec",
            reward=200,
        )
        response = app_with_writer.post("/board/task-publications", json=duplicate)
        assert response.status_code == 409
        assert response.json()["error"] == "task_id_conflict"
        assert _database_snapshot(writer) == before

    def test_fk_violation_poster_id(self, app_with_writer: TestClient) -> None:
        """TASK-03: An unregistered publication actor returns 409 foreign_key_violation."""
        tid = f"t-{uuid4()}"
        fake_poster = f"a-{uuid4()}"
        writer = get_app_state().db_writer
        assert writer is not None
        before = _database_snapshot(writer)
        payload = _native_publication_payload(
            task_id=tid,
            poster_id=fake_poster,
            operation_id=operation_id_for(
                "gateway-native-http-fixture",
                (tid, "unregistered-actor"),
            ),
            title="Test Task",
            spec="Build a login page",
            reward=100,
        )
        response = app_with_writer.post("/board/task-publications", json=payload)
        assert response.status_code == 409
        assert response.json()["error"] == "foreign_key_violation"
        assert _database_snapshot(writer) == before

    def test_supplied_escrow_id_is_rejected_before_publication(
        self,
        app_with_writer: TestClient,
    ) -> None:
        """TASK-04: Caller-supplied escrow_id is forbidden on native publication."""
        pid = _setup_funded_account(app_with_writer)
        fake_escrow = f"esc-{uuid4()}"
        tid = f"t-{uuid4()}"
        payload = _native_publication_payload(
            task_id=tid,
            poster_id=pid,
            operation_id=operation_id_for(
                "gateway-native-http-fixture",
                (tid, "unknown-escrow"),
            ),
            title="Test Task",
            spec="Build a login page",
            reward=100,
        )
        payload["escrow_id"] = fake_escrow
        writer = get_app_state().db_writer
        assert writer is not None
        before = _database_snapshot(writer)
        response = app_with_writer.post("/board/task-publications", json=payload)
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _database_snapshot(writer) == before

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
                "bidding_deadline_seconds": 43200,
                "acceptance_window_seconds": 3600,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2099-01-02T00:00:00Z",
                "escrow_id": eid,
                "created_at": "2099-01-01T12:00:00Z",
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
                "bidding_deadline_seconds": 43200,
                "acceptance_window_seconds": 3600,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2099-01-02T00:00:00Z",
                "escrow_id": eid,
                "created_at": "2099-01-01T12:00:00Z",
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
                "bidding_deadline_seconds": 43200,
                "acceptance_window_seconds": 3600,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2099-01-02T00:00:00Z",
                "escrow_id": eid,
                "created_at": "2099-01-01T12:00:00Z",
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
                "bidding_deadline_seconds": 43200,
                "acceptance_window_seconds": 3600,
                "deadline_seconds": 172800,
                "review_deadline_seconds": 43200,
                "bidding_deadline": "2099-01-02T00:00:00Z",
                "escrow_id": eid,
                "created_at": "2099-01-01T12:00:00Z",
                "event": make_event(source="board", event_type="task.created", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_amount"


# ===================================================================
# Category 8: Bid Submission (POST /board/task-operations)
# ===================================================================


@pytest.mark.unit
class TestBidSubmission:
    """Native bid submission tests — reconciled BID-01 through BID-07."""

    def test_submit_valid_bid(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """BID-01: Native bid commits one operation, bid, counter and event."""
        graph = create_task(app_with_writer)
        bidder = register_agent(app_with_writer, name="Bob")

        bid_id, response = submit_bid(app_with_writer, graph, bidder)

        assert response.status_code == 200
        envelope = response.json()
        assert envelope["operation_state"] == "completed"
        assert envelope["command"] == "submit_bid"
        assert envelope["result"]["bid_id"] == bid_id
        bid = db_writer.connection.execute(
            "SELECT task_id,bidder_id,amount FROM board_bids WHERE bid_id=?",
            (bid_id,),
        ).fetchone()
        event = db_writer.connection.execute(
            "SELECT event_type,task_id,agent_id FROM events WHERE event_id=?",
            (envelope["event_id"],),
        ).fetchone()
        task = db_writer.connection.execute(
            "SELECT bid_count FROM board_tasks WHERE task_id=?",
            (graph.task_id,),
        ).fetchone()
        assert bid is not None and tuple(bid) == (graph.task_id, bidder, 90)
        assert event is not None and tuple(event) == ("bid.submitted", graph.task_id, bidder)
        assert task is not None and tuple(task) == (1,)

    def test_duplicate_bid_rejected(self, app_with_writer: TestClient) -> None:
        """BID-02: A second bid from the same worker is still `bid_exists`."""
        graph = create_task(app_with_writer)
        bidder = register_agent(app_with_writer, name="Bob")
        _bid_id, first = submit_bid(app_with_writer, graph, bidder)
        assert first.status_code == 200

        _duplicate_id, duplicate = submit_bid(app_with_writer, graph, bidder)

        assert duplicate.status_code == 409
        assert duplicate.json()["error"] == "bid_exists"

    def test_different_bidders_succeed(self, app_with_writer: TestClient) -> None:
        """BID-03: Different registered bidders complete independent operations."""
        graph = create_task(app_with_writer)
        bob = register_agent(app_with_writer, name="Bob")
        carol = register_agent(app_with_writer, name="Carol")

        _bob_bid, bob_response = submit_bid(app_with_writer, graph, bob)
        _carol_bid, carol_response = submit_bid(app_with_writer, graph, carol)

        assert bob_response.status_code == 200
        assert carol_response.status_code == 200

    def test_fk_violation_task_id(self, app_with_writer: TestClient) -> None:
        """BID-04: A native bid for an absent task returns `task_not_found`."""
        bidder = register_agent(app_with_writer)
        missing_task_id = f"t-{uuid4()}"
        response = app_with_writer.post(
            "/board/task-operations",
            json=operation_body(
                operation_id=f"op-{uuid4()}",
                task_id=missing_task_id,
                command="submit_bid",
                expected_status="open",
                expected_lifecycle_version=0,
                actor_kind="agent",
                actor_id=bidder,
                arguments={
                    "bid_id": f"bid-{uuid4()}",
                    "bidder_id": bidder,
                    "amount": 90,
                },
            ),
        )

        assert response.status_code == 404
        assert response.json()["error"] == "task_not_found"

    def test_fk_violation_bidder_id(self, app_with_writer: TestClient) -> None:
        """BID-05: An unknown bidder retains the real foreign-key conflict."""
        graph = create_task(app_with_writer)
        unknown_bidder = f"a-{uuid4()}"

        _bid_id, response = submit_bid(app_with_writer, graph, unknown_bidder)

        assert response.status_code == 409
        assert response.json()["error"] == "foreign_key_violation"

    def test_missing_proposal(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """BID-06: Missing native `amount` is structurally rejected."""
        graph = create_task(app_with_writer)
        bidder = register_agent(app_with_writer)
        payload = operation_body(
            operation_id=f"op-{uuid4()}",
            task_id=graph.task_id,
            command="submit_bid",
            expected_status="open",
            expected_lifecycle_version=0,
            actor_kind="agent",
            actor_id=bidder,
            arguments={"bid_id": f"bid-{uuid4()}", "bidder_id": bidder},
        )

        before = _database_snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _database_snapshot(db_writer) == before

    def test_missing_event(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """BID-07: Obsolete caller events cannot enter the native envelope."""
        graph = create_task(app_with_writer)
        bidder = register_agent(app_with_writer)
        payload = operation_body(
            operation_id=f"op-{uuid4()}",
            task_id=graph.task_id,
            command="submit_bid",
            expected_status="open",
            expected_lifecycle_version=0,
            actor_kind="agent",
            actor_id=bidder,
            arguments={
                "bid_id": f"bid-{uuid4()}",
                "bidder_id": bidder,
                "amount": 90,
            },
        )
        payload["event"] = make_event(
            source="board",
            event_type="bid.submitted",
            task_id=graph.task_id,
        )

        before = _database_snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _database_snapshot(db_writer) == before


# ===================================================================
# Category 9: Native Task Lifecycle (POST /board/task-operations)
# ===================================================================


@pytest.mark.unit
class TestTaskStatusUpdate:
    """Native task lifecycle tests — reconciled TSTAT-01 through TSTAT-14."""

    def test_update_to_accepted(self, app_with_writer: TestClient) -> None:
        """TSTAT-01: Acceptance completes atomically from a persisted bid."""
        graph = create_task(app_with_writer)
        worker = register_worker(app_with_writer, name="Bob")
        bid_id, bid = submit_bid(app_with_writer, graph, worker, amount=50)
        assert bid.status_code == 200

        response = accept_bid(app_with_writer, graph, worker, bid_id, bid_amount=50)

        assert response.status_code == 200
        envelope = response.json()
        assert isinstance(envelope["event_id"], int)
        result = envelope["result"]
        assert result["status"] == "accepted"
        assert result["worker_id"] == worker
        assert result["bid_amount"] == 50
        assert result["reward"] == 100

    def test_update_to_submitted(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-02: Submission follows a real bid, acceptance and asset."""
        graph = create_task(app_with_writer)

        worker, _bid_id = prepare_submitted(app_with_writer, graph)

        row = db_writer.connection.execute(
            "SELECT status,worker_id,submitted_at FROM board_tasks WHERE task_id=?",
            (graph.task_id,),
        ).fetchone()
        contract = db_writer.connection.execute(
            "SELECT lifecycle_version,review_cutoff_us,accepted_reward,accepted_bid_amount "
            "FROM board_task_contracts WHERE task_id=?",
            (graph.task_id,),
        ).fetchone()
        assert row is not None and tuple(row) == (
            "submitted",
            worker,
            "2099-01-01T12:00:00Z",
        )
        assert contract is not None and tuple(contract) == (
            2,
            FIXED_NOW_US + 43_200 * 1_000_000,
            100,
            90,
        )

    def test_update_to_approved(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-03: Approval finalizes only after a matching Bank receipt."""
        graph = create_task(app_with_writer)
        worker, _bid_id = prepare_submitted(app_with_writer, graph)
        operation_id = f"op-{uuid4()}"
        source_reference = f"task:{graph.task_id}:approved"
        command = operation_body(
            operation_id=operation_id,
            task_id=graph.task_id,
            command="approve_task",
            expected_status="submitted",
            expected_lifecycle_version=2,
            actor_kind="agent",
            actor_id=graph.poster_id,
            arguments={
                "poster_id": graph.poster_id,
                "worker_id": worker,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": source_reference,
            },
        )

        pending = app_with_writer.post("/board/task-operations", json=command)
        release = settle_release(
            app_with_writer,
            graph,
            operation_id=operation_id,
            recipient_id=worker,
            source_reference=source_reference,
        )
        completed = finalize(app_with_writer, operation_id)

        assert pending.status_code == 202
        assert release.status_code == 200
        assert completed.status_code == 200
        assert completed.json()["result"]["status"] == "approved"
        receipt = db_writer.connection.execute(
            "SELECT operation_kind,source_reference,escrow_id FROM bank_operation_receipts "
            "WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        escrow = db_writer.connection.execute(
            "SELECT status,resolved_at FROM bank_escrow WHERE escrow_id=?",
            (graph.escrow_id,),
        ).fetchone()
        worker_balance = db_writer.connection.execute(
            "SELECT balance FROM bank_accounts WHERE account_id=?",
            (worker,),
        ).fetchone()
        assert receipt is not None and tuple(receipt) == (
            "escrow_release",
            source_reference,
            graph.escrow_id,
        )
        assert escrow is not None and tuple(escrow) == (
            "released",
            "2099-01-01T12:00:02Z",
        )
        assert worker_balance is not None and tuple(worker_balance) == (100,)

    def test_update_to_cancelled(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-04: Cancellation refunds the poster through a real receipt."""
        graph = create_task(app_with_writer)
        operation_id = f"op-{uuid4()}"
        source_reference = f"task:{graph.task_id}:cancelled"
        command = operation_body(
            operation_id=operation_id,
            task_id=graph.task_id,
            command="cancel_task",
            expected_status="open",
            expected_lifecycle_version=0,
            actor_kind="agent",
            actor_id=graph.poster_id,
            arguments={
                "poster_id": graph.poster_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": source_reference,
                "cutoff_us": BIDDING_CUTOFF_US,
            },
        )

        pending = app_with_writer.post("/board/task-operations", json=command)
        release = settle_release(
            app_with_writer,
            graph,
            operation_id=operation_id,
            recipient_id=graph.poster_id,
            source_reference=source_reference,
        )
        completed = finalize(app_with_writer, operation_id)

        assert pending.status_code == 202
        assert release.status_code == 200
        assert completed.status_code == 200
        assert completed.json()["result"]["status"] == "cancelled"
        balance = db_writer.connection.execute(
            "SELECT balance FROM bank_accounts WHERE account_id=?",
            (graph.poster_id,),
        ).fetchone()
        assert balance is not None and tuple(balance) == (500,)

    def test_update_to_disputed(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-05: Dispute finalizes from a matching persisted Court claim."""
        graph = create_task(app_with_writer)
        worker, _bid_id = prepare_submitted(app_with_writer, graph)
        reason = "The login page does not validate email format"
        operation_id = f"op-{uuid4()}"
        command = operation_body(
            operation_id=operation_id,
            task_id=graph.task_id,
            command="dispute_task",
            expected_status="submitted",
            expected_lifecycle_version=2,
            actor_kind="agent",
            actor_id=graph.poster_id,
            arguments={
                "poster_id": graph.poster_id,
                "worker_id": worker,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "reason": reason,
            },
        )
        claim_id = f"clm-{uuid4()}"

        pending = app_with_writer.post("/board/task-operations", json=command)
        claim = file_claim(
            app_with_writer,
            graph,
            worker_id=worker,
            claim_id=claim_id,
            reason=reason,
        )
        completed = finalize(app_with_writer, operation_id)

        assert pending.status_code == 202
        assert claim.status_code == 201
        assert completed.status_code == 200
        assert completed.json()["result"]["status"] == "disputed"
        row = db_writer.connection.execute(
            "SELECT status,dispute_reason,dispute_id FROM board_tasks WHERE task_id=?",
            (graph.task_id,),
        ).fetchone()
        assert row is not None and tuple(row) == ("disputed", reason, claim_id)

    def test_update_to_ruled(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-06: Ruling uses configured platform authority and split receipt."""
        graph = create_task(app_with_writer)
        worker, _bid_id = prepare_submitted(app_with_writer, graph)
        reason = "Bad work"
        dispute_operation_id = f"op-{uuid4()}"
        dispute = operation_body(
            operation_id=dispute_operation_id,
            task_id=graph.task_id,
            command="dispute_task",
            expected_status="submitted",
            expected_lifecycle_version=2,
            actor_kind="agent",
            actor_id=graph.poster_id,
            arguments={
                "poster_id": graph.poster_id,
                "worker_id": worker,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "reason": reason,
            },
        )
        claim_id = f"clm-{uuid4()}"
        assert app_with_writer.post("/board/task-operations", json=dispute).status_code == 202
        claim = file_claim(
            app_with_writer,
            graph,
            worker_id=worker,
            claim_id=claim_id,
            reason=reason,
        )
        assert claim.status_code == 201
        assert finalize(app_with_writer, dispute_operation_id).status_code == 200
        authority = app_with_writer.app.state.board_authority
        platform_id = register_agent(
            app_with_writer,
            name="Economic Platform",
            public_key=authority.platform_public_key,
        )
        ruling_operation_id = f"op-{uuid4()}"
        source_reference = f"task:{graph.task_id}:ruling:{claim_id}"
        ruling = operation_body(
            operation_id=ruling_operation_id,
            task_id=graph.task_id,
            command="record_ruling",
            expected_status="disputed",
            expected_lifecycle_version=3,
            actor_kind="platform",
            actor_id=platform_id,
            arguments={
                "poster_id": graph.poster_id,
                "worker_id": worker,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "ruling_id": claim_id,
                "worker_pct": 70,
                "summary": "Spec was ambiguous about email validation",
                "source_reference": source_reference,
            },
        )

        pending = app_with_writer.post("/board/task-operations", json=ruling)
        split = settle_split(
            app_with_writer,
            graph,
            operation_id=ruling_operation_id,
            worker_id=worker,
            worker_pct=70,
            source_reference=source_reference,
        )
        completed = finalize(app_with_writer, ruling_operation_id)

        assert pending.status_code == 202
        assert split.status_code == 200
        assert completed.status_code == 200
        assert completed.json()["result"]["status"] == "ruled"
        assert completed.json()["result"]["worker_pct"] == 70
        task = db_writer.connection.execute(
            "SELECT status,ruling_id,worker_pct,ruling_summary FROM board_tasks WHERE task_id=?",
            (graph.task_id,),
        ).fetchone()
        assert task is not None and tuple(task) == (
            "ruled",
            claim_id,
            70,
            "Spec was ambiguous about email validation",
        )

    def test_update_to_expired(
        self,
        app_with_writer: TestClient,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        """TSTAT-07: No-bid expiry becomes eligible exactly at its cutoff."""
        graph = create_task(app_with_writer)
        _fixed_gateway_business_clock.now_us = BIDDING_CUTOFF_US
        authority = app_with_writer.app.state.board_authority
        operation_id = f"op-{uuid4()}"
        source_reference = f"task:{graph.task_id}:expired"
        command = operation_body(
            operation_id=operation_id,
            task_id=graph.task_id,
            command="expire_bidding",
            expected_status="open",
            expected_lifecycle_version=0,
            actor_kind="system",
            actor_id=authority.deadline_actor_id,
            arguments={
                "poster_id": graph.poster_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": source_reference,
                "cutoff_us": BIDDING_CUTOFF_US,
            },
        )

        pending = app_with_writer.post("/board/task-operations", json=command)
        release = settle_release(
            app_with_writer,
            graph,
            operation_id=operation_id,
            recipient_id=graph.poster_id,
            source_reference=source_reference,
        )
        completed = finalize(app_with_writer, operation_id)

        assert pending.status_code == 202
        assert release.status_code == 200
        assert completed.status_code == 200
        assert completed.json()["result"]["status"] == "expired"

    def test_task_not_found(self, app_with_writer: TestClient) -> None:
        """TSTAT-08: A valid native command retains `task_not_found`."""
        actor_id = register_agent(app_with_writer)
        missing_task_id = f"t-{uuid4()}"
        response = app_with_writer.post(
            "/board/task-operations",
            json=operation_body(
                operation_id=f"op-{uuid4()}",
                task_id=missing_task_id,
                command="cancel_task",
                expected_status="open",
                expected_lifecycle_version=0,
                actor_kind="agent",
                actor_id=actor_id,
                arguments={
                    "poster_id": actor_id,
                    "escrow_id": f"esc-{uuid4()}",
                    "reward": 100,
                    "source_reference": f"task:{missing_task_id}:cancelled",
                    "cutoff_us": BIDDING_CUTOFF_US,
                },
            ),
        )

        assert response.status_code == 404
        assert response.json()["error"] == "task_not_found"

    def test_unknown_column_rejected(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-09: Unknown native arguments are rejected before mutation."""
        graph = create_task(app_with_writer)
        payload = _cancel_command(graph)
        arguments = payload["arguments"]
        assert isinstance(arguments, dict)
        arguments["nonexistent_column"] = "value"

        before = _database_snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _database_snapshot(db_writer) == before

    def test_empty_updates(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-10: Empty native arguments are rejected structurally."""
        graph = create_task(app_with_writer)
        payload = _cancel_command(graph)
        payload["arguments"] = {}

        before = _database_snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _database_snapshot(db_writer) == before

    def test_missing_updates(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-11: Missing native arguments are rejected structurally."""
        graph = create_task(app_with_writer)
        payload = _cancel_command(graph)
        del payload["arguments"]

        before = _database_snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _database_snapshot(db_writer) == before

    def test_missing_event(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-12: Obsolete caller events cannot enter the native envelope."""
        graph = create_task(app_with_writer)
        payload = _cancel_command(graph)
        payload["event"] = make_event(
            source="board",
            event_type="task.cancelled",
            task_id=graph.task_id,
        )

        before = _database_snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _database_snapshot(db_writer) == before

    def test_multiple_fields_updated(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-13: Native acceptance persists its complete frozen state."""
        graph = create_task(app_with_writer)
        worker = register_worker(app_with_writer, name="Bob")
        bid_id, bid = submit_bid(app_with_writer, graph, worker, amount=50)
        assert bid.status_code == 200

        response = accept_bid(app_with_writer, graph, worker, bid_id, bid_amount=50)

        assert response.status_code == 200
        row = db_writer.connection.execute(
            "SELECT t.status,t.worker_id,t.accepted_bid_id,t.accepted_at,t.execution_deadline,"
            "c.lifecycle_version,c.execution_cutoff_us,c.accepted_reward,c.accepted_bid_amount "
            "FROM board_tasks AS t JOIN board_task_contracts AS c USING(task_id) "
            "WHERE t.task_id=?",
            (graph.task_id,),
        ).fetchone()
        assert row is not None and tuple(row) == (
            "accepted",
            worker,
            bid_id,
            "2099-01-01T12:00:00Z",
            "2099-01-03T12:00:00Z",
            1,
            FIXED_NOW_US + 172_800 * 1_000_000,
            100,
            50,
        )

    def test_gateway_does_not_validate_transitions(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """TSTAT-14: Gateway now rejects a skipped native transition."""
        graph = create_task(app_with_writer)
        worker = register_worker(app_with_writer)
        before = tuple(db_writer.connection.iterdump())
        response = app_with_writer.post(
            "/board/task-operations",
            json=operation_body(
                operation_id=f"op-{uuid4()}",
                task_id=graph.task_id,
                command="approve_task",
                expected_status="submitted",
                expected_lifecycle_version=0,
                actor_kind="agent",
                actor_id=graph.poster_id,
                arguments={
                    "poster_id": graph.poster_id,
                    "worker_id": worker,
                    "escrow_id": graph.escrow_id,
                    "reward": 100,
                    "source_reference": f"task:{graph.task_id}:approved",
                },
            ),
        )

        assert response.status_code == 409
        assert response.json()["error"] == "invalid_status"
        assert tuple(db_writer.connection.iterdump()) == before


# ===================================================================
# Category 10: Asset Recording (POST /board/assets)
# ===================================================================


@pytest.mark.unit
class TestAssetRecording:
    """Asset recording tests — ASSET-01 through ASSET-07."""

    def test_record_valid_asset(self, app_with_writer: TestClient) -> None:
        """ASSET-01: Record a valid asset returns 201 with asset_id and event_id."""
        tid, uploader = _create_accepted_asset_task(app_with_writer)
        asset_id = f"asset-{uuid4()}"

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
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
        tid, uploader = _create_accepted_asset_task(app_with_writer)
        asset_id = f"asset-{uuid4()}"

        app_with_writer.post(
            "/board/assets",
            json={
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
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
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
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

    def test_missing_task_rejected(self, app_with_writer: TestClient) -> None:
        """ASSET-03: Missing task is rejected by asset admission."""
        uploader = _register_agent(app_with_writer)
        fake_task = f"t-{uuid4()}"

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
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
        assert resp.status_code == 404
        assert resp.json()["error"] == "task_not_found"

    def test_wrong_uploader_rejected(self, app_with_writer: TestClient) -> None:
        """ASSET-04: A non-assigned uploader is forbidden."""
        tid, _assigned_worker = _create_accepted_asset_task(app_with_writer)
        fake_uploader = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
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
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_missing_filename(self, app_with_writer: TestClient) -> None:
        """ASSET-05: Missing filename returns 400 missing_field."""
        tid, _pid, _eid = _create_task(app_with_writer)
        uploader = _register_agent(app_with_writer)

        resp = app_with_writer.post(
            "/board/assets",
            json={
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
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
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
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
        tid, uploader = _create_accepted_asset_task(app_with_writer)

        resp1 = app_with_writer.post(
            "/board/assets",
            json={
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
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
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
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
