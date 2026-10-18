"""GAP-E13 (T-101) — the gateway write path must keep board_tasks.bid_count truthful.

Historical WP-15 regression: the now-retired dictionary store incremented bid_count
on every bid insert, but the gateway-backed production path did not — a task's
stored bid_count stayed 0 forever while list_bids/get_bids_for_task
returned the real bids. The feeder's acceptance loop worked around this by counting
list_bids results directly; this suite is what proves the service side no longer
needs that workaround (the workaround itself, agents/src/task_feeder/acceptance.py,
is untouched).

Design choice: increment bid_count in the SAME BEGIN IMMEDIATE transaction as the
bid INSERT (db_writer.submit_bid), rather than deriving it on every read. This
matches how every other board_tasks field in this
schema works — status, worker_id, escrow_pending etc. are all materialized columns
updated by the write path, not computed on read. Deriving on read would mean a new
COUNT(*) subquery (or JOIN) on every get_task/list_tasks call, a bigger and slower
change to the read layer for no behavioral benefit over updating one counter at
write time inside a transaction that is already open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event
from tests.fakes.native_board_history import (
    BusinessClock,
    TaskGraph,
)
from tests.fakes.native_board_history import (
    submit_bid as submit_native_bid,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_BUSINESS_NOW_US = parse_utc_text_to_epoch_us("2026-03-01T09:20:00Z")


@pytest.fixture(autouse=True)
def _fixed_gateway_business_clock(monkeypatch: pytest.MonkeyPatch) -> BusinessClock:
    """Keep native bid admission inside the published bidding cutoff."""
    clock = BusinessClock(now_us=_BUSINESS_NOW_US)
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock)
    return clock


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state (app_with_writer resets state after
    lifespan runs, dropping the reader lifespan wired up). Uses the public
    DbWriter.connection accessor (GAP-C5) rather than reaching into `_db`.
    """
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer.connection)


def _register_agent(client: TestClient, name: str) -> str:
    agent_id = f"a-{uuid4()}"
    resp = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-03-01T09:00:00Z",
            "event": make_event(),
        },
    )
    assert resp.status_code == 201
    return agent_id


def _create_funded_account(client: TestClient, name: str, balance: int) -> str:
    agent_id = _register_agent(client, name)
    payload = {
        "operation_id": f"op-{uuid4()}",
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2026-03-01T09:05:00Z",
        "event": make_event(source="bank", event_type="account.created"),
        "initial_credit": {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-03-01T09:05:00Z",
        },
    }
    resp = client.post("/bank/accounts", json=payload)
    assert resp.status_code == 201
    return agent_id


def _create_task(client: TestClient, poster_id: str) -> TaskGraph:
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    lock = client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": f"op-{uuid4()}",
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": 100,
            "task_id": task_id,
            "created_at": "2026-03-01T09:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=task_id),
        },
    )
    assert lock.status_code == 201
    created = client.post(
        "/board/tasks",
        json={
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "bid_count test task",
            "spec": "Build a thing",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 3600,
            "acceptance_window_seconds": 3600,
            "deadline_seconds": 7200,
            "review_deadline_seconds": 1800,
            "bidding_deadline": "2026-03-01T10:12:00Z",
            "escrow_id": escrow_id,
            "created_at": "2026-03-01T09:12:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=task_id),
        },
    )
    assert created.status_code == 201
    return TaskGraph(task_id=task_id, poster_id=poster_id, escrow_id=escrow_id)


def _submit_bid(client: TestClient, graph: TaskGraph, bidder_id: str, amount: int) -> None:
    _bid_id, response = submit_native_bid(client, graph, bidder_id, amount=amount)
    assert response.status_code == 200


@pytest.mark.unit
class TestBidCountReflectsReality:
    """board_tasks.bid_count must match the number of bids list_bids returns."""

    def test_bid_count_is_zero_before_any_bids(self, app_with_writer: TestClient) -> None:
        _wire_db_reader()
        poster = _create_funded_account(app_with_writer, "Poster", balance=500)
        graph = _create_task(app_with_writer, poster)

        task = app_with_writer.get(f"/board/tasks/{graph.task_id}")

        assert task.status_code == 200
        assert task.json()["bid_count"] == 0

    def test_bid_count_increments_on_each_bid(self, app_with_writer: TestClient) -> None:
        _wire_db_reader()
        poster = _create_funded_account(app_with_writer, "Poster", balance=500)
        bidder_a = _register_agent(app_with_writer, "Alice")
        bidder_b = _register_agent(app_with_writer, "Bob")
        graph = _create_task(app_with_writer, poster)

        _submit_bid(app_with_writer, graph, bidder_a, amount=50)
        task_after_one = app_with_writer.get(f"/board/tasks/{graph.task_id}")
        assert task_after_one.json()["bid_count"] == 1

        _submit_bid(app_with_writer, graph, bidder_b, amount=45)
        task_after_two = app_with_writer.get(f"/board/tasks/{graph.task_id}")
        assert task_after_two.json()["bid_count"] == 2

    def test_bid_count_matches_get_bids_for_task_length(self, app_with_writer: TestClient) -> None:
        """The exact assertion T-101 cares about: the summary must stop lying."""
        _wire_db_reader()
        poster = _create_funded_account(app_with_writer, "Poster", balance=500)
        bidder_a = _register_agent(app_with_writer, "Alice")
        bidder_b = _register_agent(app_with_writer, "Bob")
        bidder_c = _register_agent(app_with_writer, "Carol")
        graph = _create_task(app_with_writer, poster)

        for bidder in (bidder_a, bidder_b, bidder_c):
            _submit_bid(app_with_writer, graph, bidder, amount=10)

        task = app_with_writer.get(f"/board/tasks/{graph.task_id}")
        bids = app_with_writer.get(f"/board/tasks/{graph.task_id}/bids")

        assert bids.status_code == 200
        real_bid_count = len(bids.json()["bids"])
        assert real_bid_count == 3
        assert task.json()["bid_count"] == real_bid_count

    def test_duplicate_bid_rejection_does_not_double_increment(
        self, app_with_writer: TestClient
    ) -> None:
        """A rejected duplicate bid (same bidder, same task) must not inflate bid_count."""
        _wire_db_reader()
        poster = _create_funded_account(app_with_writer, "Poster", balance=500)
        bidder = _register_agent(app_with_writer, "Alice")
        graph = _create_task(app_with_writer, poster)

        _submit_bid(app_with_writer, graph, bidder, amount=50)

        _duplicate_bid_id, duplicate = submit_native_bid(
            app_with_writer,
            graph,
            bidder,
            amount=40,
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"] == "bid_exists"

        task = app_with_writer.get(f"/board/tasks/{graph.task_id}")
        assert task.json()["bid_count"] == 1
