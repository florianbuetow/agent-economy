"""T-072 coverage-vs-spec-ID sweep: gap-fill tests for reputation read endpoints.

Covers READ-22 (GET /reputation/feedback/{feedback_id} single lookup) and
READ-23 (not found). GET /reputation/feedback/count and the task_id/agent_id
filtered list were already covered by the existing test_reputation_reads.py;
only the single-record-by-id lookup was missing (verified via a full
literal-ID grep across services/db-gateway/tests/ before writing).

New file — no existing test file was modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event
from tests.fakes.native_board_history import publish_task

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


def _register_agent(client: TestClient, agent_id: str | None = None, name: str = "Test") -> str:
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
    client: TestClient, agent_id: str | None = None, balance: int = 500, name: str = "Test"
) -> str:
    aid = _register_agent(client, agent_id, name=name)
    data: dict[str, Any] = {
        "account_id": aid,
        "balance": balance,
        "created_at": "2026-02-28T10:00:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        data["operation_id"] = f"op-{uuid4()}"
        data["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-02-28T10:00:00Z",
        }
    client.post("/bank/accounts", json=data)
    return aid


def _create_task(client: TestClient, poster_id: str | None = None) -> tuple[str, str, str]:
    pid = poster_id or _setup_funded_account(client)
    tid = f"t-{uuid4()}"
    eid = f"esc-{uuid4()}"
    publish_task(
        client,
        task_id=tid,
        poster_id=pid,
        escrow_id=eid,
        title="Test Task",
        spec="Build a login page",
        reward=100,
        bidding_deadline_seconds=86400,
        acceptance_window_seconds=3600,
        execution_deadline_seconds=172800,
        review_deadline_seconds=43200,
        lock_timestamp="2026-02-28T10:10:00Z",
    )
    return tid, pid, eid


def _submit_feedback(
    client: TestClient, task_id: str, from_id: str, to_id: str, feedback_id: str | None = None
) -> str:
    fid = feedback_id or f"fb-{uuid4()}"
    client.post(
        "/reputation/feedback",
        json={
            "feedback_id": fid,
            "task_id": task_id,
            "from_agent_id": from_id,
            "to_agent_id": to_id,
            "role": "poster",
            "source": "peer",
            "category": "delivery_quality",
            "rating": "satisfied",
            "comment": "Good work",
            "submitted_at": "2026-02-28T15:00:00Z",
            "reveal_reverse": False,
            "event": make_event(source="reputation", event_type="feedback.revealed"),
        },
    )
    return fid


@pytest.mark.unit
class TestReadFeedbackById:
    """READ-22/READ-23: GET /reputation/feedback/{feedback_id}."""

    def test_get_feedback_by_id_returns_the_record(self, app_with_writer: TestClient) -> None:
        alice = _setup_funded_account(app_with_writer, name="Alice")
        bob = _register_agent(app_with_writer, name="Bob")
        tid, _pid, _eid = _create_task(app_with_writer, poster_id=alice)
        fid = _submit_feedback(app_with_writer, tid, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get(f"/reputation/feedback/{fid}")

        assert response.status_code == 200
        data = response.json()
        assert data["feedback_id"] == fid
        assert data["task_id"] == tid
        assert data["from_agent_id"] == alice
        assert data["to_agent_id"] == bob

    def test_get_feedback_by_id_not_found(self, app_with_writer: TestClient) -> None:
        _wire_db_reader()

        response = app_with_writer.get("/reputation/feedback/fb-does-not-exist")

        assert response.status_code == 404
        assert response.json()["error"] == "feedback_not_found"
