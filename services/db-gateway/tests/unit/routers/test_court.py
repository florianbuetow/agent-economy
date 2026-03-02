"""Court domain tests — Categories 12-14 (CLM, REB, RUL)."""

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


def _setup_court_prerequisites(
    client: TestClient,
) -> tuple[str, str, str]:
    """Create alice, bob, and a task. Returns (alice, bob, task_id)."""
    alice = _setup_funded_account(client, name="Alice")
    bob = _register_agent(client, name="Bob")
    tid, _pid, _eid = _create_task(client, poster_id=alice)
    return alice, bob, tid


def _file_claim(
    client: TestClient,
    task_id: str,
    claimant_id: str,
    respondent_id: str,
    claim_id: str | None = None,
    status: str = "filed",
) -> str:
    cid = claim_id or f"clm-{uuid4()}"
    client.post(
        "/court/claims",
        json={
            "claim_id": cid,
            "task_id": task_id,
            "claimant_id": claimant_id,
            "respondent_id": respondent_id,
            "reason": "The login page does not validate email format",
            "status": status,
            "filed_at": "2026-02-28T16:00:00Z",
            "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
        },
    )
    return cid


# ===================================================================
# Category 12: Dispute Claims (POST /court/claims)
# ===================================================================


@pytest.mark.unit
class TestDisputeClaims:
    """Dispute claims tests — CLM-01 through CLM-08."""

    def test_file_valid_claim(self, app_with_writer: TestClient) -> None:
        """CLM-01: File a valid claim returns 201 with claim_id and event_id."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = f"clm-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "The login page does not validate email format",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "claim_id" in data
        assert "event_id" in data
        assert data["claim_id"] == claim_id

    def test_duplicate_claim_id_rejected(self, app_with_writer: TestClient) -> None:
        """CLM-02: Duplicate claim_id is rejected with 409 claim_exists."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = f"clm-{uuid4()}"

        _file_claim(app_with_writer, tid, alice, bob, claim_id=claim_id)

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "Different reason",
                "status": "filed",
                "filed_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "claim_exists"

    def test_fk_violation_task_id(self, app_with_writer: TestClient) -> None:
        """CLM-03: Foreign key violation on task_id returns 409."""
        alice = _register_agent(app_with_writer, name="Alice")
        bob = _register_agent(app_with_writer, name="Bob")
        fake_task = f"t-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": fake_task,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "Bad work",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=fake_task),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_claimant_id(self, app_with_writer: TestClient) -> None:
        """CLM-04: Foreign key violation on claimant_id returns 409."""
        _alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        fake_claimant = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": tid,
                "claimant_id": fake_claimant,
                "respondent_id": bob,
                "reason": "Bad work",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_respondent_id(self, app_with_writer: TestClient) -> None:
        """CLM-05: Foreign key violation on respondent_id returns 409."""
        alice, _bob, tid = _setup_court_prerequisites(app_with_writer)
        fake_respondent = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": fake_respondent,
                "reason": "Bad work",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_missing_reason(self, app_with_writer: TestClient) -> None:
        """CLM-06: Missing reason returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                # reason omitted
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """CLM-07: Missing event returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": f"clm-{uuid4()}",
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "Bad work",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_claim_and_event_atomic(self, app_with_writer: TestClient, db_writer: DbWriter) -> None:
        """CLM-08: Claim and event are written atomically."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = f"clm-{uuid4()}"

        resp = app_with_writer.post(
            "/court/claims",
            json={
                "claim_id": claim_id,
                "task_id": tid,
                "claimant_id": alice,
                "respondent_id": bob,
                "reason": "The login page does not validate email format",
                "status": "filed",
                "filed_at": "2026-02-28T16:00:00Z",
                "event": make_event(source="court", event_type="claim.filed", task_id=tid),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        claim_row = conn.execute(
            "SELECT claim_id FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        assert claim_row is not None

        event_row = conn.execute(
            "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'claim.filed'",
            (tid,),
        ).fetchone()
        assert event_row is not None
        assert event_row[0] == "claim.filed"
        conn.close()


# ===================================================================
# Category 13: Rebuttals (POST /court/rebuttals)
# ===================================================================


@pytest.mark.unit
class TestRebuttals:
    """Rebuttal tests — REB-01 through REB-09."""

    def test_submit_valid_rebuttal(self, app_with_writer: TestClient) -> None:
        """REB-01: Submit a valid rebuttal returns 201 with rebuttal_id and event_id."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        rebuttal_id = f"reb-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": rebuttal_id,
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "The specification did not mention email validation",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "rebuttal_id" in data
        assert "event_id" in data
        assert data["rebuttal_id"] == rebuttal_id

    def test_rebuttal_with_claim_status_update(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """REB-02: Rebuttal with claim_status_update changes claim status."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "The specification did not mention email validation",
                "submitted_at": "2026-02-28T17:00:00Z",
                "claim_status_update": "rebuttal",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "rebuttal"

    def test_rebuttal_without_claim_status_update(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """REB-03: Rebuttal without claim_status_update leaves claim unchanged."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "The specification did not mention email validation",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "filed"

    def test_duplicate_rebuttal_id_rejected(self, app_with_writer: TestClient) -> None:
        """REB-04: Duplicate rebuttal_id is rejected with 409 rebuttal_exists."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        rebuttal_id = f"reb-{uuid4()}"

        app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": rebuttal_id,
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "First rebuttal",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": rebuttal_id,
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "Second rebuttal with same ID",
                "submitted_at": "2026-02-28T17:30:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "rebuttal_exists"

    def test_fk_violation_claim_id(self, app_with_writer: TestClient) -> None:
        """REB-05: Foreign key violation on claim_id returns 409."""
        _alice, bob, _tid = _setup_court_prerequisites(app_with_writer)
        fake_claim = f"clm-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": fake_claim,
                "agent_id": bob,
                "content": "Rebuttal to non-existent claim",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted"),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_agent_id(self, app_with_writer: TestClient) -> None:
        """REB-06: Foreign key violation on agent_id returns 409."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        fake_agent = f"a-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": fake_agent,
                "content": "Rebuttal from fake agent",
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_missing_content(self, app_with_writer: TestClient) -> None:
        """REB-07: Missing content returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": bob,
                # content omitted
                "submitted_at": "2026-02-28T17:00:00Z",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """REB-08: Missing event returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": f"reb-{uuid4()}",
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "Rebuttal text",
                "submitted_at": "2026-02-28T17:00:00Z",
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_rebuttal_and_claim_update_atomic(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """REB-09: Rebuttal, claim update, and event are all written atomically."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        rebuttal_id = f"reb-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": rebuttal_id,
                "claim_id": claim_id,
                "agent_id": bob,
                "content": "The specification was ambiguous",
                "submitted_at": "2026-02-28T17:00:00Z",
                "claim_status_update": "rebuttal",
                "event": make_event(source="court", event_type="rebuttal.submitted", task_id=tid),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        # Rebuttal row exists
        reb_row = conn.execute(
            "SELECT rebuttal_id FROM court_rebuttals WHERE rebuttal_id = ?",
            (rebuttal_id,),
        ).fetchone()
        assert reb_row is not None

        # Claim status updated
        claim_row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        assert claim_row is not None
        assert claim_row[0] == "rebuttal"

        # Event exists
        event_row = conn.execute(
            "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'rebuttal.submitted'",
            (tid,),
        ).fetchone()
        assert event_row is not None
        conn.close()


# ===================================================================
# Category 14: Rulings (POST /court/rulings)
# ===================================================================


@pytest.mark.unit
class TestRulings:
    """Ruling tests — RUL-01 through RUL-10."""

    def test_record_valid_ruling(self, app_with_writer: TestClient) -> None:
        """RUL-01: Record a valid ruling returns 201 with ruling_id and event_id."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        ruling_id = f"rul-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": ruling_id,
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "The specification was ambiguous about email validation",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
                '"reasoning": "Spec did not mention email format"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "ruling_id" in data
        assert "event_id" in data
        assert data["ruling_id"] == ruling_id

    def test_ruling_with_claim_status_update(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """RUL-02: Ruling with claim_status_update changes claim status to 'ruled'."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob, status="rebuttal")

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Spec was ambiguous",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
                '"reasoning": "Ambiguous spec"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "claim_status_update": "ruled",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "ruled"

    def test_ruling_without_claim_status_update(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """RUL-03: Ruling without claim_status_update leaves claim unchanged."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob, status="rebuttal")

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Spec was ambiguous",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
                '"reasoning": "Ambiguous spec"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "rebuttal"

    def test_duplicate_ruling_id_rejected(self, app_with_writer: TestClient) -> None:
        """RUL-04: Duplicate ruling_id is rejected with 409 ruling_exists."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        ruling_id = f"rul-{uuid4()}"

        app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": ruling_id,
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "First ruling",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "First"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": ruling_id,
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 50,
                "summary": "Second ruling with same ID",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 50, "reasoning": "Second"}]',
                "ruled_at": "2026-02-28T19:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "ruling_exists"

    def test_fk_violation_claim_id(self, app_with_writer: TestClient) -> None:
        """RUL-05: Foreign key violation on claim_id returns 409."""
        _alice, _bob, tid = _setup_court_prerequisites(app_with_writer)
        fake_claim = f"clm-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": fake_claim,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Ruling on non-existent claim",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "Test"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_fk_violation_task_id(self, app_with_writer: TestClient) -> None:
        """RUL-06: Foreign key violation on task_id returns 409."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        fake_task = f"t-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": fake_task,
                "worker_pct": 70,
                "summary": "Ruling with wrong task",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "Test"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=fake_task,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_missing_summary(self, app_with_writer: TestClient) -> None:
        """RUL-07: Missing summary returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                # summary omitted
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "Test"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_judge_votes(self, app_with_writer: TestClient) -> None:
        """RUL-08: Missing judge_votes returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Spec was ambiguous",
                # judge_votes omitted
                "ruled_at": "2026-02-28T18:00:00Z",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """RUL-09: Missing event returns 400 missing_field."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": f"rul-{uuid4()}",
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "Spec was ambiguous",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, "reasoning": "Test"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                # event omitted
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_ruling_and_claim_update_atomic(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """RUL-10: Ruling, claim update, and event are all written atomically."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        ruling_id = f"rul-{uuid4()}"

        resp = app_with_writer.post(
            "/court/rulings",
            json={
                "ruling_id": ruling_id,
                "claim_id": claim_id,
                "task_id": tid,
                "worker_pct": 70,
                "summary": "The specification was ambiguous about email validation",
                "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
                '"reasoning": "Spec did not mention email format"}]',
                "ruled_at": "2026-02-28T18:00:00Z",
                "claim_status_update": "ruled",
                "event": make_event(
                    source="court",
                    event_type="ruling.delivered",
                    task_id=tid,
                    agent_id=None,
                ),
            },
        )
        assert resp.status_code == 201

        conn = sqlite3.connect(db_writer._db_path)
        conn.execute("PRAGMA foreign_keys=ON")

        # Ruling row exists
        rul_row = conn.execute(
            "SELECT ruling_id FROM court_rulings WHERE ruling_id = ?", (ruling_id,)
        ).fetchone()
        assert rul_row is not None

        # Claim status updated
        claim_row = conn.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        assert claim_row is not None
        assert claim_row[0] == "ruled"

        # Event exists
        event_row = conn.execute(
            "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'ruling.delivered'",
            (tid,),
        ).fetchone()
        assert event_row is not None
        conn.close()
