"""Reputation read endpoint tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


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
    name: str = "Test",
) -> str:
    aid = _register_agent(client, agent_id, name=name)
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
) -> tuple[str, str, str]:
    """Create a full task. Returns (task_id, poster_id, escrow_id)."""
    pid = poster_id or _setup_funded_account(client)
    eid, tid = _lock_escrow(client, pid)
    client.post(
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
    return tid, pid, eid


def _setup_feedback_pair(
    client: TestClient,
) -> tuple[str, str, str, str]:
    """Create two agents and a task. Returns (alice, bob, task_id, escrow_id)."""
    alice = _setup_funded_account(client, name="Alice")
    bob = _register_agent(client, name="Bob")
    tid, _pid, eid = _create_task(client, poster_id=alice)
    return alice, bob, tid, eid


def _submit_feedback(
    client: TestClient,
    task_id: str,
    from_id: str,
    to_id: str,
    role: str = "poster",
    category: str = "delivery_quality",
    feedback_id: str | None = None,
) -> str:
    fid = feedback_id or f"fb-{uuid4()}"
    client.post(
        "/reputation/feedback",
        json={
            "feedback_id": fid,
            "task_id": task_id,
            "from_agent_id": from_id,
            "to_agent_id": to_id,
            "role": role,
            "category": category,
            "rating": "satisfied",
            "comment": "Good work",
            "submitted_at": "2026-02-28T15:00:00Z",
            "reveal_reverse": False,
            "event": make_event(source="reputation", event_type="feedback.revealed"),
        },
    )
    return fid


# ===========================================================================
# Reputation Read Endpoint Tests
# ===========================================================================


@pytest.mark.unit
class TestReputationReads:
    """Tests for reputation read endpoints."""

    def test_get_feedback_by_task(self, app_with_writer: TestClient) -> None:
        """GET /reputation/feedback?task_id=X returns feedback for that task."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)
        _submit_feedback(
            app_with_writer,
            tid,
            alice,
            bob,
            role="poster",
            category="delivery_quality",
        )
        _submit_feedback(
            app_with_writer,
            tid,
            bob,
            alice,
            role="worker",
            category="spec_quality",
        )
        _wire_db_reader()

        response = app_with_writer.get(f"/reputation/feedback?task_id={tid}")

        assert response.status_code == 200
        feedback = response.json()["feedback"]
        assert len(feedback) == 2

    def test_get_feedback_by_task_empty(self, app_with_writer: TestClient) -> None:
        """GET /reputation/feedback?task_id=X returns empty when no feedback."""
        _alice, _bob, tid, _eid = _setup_feedback_pair(app_with_writer)
        _wire_db_reader()

        response = app_with_writer.get(f"/reputation/feedback?task_id={tid}")

        assert response.status_code == 200
        assert response.json() == {"feedback": []}

    def test_get_feedback_by_agent(self, app_with_writer: TestClient) -> None:
        """GET /reputation/feedback?agent_id=X returns feedback for that agent."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)
        _submit_feedback(
            app_with_writer,
            tid,
            alice,
            bob,
            role="poster",
            category="delivery_quality",
        )
        _wire_db_reader()

        response = app_with_writer.get(f"/reputation/feedback?agent_id={bob}")

        assert response.status_code == 200
        feedback = response.json()["feedback"]
        assert len(feedback) >= 1
        assert all(fb["to_agent_id"] == bob for fb in feedback)

    def test_get_feedback_no_filter(self, app_with_writer: TestClient) -> None:
        """GET /reputation/feedback without filter returns empty list."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)
        _submit_feedback(app_with_writer, tid, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get("/reputation/feedback")

        assert response.status_code == 200
        assert response.json() == {"feedback": []}

    def test_count_feedback_zero(self, app_with_writer: TestClient) -> None:
        """GET /reputation/feedback/count returns zero when empty."""
        _wire_db_reader()

        response = app_with_writer.get("/reputation/feedback/count")

        assert response.status_code == 200
        assert response.json() == {"count": 0}

    def test_count_feedback_after_submissions(self, app_with_writer: TestClient) -> None:
        """GET /reputation/feedback/count reflects submitted feedback."""
        alice, bob, tid1, _eid1 = _setup_feedback_pair(app_with_writer)
        tid2, _pid2, _eid2 = _create_task(app_with_writer, poster_id=alice)
        _submit_feedback(
            app_with_writer,
            tid1,
            alice,
            bob,
            role="poster",
            category="delivery_quality",
        )
        _submit_feedback(
            app_with_writer,
            tid1,
            bob,
            alice,
            role="worker",
            category="spec_quality",
        )
        _submit_feedback(
            app_with_writer,
            tid2,
            alice,
            bob,
            role="poster",
            category="delivery_quality",
        )
        _wire_db_reader()

        response = app_with_writer.get("/reputation/feedback/count")

        assert response.status_code == 200
        assert response.json() == {"count": 3}
