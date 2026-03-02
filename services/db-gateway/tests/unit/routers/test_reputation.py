"""Reputation domain tests — Category 11: Feedback (FB-01 through FB-10)."""

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
    """Create a full task with all prerequisites. Returns (task_id, poster_id, escrow_id)."""
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


# ===================================================================
# Category 11: Feedback Submission (POST /reputation/feedback)
# ===================================================================


@pytest.mark.unit
class TestFeedbackSubmission:
    """Feedback submission tests — FB-01 through FB-10."""

    def test_submit_without_reveal(self, app_with_writer: TestClient) -> None:
        """FB-01: Submit feedback without reveal returns visible=false."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)
        fb_id = f"fb-{uuid4()}"

        resp = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": fb_id,
                "task_id": tid,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Good work",
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "feedback_id" in data
        assert "visible" in data
        assert "event_id" in data
        assert data["visible"] is False

    def test_submit_with_mutual_reveal(self, app_with_writer: TestClient) -> None:
        """FB-02: Submit feedback with mutual reveal returns visible=true."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)

        # Bob submits first without reveal
        bob_fb_id = f"fb-{uuid4()}"
        app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": bob_fb_id,
                "task_id": tid,
                "from_agent_id": bob,
                "to_agent_id": alice,
                "role": "worker",
                "category": "spec_quality",
                "rating": "satisfied",
                "comment": "Clear spec",
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )

        # Alice submits with reveal
        alice_fb_id = f"fb-{uuid4()}"
        resp = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": alice_fb_id,
                "task_id": tid,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Good work",
                "submitted_at": "2026-02-28T15:30:00Z",
                "reveal_reverse": True,
                "reverse_feedback_id": bob_fb_id,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )
        assert resp.status_code == 201
        assert resp.json()["visible"] is True

    def test_mutual_reveal_both_visible(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """FB-03: Mutual reveal sets both feedbacks to visible in the database."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)

        # Bob submits first without reveal
        bob_fb_id = f"fb-{uuid4()}"
        app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": bob_fb_id,
                "task_id": tid,
                "from_agent_id": bob,
                "to_agent_id": alice,
                "role": "worker",
                "category": "spec_quality",
                "rating": "satisfied",
                "comment": "Clear spec",
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )

        # Alice submits with reveal
        alice_fb_id = f"fb-{uuid4()}"
        app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": alice_fb_id,
                "task_id": tid,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Good work",
                "submitted_at": "2026-02-28T15:30:00Z",
                "reveal_reverse": True,
                "reverse_feedback_id": bob_fb_id,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        bob_row = conn.execute(
            "SELECT visible FROM reputation_feedback WHERE feedback_id = ?",
            (bob_fb_id,),
        ).fetchone()
        alice_row = conn.execute(
            "SELECT visible FROM reputation_feedback WHERE feedback_id = ?",
            (alice_fb_id,),
        ).fetchone()
        conn.close()

        assert bob_row is not None
        assert bob_row[0] == 1
        assert alice_row is not None
        assert alice_row[0] == 1

    def test_duplicate_feedback_rejected(self, app_with_writer: TestClient) -> None:
        """FB-04: Duplicate feedback (same task, from, to) is rejected with 409."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)

        app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": f"fb-{uuid4()}",
                "task_id": tid,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Good work",
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )

        resp = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": f"fb-{uuid4()}",
                "task_id": tid,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "extremely_satisfied",
                "comment": "Actually great",
                "submitted_at": "2026-02-28T16:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "feedback_exists"

    def test_same_agents_different_tasks(self, app_with_writer: TestClient) -> None:
        """FB-05: Same agents, different tasks succeed."""
        alice = _setup_funded_account(app_with_writer, name="Alice")
        bob = _register_agent(app_with_writer, name="Bob")

        tid1, _p1, _e1 = _create_task(app_with_writer, poster_id=alice)
        tid2, _p2, _e2 = _create_task(app_with_writer, poster_id=alice)

        resp1 = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": f"fb-{uuid4()}",
                "task_id": tid1,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Good",
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )
        resp2 = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": f"fb-{uuid4()}",
                "task_id": tid2,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Also good",
                "submitted_at": "2026-02-28T16:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201

    def test_fk_violation_from_agent_id(self, app_with_writer: TestClient) -> None:
        """FB-06: Foreign key violation on from_agent_id returns 409."""
        _alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)
        fake_agent = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": f"fb-{uuid4()}",
                "task_id": tid,
                "from_agent_id": fake_agent,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Test",
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_missing_rating(self, app_with_writer: TestClient) -> None:
        """FB-07: Missing rating returns 400 missing_field."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)

        resp = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": f"fb-{uuid4()}",
                "task_id": tid,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                # rating omitted
                "comment": "Good work",
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """FB-08: Missing event returns 400 missing_field."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)

        resp = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": f"fb-{uuid4()}",
                "task_id": tid,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Good work",
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_null_comment_accepted(self, app_with_writer: TestClient) -> None:
        """FB-09: Null comment is accepted with 201."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)

        resp = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": f"fb-{uuid4()}",
                "task_id": tid,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": None,
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )
        assert resp.status_code == 201

    def test_without_reveal_defaults_sealed(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """FB-10: Without reveal_reverse, visible=false and DB row has visible=0."""
        alice, bob, tid, _eid = _setup_feedback_pair(app_with_writer)
        fb_id = f"fb-{uuid4()}"

        resp = app_with_writer.post(
            "/reputation/feedback",
            json={
                "feedback_id": fb_id,
                "task_id": tid,
                "from_agent_id": alice,
                "to_agent_id": bob,
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Test",
                "submitted_at": "2026-02-28T15:00:00Z",
                "reveal_reverse": False,
                "event": make_event(source="reputation", event_type="feedback.revealed"),
            },
        )
        assert resp.status_code == 201
        assert resp.json()["visible"] is False

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT visible FROM reputation_feedback WHERE feedback_id = ?",
            (fb_id,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 0
