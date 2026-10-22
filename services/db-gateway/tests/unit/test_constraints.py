"""Tests for optional write constraints on DB Gateway endpoints."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.conftest import make_event
from tests.fakes.native_board_history import (
    BIDDING_CUTOFF_US,
    BusinessClock,
    TaskGraph,
    accept_bid,
    operation_body,
    publish_task,
    register_worker,
    submit_bid,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


@pytest.fixture(autouse=True)
def _fixed_gateway_business_clock(monkeypatch: pytest.MonkeyPatch) -> BusinessClock:
    """Use deterministic lifecycle time without changing real JWT time."""
    clock = BusinessClock()
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock)
    return clock


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
        payload["operation_id"] = f"op-{uuid4()}"
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
    publish_task(
        client,
        task_id=task_id,
        poster_id=poster_id,
        escrow_id=escrow_id,
        title="Test Task",
        spec="Build a login page",
        reward=100,
        bidding_deadline_seconds=43200,
        acceptance_window_seconds=3600,
        execution_deadline_seconds=172800,
        review_deadline_seconds=43200,
        lock_timestamp="2026-03-01T09:10:00Z",
    )
    return task_id, escrow_id


def _read_one(db_writer: DbWriter, query: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
    conn = sqlite3.connect(db_writer._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(query, params).fetchone()
    conn.close()
    return row


def _graph(task_id: str, poster_id: str, escrow_id: str) -> TaskGraph:
    return TaskGraph(task_id, poster_id, escrow_id)


def _accept_task(
    client: TestClient,
    *,
    poster_id: str,
) -> tuple[TaskGraph, str, str]:
    task_id, escrow_id = _create_task(client, poster_id=poster_id)
    graph = _graph(task_id, poster_id, escrow_id)
    worker_id = register_worker(client)
    bid_id, bid = submit_bid(client, graph, worker_id, amount=50)
    assert bid.status_code == 200, bid.text
    accepted = accept_bid(client, graph, worker_id, bid_id, bid_amount=50)
    assert accepted.status_code == 200, accepted.text
    return graph, worker_id, bid_id


def _snapshot(db_writer: DbWriter) -> tuple[str, ...]:
    return tuple(db_writer.connection.iterdump())


def _cancel_command(graph: TaskGraph) -> dict[str, object]:
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


@pytest.mark.unit
class TestConstraintEnforcement:
    """Constraint behavior for task status, escrow, and cross-table writes."""

    def test_update_task_with_valid_constraint(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """A native update succeeds when explicit status/version preconditions match."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        graph, worker_id, bid_id = _accept_task(app_with_writer, poster_id=poster_id)

        row = _read_one(
            db_writer,
            "SELECT t.status,t.worker_id,t.accepted_bid_id,c.lifecycle_version,"
            "c.accepted_reward,c.accepted_bid_amount FROM board_tasks AS t "
            "JOIN board_task_contracts AS c USING(task_id) WHERE t.task_id=?",
            (graph.task_id,),
        )
        assert row is not None
        assert tuple(row) == ("accepted", worker_id, bid_id, 1, 100, 50)

    def test_update_task_with_violated_constraint(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """A stale native lifecycle version fails without mutation."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        graph, worker_id, _bid_id = _accept_task(app_with_writer, poster_id=poster_id)
        payload = operation_body(
            operation_id=f"op-{uuid4()}",
            task_id=graph.task_id,
            command="submit_deliverable",
            expected_status="accepted",
            expected_lifecycle_version=0,
            actor_kind="agent",
            actor_id=worker_id,
            arguments={"worker_id": worker_id},
        )

        before = _snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 409
        assert response.json()["error"] == "task_version_conflict"
        assert response.json()["details"] == {
            "expected_lifecycle_version": 0,
            "actual_lifecycle_version": 1,
            "current_status": "accepted",
        }
        assert _snapshot(db_writer) == before

    def test_constraint_violation_error_format(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """Native status conflicts expose expected and current lifecycle state."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        graph, worker_id, bid_id = _accept_task(app_with_writer, poster_id=poster_id)
        payload = operation_body(
            operation_id=f"op-{uuid4()}",
            task_id=graph.task_id,
            command="accept_bid",
            expected_status="open",
            expected_lifecycle_version=1,
            actor_kind="agent",
            actor_id=graph.poster_id,
            arguments={
                "bid_id": bid_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "bid_amount": 50,
            },
        )

        before = _snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        body = response.json()
        assert response.status_code == 409
        assert body["error"] == "invalid_status"
        assert body["details"] == {
            "expected_status": "open",
            "current_status": "accepted",
        }
        assert _snapshot(db_writer) == before

    def test_update_without_constraints(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """Native commands reject a missing lifecycle-version precondition."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, escrow_id = _create_task(app_with_writer, poster_id=poster_id)
        graph = _graph(task_id, poster_id, escrow_id)
        payload = _cancel_command(graph)
        del payload["expected_lifecycle_version"]

        before = _snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _snapshot(db_writer) == before

    def test_empty_constraints_object(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """An obsolete empty constraints field is rejected before mutation."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id, escrow_id = _create_task(app_with_writer, poster_id=poster_id)
        graph = _graph(task_id, poster_id, escrow_id)
        payload = _cancel_command(graph)
        payload["constraints"] = {}

        before = _snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        assert _snapshot(db_writer) == before

    def test_escrow_release_owns_locked_precondition(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """Escrow release applies its locked-state precondition itself."""
        payer_id = _create_funded_account(app_with_writer, name="Payer", balance=500)
        recipient_id = _create_funded_account(app_with_writer, name="Recipient", balance=0)
        task_id, escrow_id = _create_task(app_with_writer, poster_id=payer_id)

        response = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "operation_id": f"op-{uuid4()}",
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_id,
                "source_reference": f"release-{task_id}",
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-03-01T09:30:00Z",
                "event": make_event(source="bank", event_type="escrow.released", task_id=task_id),
            },
        )
        assert response.status_code == 200
        assert response.json()["receipt"]["result"]["escrow_status_at_commit"] == "released"
        assert response.json()["current_escrow"]["status"] == "released"

        row = _read_one(
            db_writer,
            "SELECT status FROM bank_escrow WHERE escrow_id = ?",
            (escrow_id,),
        )
        assert row is not None
        assert row["status"] == "released"

    def test_cross_table_bid_constraint(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
    ) -> None:
        """Native bid admission rejects an already accepted task without mutation."""
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        graph, _worker_id, _bid_id = _accept_task(app_with_writer, poster_id=poster_id)
        bidder_id = _register_agent(app_with_writer, name="Late Bidder")
        payload = operation_body(
            operation_id=f"op-{uuid4()}",
            task_id=graph.task_id,
            command="submit_bid",
            expected_status="open",
            expected_lifecycle_version=1,
            actor_kind="agent",
            actor_id=bidder_id,
            arguments={
                "bid_id": f"bid-{uuid4()}",
                "bidder_id": bidder_id,
                "amount": 55,
            },
        )

        before = _snapshot(db_writer)
        response = app_with_writer.post("/board/task-operations", json=payload)

        assert response.status_code == 409
        assert response.json()["error"] == "invalid_status"
        assert response.json()["details"] == {
            "expected_status": "open",
            "current_status": "accepted",
        }
        assert _snapshot(db_writer) == before
