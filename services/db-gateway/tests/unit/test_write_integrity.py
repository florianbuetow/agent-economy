"""Gateway write integrity: every write emits an event, replays return real values.

Covers GAP-C2 (delete_ruling / update_claim_status must log events transactionally),
GAP-C3 (idempotent replays must not return sentinel zeros), and GAP-C4 (a broken
schema must kill startup instead of being silently suppressed).
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.services.db_writer import DbWriter
from tests.conftest import make_event

if TYPE_CHECKING:
    from collections.abc import Iterator

BUSY_TIMEOUT_MS = 5000
JOURNAL_MODE = "wal"


# ---------------------------------------------------------------------------
# Fixture helpers — build the FK chain agent -> account -> escrow -> task -> claim
# ---------------------------------------------------------------------------


def _register_agent(writer: DbWriter, name: str) -> str:
    agent_id = f"a-{uuid4()}"
    writer.register_agent(
        {
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(agent_id=agent_id),
        }
    )
    return agent_id


def _create_account(writer: DbWriter, agent_id: str, balance: int) -> None:
    data: dict[str, Any] = {
        "account_id": agent_id,
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
    writer.create_account(data)


def _lock_escrow(writer: DbWriter, payer_id: str, task_id: str, amount: int) -> str:
    escrow_id = f"esc-{uuid4()}"
    writer.escrow_lock(
        {
            "escrow_id": escrow_id,
            "payer_account_id": payer_id,
            "amount": amount,
            "task_id": task_id,
            "created_at": "2026-02-28T10:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=task_id),
        }
    )
    return escrow_id


def _create_task(writer: DbWriter, poster_id: str) -> str:
    task_id = f"t-{uuid4()}"
    escrow_id = _lock_escrow(writer, poster_id, task_id, amount=100)
    writer.create_task(
        {
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "Test Task",
            "spec": "Build a login page",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 86400,
            "deadline_seconds": 172800,
            "review_deadline_seconds": 43200,
            "bidding_deadline": "2026-03-01T10:00:00Z",
            "escrow_id": escrow_id,
            "created_at": "2026-02-28T10:15:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=task_id),
        }
    )
    return task_id


def _file_claim(writer: DbWriter, task_id: str, claimant: str, respondent: str) -> str:
    claim_id = f"clm-{uuid4()}"
    writer.file_claim(
        {
            "claim_id": claim_id,
            "task_id": task_id,
            "claimant_id": claimant,
            "respondent_id": respondent,
            "reason": "The login page does not validate email format",
            "status": "filed",
            "filed_at": "2026-02-28T16:00:00Z",
            "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
        }
    )
    return claim_id


def _record_ruling(writer: DbWriter, claim_id: str, task_id: str) -> str:
    ruling_id = f"rul-{uuid4()}"
    writer.record_ruling(
        {
            "ruling_id": ruling_id,
            "claim_id": claim_id,
            "task_id": task_id,
            "worker_pct": 70,
            "summary": "Spec was ambiguous",
            "judge_votes": "[]",
            "ruled_at": "2026-02-28T18:00:00Z",
            "event": make_event(source="court", event_type="ruling.delivered", task_id=task_id),
        }
    )
    return ruling_id


def _ruling_count(writer: DbWriter, claim_id: str) -> int:
    cursor = writer._db.execute(
        "SELECT COUNT(*) FROM court_rulings WHERE claim_id = ?",
        (claim_id,),
    )
    return int(cursor.fetchone()[0])


def _claim_status(writer: DbWriter, claim_id: str) -> str:
    cursor = writer._db.execute(
        "SELECT status FROM court_claims WHERE claim_id = ?",
        (claim_id,),
    )
    return str(cursor.fetchone()[0])


def _events_of_type(writer: DbWriter, event_type: str) -> list[dict[str, Any]]:
    cursor = writer._db.execute(
        "SELECT event_id, event_source, event_type, summary FROM events WHERE event_type = ?",
        (event_type,),
    )
    return [dict(row) for row in cursor.fetchall()]


@pytest.fixture
def court_case(db_writer: DbWriter) -> tuple[str, str, str]:
    """Return (claim_id, task_id, respondent_id) for a task with a recorded ruling."""
    alice = _register_agent(db_writer, "Alice")
    bob = _register_agent(db_writer, "Bob")
    _create_account(db_writer, alice, balance=500)
    task_id = _create_task(db_writer, alice)
    claim_id = _file_claim(db_writer, task_id, alice, bob)
    _record_ruling(db_writer, claim_id, task_id)
    return claim_id, task_id, bob


# ---------------------------------------------------------------------------
# GAP-C2 — delete_ruling is transactional and emits an event
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteRulingEmitsEvent:
    """DELETE /court/rulings/{claim_id} is a write and must log an event."""

    def test_delete_ruling_writes_event_in_same_transaction(
        self, db_writer: DbWriter, court_case: tuple[str, str, str]
    ) -> None:
        claim_id, task_id, _respondent = court_case
        event = make_event(source="court", event_type="ruling.deleted", task_id=task_id)

        result = db_writer.delete_ruling(claim_id, event)

        assert result["deleted"] is True
        assert _ruling_count(db_writer, claim_id) == 0
        logged = _events_of_type(db_writer, "ruling.deleted")
        assert len(logged) == 1
        assert logged[0]["event_source"] == "court"
        assert result["event_id"] == logged[0]["event_id"]

    def test_delete_ruling_rolls_back_when_event_insert_fails(
        self, db_writer: DbWriter, court_case: tuple[str, str, str]
    ) -> None:
        claim_id, task_id, _respondent = court_case
        # events.agent_id has a FK to identity_agents — an unknown agent aborts the insert.
        bad_event = make_event(
            source="court",
            event_type="ruling.deleted",
            task_id=task_id,
            agent_id="a-does-not-exist",
        )

        with pytest.raises(ServiceError):
            db_writer.delete_ruling(claim_id, bad_event)

        assert _ruling_count(db_writer, claim_id) == 1
        assert _events_of_type(db_writer, "ruling.deleted") == []

    def test_delete_ruling_missing_claim_logs_no_event(self, db_writer: DbWriter) -> None:
        event = make_event(source="court", event_type="ruling.deleted")

        result = db_writer.delete_ruling(f"clm-{uuid4()}", event)

        assert result["deleted"] is False
        assert result["event_id"] is None
        assert _events_of_type(db_writer, "ruling.deleted") == []


# ---------------------------------------------------------------------------
# GAP-C2 — update_claim_status requires an event
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateClaimStatusRequiresEvent:
    """POST /court/claims/{claim_id}/status must carry an event."""

    def test_missing_event_is_rejected_and_row_unchanged(
        self, db_writer: DbWriter, court_case: tuple[str, str, str]
    ) -> None:
        claim_id, _task_id, _respondent = court_case
        events_before = db_writer.get_total_events()

        with pytest.raises(ServiceError) as excinfo:
            db_writer.update_claim_status(claim_id, {"status": "ruled"}, None)

        assert excinfo.value.error == "missing_field"
        assert excinfo.value.status_code == 400
        assert _claim_status(db_writer, claim_id) == "filed"
        assert db_writer.get_total_events() == events_before

    def test_event_is_written_with_the_status_update(
        self, db_writer: DbWriter, court_case: tuple[str, str, str]
    ) -> None:
        claim_id, task_id, _respondent = court_case
        event = make_event(source="court", event_type="claim.status_changed", task_id=task_id)

        result = db_writer.update_claim_status(claim_id, {"status": "ruled", "event": event}, None)

        assert _claim_status(db_writer, claim_id) == "ruled"
        logged = _events_of_type(db_writer, "claim.status_changed")
        assert len(logged) == 1
        assert result["event_id"] == logged[0]["event_id"]


# ---------------------------------------------------------------------------
# GAP-C3 — idempotent replays return the original event_id and a real balance
# ---------------------------------------------------------------------------


def _event(
    source: str,
    event_type: str,
    timestamp: str,
    task_id: str | None = None,
    agent_id: str | None = None,
    payload: str = "{}",
) -> dict[str, Any]:
    """Build an event the way the real service clients do — caller controls every field."""
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": timestamp,
        "task_id": task_id,
        "agent_id": agent_id,
        "summary": "Test event",
        "payload": payload,
    }


def _register_body(agent_id: str, public_key: str, event_timestamp: str) -> dict[str, Any]:
    """Mirror identity/agent_db_client.py: fresh agent_id + fresh event timestamp per call."""
    return {
        "agent_id": agent_id,
        "name": "Alice",
        "public_key": public_key,
        # registered_at is second-granular in the real client, so a same-second replay matches.
        "registered_at": "2026-02-28T10:00:00Z",
        "event": _event(
            "identity",
            "agent.registered",
            timestamp=event_timestamp,
            agent_id=agent_id,
        ),
    }


def _credit_body(account_id: str, reference: str, amount: int, now: str) -> dict[str, Any]:
    """Mirror central-bank/ledger_db_client.py credit(): fresh tx_id and fresh timestamps."""
    return {
        "tx_id": f"tx-{uuid4()}",
        "account_id": account_id,
        "amount": amount,
        "reference": reference,
        "timestamp": now,
        "event": _event("bank", "salary.paid", timestamp=now, agent_id=account_id),
    }


def _escrow_body(payer_account_id: str, task_id: str, amount: int, now: str) -> dict[str, Any]:
    """Mirror central-bank/ledger_db_client.py escrow_lock(): fresh escrow_id inside the payload."""
    escrow_id = f"esc-{uuid4()}"
    return {
        "escrow_id": escrow_id,
        "payer_account_id": payer_account_id,
        "amount": amount,
        "task_id": task_id,
        "created_at": now,
        "tx_id": f"tx-{uuid4()}",
        "event": _event(
            "bank",
            "escrow.locked",
            timestamp=now,
            task_id=task_id,
            agent_id=payer_account_id,
            payload=json.dumps({"escrow_id": escrow_id, "amount": amount, "title": task_id}),
        ),
    }


@pytest.mark.unit
class TestIdempotentReplaysReturnRealValues:
    """A replayed write returns the original positive event_id, never a sentinel or null.

    Each replay is built FRESH, exactly as the real service clients build it: new
    surrogate ids and new timestamps. Replaying a byte-identical body would not
    exercise the production path at all.
    """

    def test_register_agent_replay_returns_original_event_id(self, db_writer: DbWriter) -> None:
        public_key = f"ed25519:{uuid4()}"
        original_id = f"a-{uuid4()}"

        first = db_writer.register_agent(
            _register_body(original_id, public_key, event_timestamp="2026-02-28T10:00:00Z")
        )
        events_after_first = db_writer.get_total_events()

        # Realistic replay: new agent_id, new event timestamp, same public_key.
        replay = db_writer.register_agent(
            _register_body(f"a-{uuid4()}", public_key, event_timestamp="2026-02-28T10:00:07Z")
        )

        assert isinstance(first["event_id"], int)
        assert first["event_id"] > 0
        assert replay["agent_id"] == original_id
        assert replay["event_id"] == first["event_id"]
        assert db_writer.get_total_events() == events_after_first

    def test_credit_replay_returns_original_event_id_and_balance(self, db_writer: DbWriter) -> None:
        agent_id = _register_agent(db_writer, "Alice")
        _create_account(db_writer, agent_id, balance=100)

        first = db_writer.credit_account(
            _credit_body(agent_id, "salary_round_1", 25, now="2026-02-28T10:05:00Z")
        )
        events_after_first = db_writer.get_total_events()

        # Realistic replay: new tx_id, new timestamps, same (account_id, reference, amount).
        replay = db_writer.credit_account(
            _credit_body(agent_id, "salary_round_1", 25, now="2026-02-28T10:05:09Z")
        )

        assert first["event_id"] > 0
        assert first["balance_after"] == 125
        assert replay["balance_after"] == 125
        assert replay["event_id"] == first["event_id"]
        assert db_writer.get_total_events() == events_after_first

    def test_escrow_lock_replay_returns_original_event_id_and_balance(
        self, db_writer: DbWriter
    ) -> None:
        agent_id = _register_agent(db_writer, "Alice")
        _create_account(db_writer, agent_id, balance=100)
        task_id = f"t-{uuid4()}"

        first = db_writer.escrow_lock(
            _escrow_body(agent_id, task_id, 30, now="2026-02-28T10:10:00Z")
        )
        events_after_first = db_writer.get_total_events()

        # Realistic replay: new escrow_id (also inside the event payload), new timestamps.
        replay = db_writer.escrow_lock(
            _escrow_body(agent_id, task_id, 30, now="2026-02-28T10:10:04Z")
        )

        assert first["event_id"] > 0
        assert first["balance_after"] == 70
        assert replay["escrow_id"] == first["escrow_id"]
        assert replay["balance_after"] == 70
        assert replay["event_id"] == first["event_id"]
        assert db_writer.get_total_events() == events_after_first

    def test_credit_replay_of_legacy_row_returns_none(self, db_writer: DbWriter) -> None:
        """A row written before the event_id column existed cannot name its event."""
        agent_id = _register_agent(db_writer, "Alice")
        _create_account(db_writer, agent_id, balance=100)
        first = db_writer.credit_account(
            _credit_body(agent_id, "salary_round_1", 25, now="2026-02-28T10:05:00Z")
        )
        db_writer._db.execute(
            "UPDATE bank_transactions SET event_id = NULL WHERE tx_id = ?",
            (first["tx_id"],),
        )
        db_writer._db.commit()

        replay = db_writer.credit_account(
            _credit_body(agent_id, "salary_round_1", 25, now="2026-02-28T10:05:09Z")
        )

        assert replay["event_id"] is None
        assert replay["balance_after"] == 125

    def test_escrow_lock_replay_of_legacy_row_returns_none(self, db_writer: DbWriter) -> None:
        """A row written before the event_id column existed cannot name its event."""
        agent_id = _register_agent(db_writer, "Alice")
        _create_account(db_writer, agent_id, balance=100)
        task_id = f"t-{uuid4()}"
        first = db_writer.escrow_lock(
            _escrow_body(agent_id, task_id, 30, now="2026-02-28T10:10:00Z")
        )
        db_writer._db.execute(
            "UPDATE bank_escrow SET event_id = NULL WHERE escrow_id = ?",
            (first["escrow_id"],),
        )
        db_writer._db.commit()

        replay = db_writer.escrow_lock(
            _escrow_body(agent_id, task_id, 30, now="2026-02-28T10:10:04Z")
        )

        assert replay["event_id"] is None
        assert replay["balance_after"] == 70


# ---------------------------------------------------------------------------
# GAP-C4 — schema load failures are fatal, repeated startup is a no-op
# ---------------------------------------------------------------------------


def _open_writer(db_path: str, schema_sql: str | None) -> Iterator[DbWriter]:
    writer = DbWriter(
        db_path=db_path,
        busy_timeout_ms=BUSY_TIMEOUT_MS,
        journal_mode=JOURNAL_MODE,
        schema_sql=schema_sql,
    )
    yield writer
    writer.close()


@pytest.mark.unit
class TestSchemaInitIsStrict:
    """A broken schema kills startup; a healthy one survives repeated startups."""

    def test_repeated_init_preserves_data(self, tmp_db_path: str, schema_sql: str) -> None:
        first = next(_open_writer(tmp_db_path, schema_sql))
        agent_id = _register_agent(first, "Alice")
        first.close()

        second = next(_open_writer(tmp_db_path, schema_sql))
        try:
            cursor = second._db.execute(
                "SELECT COUNT(*) FROM identity_agents WHERE agent_id = ?",
                (agent_id,),
            )
            assert int(cursor.fetchone()[0]) == 1
        finally:
            second.close()

    def test_malformed_schema_raises(self, tmp_db_path: str) -> None:
        with pytest.raises(sqlite3.OperationalError):
            next(_open_writer(tmp_db_path, "CREATE TABLE broken (;"))

    def test_event_id_columns_are_added_to_legacy_bank_tables(
        self, tmp_db_path: str, schema_sql: str
    ) -> None:
        """A database predating event_id gains the column without losing rows."""
        conn = sqlite3.connect(tmp_db_path)
        conn.execute(
            "CREATE TABLE bank_transactions ("
            "tx_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, type TEXT NOT NULL, "
            "amount INTEGER NOT NULL, balance_after INTEGER NOT NULL, "
            "reference TEXT NOT NULL, timestamp TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE bank_escrow ("
            "escrow_id TEXT PRIMARY KEY, payer_account_id TEXT NOT NULL, "
            "amount INTEGER NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL, "
            "created_at TEXT NOT NULL, resolved_at TEXT)"
        )
        conn.execute(
            "INSERT INTO bank_transactions VALUES "
            "('tx-legacy', 'a-1', 'credit', 10, 10, 'ref', '2026-01-01T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        writer = next(_open_writer(tmp_db_path, schema_sql))
        try:
            tx_columns = [
                str(row[1]) for row in writer._db.execute("PRAGMA table_info(bank_transactions)")
            ]
            escrow_columns = [
                str(row[1]) for row in writer._db.execute("PRAGMA table_info(bank_escrow)")
            ]
            legacy = writer._db.execute(
                "SELECT event_id FROM bank_transactions WHERE tx_id = 'tx-legacy'"
            ).fetchone()

            assert tx_columns.count("event_id") == 1
            assert escrow_columns.count("event_id") == 1
            assert legacy[0] is None
        finally:
            writer.close()

    def test_schema_referencing_missing_table_raises(self, tmp_db_path: str) -> None:
        with pytest.raises(sqlite3.OperationalError):
            next(_open_writer(tmp_db_path, "CREATE INDEX idx_x ON no_such_table (col);"))
