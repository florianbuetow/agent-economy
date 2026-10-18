"""Cross-cutting tests — Categories 16-20 (Event Integrity, Atomicity,
Concurrency, HTTP Misuse, Security).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.signing import public_key_to_b64
from service_commons.exceptions import ServiceError

from db_gateway_service.services import court_writer
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import parse_task_operation_request
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event

if TYPE_CHECKING:
    import sqlite3

    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

# ---------------------------------------------------------------------------
# Helpers — set up prerequisite data without HTTP
# ---------------------------------------------------------------------------


def _register_agent(db_writer: DbWriter, name: str = "TestAgent") -> str:
    """Register an agent directly and return agent_id."""
    aid = f"a-{uuid4()}"
    db_writer.register_agent(
        {
            "agent_id": aid,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        }
    )
    return aid


def _create_funded_account(
    db_writer: DbWriter,
    agent_id: str,
    balance: int,
) -> None:
    """Create a bank account with initial balance."""
    tx_id = f"tx-{uuid4()}"
    db_writer.create_account(
        {
            "operation_id": f"op-{uuid4()}",
            "account_id": agent_id,
            "balance": balance,
            "created_at": "2026-02-28T10:00:00Z",
            "initial_credit": {
                "tx_id": tx_id,
                "amount": balance,
                "reference": "initial_balance",
                "timestamp": "2026-02-28T10:00:00Z",
            },
            "event": make_event(source="bank", event_type="account.created"),
        }
    )


def _create_zero_account(db_writer: DbWriter, agent_id: str) -> None:
    """Create a bank account with zero balance."""
    db_writer.create_account(
        {
            "account_id": agent_id,
            "balance": 0,
            "created_at": "2026-02-28T10:00:00Z",
            "event": make_event(source="bank", event_type="account.created"),
        }
    )


def _lock_escrow(
    db_writer: DbWriter,
    payer_id: str,
    amount: int,
    task_id: str | None = None,
) -> str:
    """Lock escrow and return escrow_id."""
    esc_id = f"esc-{uuid4()}"
    tid = task_id if task_id is not None else f"t-{uuid4()}"
    db_writer.escrow_lock(
        {
            "operation_id": f"op-{uuid4()}",
            "escrow_id": esc_id,
            "payer_account_id": payer_id,
            "amount": amount,
            "task_id": tid,
            "created_at": "2026-02-28T10:01:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked"),
        }
    )
    return esc_id


def _create_task_for_agent(
    db_writer: DbWriter,
    poster_id: str,
    escrow_id: str,
) -> str:
    """Create a task and return task_id."""
    escrow = db_writer.connection.execute(
        "SELECT task_id FROM bank_escrow WHERE escrow_id=?", (escrow_id,)
    ).fetchone()
    assert escrow is not None
    tid = str(escrow[0])
    db_writer.create_task(
        {
            "task_id": tid,
            "poster_id": poster_id,
            "title": "Test task",
            "spec": "Do something",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 3600,
            "acceptance_window_seconds": 3600,
            "deadline_seconds": 86400,
            "review_deadline_seconds": 3600,
            "bidding_deadline": "2026-02-28T11:00:00Z",
            "escrow_id": escrow_id,
            "created_at": "2026-02-28T10:00:00Z",
            "event": make_event(
                source="board",
                event_type="task.created",
                task_id=tid,
                agent_id=poster_id,
            ),
        }
    )
    return tid


def _file_claim(
    db_writer: DbWriter,
    task_id: str,
    claimant_id: str,
    respondent_id: str,
    status: str = "filed",
) -> str:
    """File a claim and return claim_id."""
    cid = f"clm-{uuid4()}"
    db_writer.file_claim(
        {
            "claim_id": cid,
            "task_id": task_id,
            "claimant_id": claimant_id,
            "respondent_id": respondent_id,
            "reason": "Test dispute reason",
            "status": status,
            "filed_at": "2026-02-28T12:00:00Z",
            "rebuttal_deadline": "2026-02-28T14:00:00Z",
            "event": make_event(
                source="court",
                event_type="claim.filed",
                task_id=task_id,
                agent_id=claimant_id,
            ),
        }
    )
    return cid


def _prepare_disputed_claim(
    writer: DbWriter, task_id: str, poster_id: str, worker_id: str, escrow_id: str
) -> str:
    """Persist an admitted dispute before injecting the original rollback failure."""
    clock = {"now": parse_utc_text_to_epoch_us("2026-02-28T10:30:00Z")}
    key = Ed25519PrivateKey.generate().public_key()
    operations = TaskOperationWriter(
        writer.connection,
        lambda: clock["now"],
        "system-atomicity-test",
        f"ed25519:{public_key_to_b64(key)}",
    )

    def claim(
        command: str, status: str, version: int, actor_id: str, arguments: dict[str, object]
    ) -> dict[str, Any]:
        return operations.claim_operation(
            parse_task_operation_request(
                {
                    "protocol_version": 1,
                    "operation_id": f"op-{uuid4()}",
                    "task_id": task_id,
                    "command": command,
                    "expected_status": status,
                    "expected_lifecycle_version": version,
                    "actor_kind": "agent",
                    "actor_id": actor_id,
                    "arguments": arguments,
                }
            ),
            "gateway-task-board",
        )

    bid_id = f"bid-{uuid4()}"
    bid = claim(
        "submit_bid", "open", 0, worker_id, {"bid_id": bid_id, "bidder_id": worker_id, "amount": 50}
    )
    assert bid["operation_state"] == "completed"
    accepted = claim(
        "accept_bid",
        "open",
        0,
        poster_id,
        {
            "bid_id": bid_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "reward": 100,
            "bid_amount": 50,
        },
    )
    assert accepted["operation_state"] == "completed"
    writer.record_asset(
        {
            "asset_id": f"asset-{uuid4()}",
            "task_id": task_id,
            "uploader_id": worker_id,
            "filename": "answer.txt",
            "content_type": "text/plain",
            "size_bytes": 1,
            "storage_path": f"{task_id}/answer.txt",
            "uploaded_at": "2026-02-28T11:00:00Z",
            "event": make_event(source="board", event_type="asset.uploaded", task_id=task_id),
        },
        None,
    )
    clock["now"] = parse_utc_text_to_epoch_us("2026-02-28T11:30:00Z")
    submitted = claim("submit_deliverable", "accepted", 1, worker_id, {"worker_id": worker_id})
    assert submitted["operation_state"] == "completed"
    clock["now"] = parse_utc_text_to_epoch_us("2026-02-28T12:00:00Z")
    disputed = claim(
        "dispute_task",
        "submitted",
        2,
        poster_id,
        {
            "poster_id": poster_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "reward": 100,
            "reason": "Test dispute reason",
        },
    )
    assert disputed["operation_state"] == "claimed"
    claim_id = _file_claim(writer, task_id, poster_id, worker_id, status="filed")
    finalized = operations.finalize_operation(disputed["operation_id"], "gateway-task-board")
    assert finalized["operation_state"] == "completed"
    assert finalized["result"]["dispute_id"] == claim_id
    return claim_id


def _count_rows(db: sqlite3.Connection, table: str) -> int:
    """Count rows in a table."""
    cursor = db.execute(f"SELECT COUNT(*) FROM {table}")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


# ===================================================================
# Category 16: Event Integrity
# ===================================================================


@pytest.mark.unit
class TestEventIntegrity:
    """Event Integrity tests — EVT-01 through EVT-05."""

    def test_event_ids_monotonically_increasing(self, app_with_writer: TestClient) -> None:
        """EVT-01: Event IDs are monotonically increasing across 5 writes."""
        event_ids: list[int] = []
        for _ in range(5):
            resp = app_with_writer.post(
                "/identity/agents",
                json={
                    "agent_id": f"a-{uuid4()}",
                    "name": "Agent",
                    "public_key": f"ed25519:{uuid4()}",
                    "registered_at": "2026-02-28T10:00:00Z",
                    "event": make_event(),
                },
            )
            assert resp.status_code == 201
            event_ids.append(resp.json()["event_id"])

        for i in range(1, len(event_ids)):
            assert event_ids[i] > event_ids[i - 1]

    def test_event_contains_correct_source_and_type(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """EVT-02: Event contains correct source and type."""
        # Register an agent (source: identity, type: agent.registered)
        aid = f"a-{uuid4()}"
        resp1 = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="identity", event_type="agent.registered"),
            },
        )
        assert resp1.status_code == 201
        eid1 = resp1.json()["event_id"]

        # Create account and credit (source: bank, type: salary.paid)
        app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": aid,
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        resp2 = app_with_writer.post(
            "/bank/credit",
            json={
                "operation_id": f"op-{uuid4()}",
                "tx_id": f"tx-{uuid4()}",
                "account_id": aid,
                "amount": 500,
                "reference": f"salary_{uuid4()}",
                "timestamp": "2026-02-28T10:01:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert resp2.status_code == 200
        eid2 = resp2.json()["receipt"]["event_id"]

        # Query events table directly
        row1 = db_writer._db.execute(
            "SELECT event_source, event_type FROM events WHERE event_id = ?",
            (eid1,),
        ).fetchone()
        assert row1 is not None
        assert row1[0] == "identity"
        assert row1[1] == "agent.registered"

        row2 = db_writer._db.execute(
            "SELECT event_source, event_type FROM events WHERE event_id = ?",
            (eid2,),
        ).fetchone()
        assert row2 is not None
        assert row2[0] == "bank"
        assert row2[1] == "salary.paid"

    def test_event_task_id_and_agent_id_match_request(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """EVT-03: Event task_id and agent_id match request."""
        # Setup: register agent, create account, fund, lock escrow, create task
        poster_id = f"a-{uuid4()}"
        app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": poster_id,
                "name": "Poster",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        app_with_writer.post(
            "/bank/accounts",
            json={
                "operation_id": f"op-{uuid4()}",
                "account_id": poster_id,
                "balance": 500,
                "created_at": "2026-02-28T10:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": 500,
                    "reference": "initial_balance",
                    "timestamp": "2026-02-28T10:00:00Z",
                },
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        esc_id = f"esc-{uuid4()}"
        task_id = f"t-{uuid4()}"
        app_with_writer.post(
            "/bank/escrow/lock",
            json={
                "operation_id": f"op-{uuid4()}",
                "escrow_id": esc_id,
                "payer_account_id": poster_id,
                "amount": 100,
                "task_id": task_id,
                "created_at": "2026-02-28T10:01:00Z",
                "tx_id": f"tx-{uuid4()}",
                "event": make_event(source="bank", event_type="escrow.locked"),
            },
        )
        resp = app_with_writer.post(
            "/board/tasks",
            json={
                "task_id": task_id,
                "poster_id": poster_id,
                "title": "Test task",
                "spec": "Do something",
                "reward": 100,
                "status": "open",
                "bidding_deadline_seconds": 3600,
                "acceptance_window_seconds": 3600,
                "deadline_seconds": 86400,
                "review_deadline_seconds": 3600,
                "bidding_deadline": "2026-02-28T11:00:00Z",
                "escrow_id": esc_id,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(
                    source="board",
                    event_type="task.created",
                    task_id=task_id,
                    agent_id=poster_id,
                ),
            },
        )
        assert resp.status_code == 201
        eid = resp.json()["event_id"]

        row = db_writer._db.execute(
            "SELECT task_id, agent_id FROM events WHERE event_id = ?",
            (eid,),
        ).fetchone()
        assert row is not None
        assert row[0] == task_id
        assert row[1] == poster_id

    def test_event_summary_and_payload_stored(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """EVT-04: Event summary and payload are stored as provided."""
        aid = f"a-{uuid4()}"
        custom_summary = "Agent Alice registered successfully"
        custom_payload = '{"agent_name": "Alice"}'
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(
                    summary=custom_summary,
                    payload=custom_payload,
                ),
            },
        )
        assert resp.status_code == 201
        eid = resp.json()["event_id"]

        row = db_writer._db.execute(
            "SELECT summary, payload FROM events WHERE event_id = ?",
            (eid,),
        ).fetchone()
        assert row is not None
        assert row[0] == custom_summary
        assert row[1] == custom_payload

    def test_failed_write_does_not_create_event(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """EVT-05: Failed write does not create an event."""
        events_before = _count_rows(db_writer._db, "events")

        # Attempt to create account for non-existent agent (FK violation)
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": f"a-{uuid4()}",
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert resp.status_code == 409

        events_after = _count_rows(db_writer._db, "events")
        assert events_after == events_before


# ===================================================================
# Category 17: Atomicity
# ===================================================================


@pytest.mark.unit
class TestAtomicity:
    """Atomicity tests — ATOM-01 through ATOM-05."""

    def test_account_creation_credit_event_all_or_nothing(self, db_writer: DbWriter) -> None:
        """ATOM-01: Account creation + credit + event are all-or-nothing."""
        # Register an agent so FK is satisfied
        aid = _register_agent(db_writer)
        _create_funded_account(db_writer, aid, balance=100)

        # Verify all rows exist together
        acct = db_writer._db.execute(
            "SELECT account_id FROM bank_accounts WHERE account_id = ?",
            (aid,),
        ).fetchone()
        assert acct is not None

        tx = db_writer._db.execute(
            "SELECT tx_id FROM bank_transactions WHERE account_id = ?",
            (aid,),
        ).fetchone()
        assert tx is not None

        events = db_writer._db.execute(
            "SELECT event_id FROM events WHERE event_source = 'bank'"
        ).fetchall()
        assert len(events) >= 1

    def test_escrow_lock_failure_no_partial_state(self, db_writer: DbWriter) -> None:
        """ATOM-02: Escrow lock failure does not leave partial state."""
        aid = _register_agent(db_writer)
        _create_funded_account(db_writer, aid, balance=10)

        escrow_before = _count_rows(db_writer._db, "bank_escrow")
        tx_before = _count_rows(db_writer._db, "bank_transactions")
        events_before = _count_rows(db_writer._db, "events")

        # Attempt escrow lock with insufficient funds
        with pytest.raises(ServiceError) as exc_info:
            db_writer.escrow_lock(
                {
                    "operation_id": f"op-{uuid4()}",
                    "escrow_id": f"esc-{uuid4()}",
                    "payer_account_id": aid,
                    "amount": 50,
                    "task_id": f"t-{uuid4()}",
                    "created_at": "2026-02-28T10:01:00Z",
                    "tx_id": f"tx-{uuid4()}",
                    "event": make_event(source="bank", event_type="escrow.locked"),
                }
            )
        assert exc_info.value.error == "insufficient_funds"

        assert _count_rows(db_writer._db, "bank_escrow") == escrow_before
        assert _count_rows(db_writer._db, "bank_transactions") == tx_before
        assert _count_rows(db_writer._db, "events") == events_before

    def test_escrow_release_failure_no_partial_state(self, db_writer: DbWriter) -> None:
        """ATOM-03: Escrow release failure does not leave partial state."""
        aid = _register_agent(db_writer)
        _create_funded_account(db_writer, aid, balance=200)
        task_id = f"t-{uuid4()}"
        esc_id = _lock_escrow(db_writer, aid, amount=100, task_id=task_id)

        tx_before = _count_rows(db_writer._db, "bank_transactions")
        events_before = _count_rows(db_writer._db, "events")

        # Attempt release to non-existent account
        with pytest.raises(ServiceError) as exc_info:
            db_writer.escrow_release(
                {
                    "operation_id": f"op-{uuid4()}",
                    "escrow_id": esc_id,
                    "recipient_account_id": f"a-{uuid4()}",
                    "source_reference": f"release-{task_id}",
                    "tx_id": f"tx-{uuid4()}",
                    "resolved_at": "2026-02-28T12:00:00Z",
                    "event": make_event(source="bank", event_type="escrow.released"),
                }
            )
        assert exc_info.value.error == "account_not_found"

        # Escrow remains locked
        row = db_writer._db.execute(
            "SELECT status FROM bank_escrow WHERE escrow_id = ?",
            (esc_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "locked"

        assert _count_rows(db_writer._db, "bank_transactions") == tx_before
        assert _count_rows(db_writer._db, "events") == events_before

    def test_split_failure_no_partial_state(self, db_writer: DbWriter) -> None:
        """ATOM-04: Split failure does not leave partial state."""
        aid = _register_agent(db_writer)
        _create_funded_account(db_writer, aid, balance=200)
        task_id = f"t-{uuid4()}"
        esc_id = _lock_escrow(db_writer, aid, amount=100, task_id=task_id)

        # Create worker account
        worker_id = _register_agent(db_writer, name="Worker")
        _create_zero_account(db_writer, worker_id)

        tx_before = _count_rows(db_writer._db, "bank_transactions")
        events_before = _count_rows(db_writer._db, "events")

        # Attempt split with an invalid percentage.
        with pytest.raises(ServiceError) as exc_info:
            db_writer.escrow_split(
                {
                    "operation_id": f"op-{uuid4()}",
                    "escrow_id": esc_id,
                    "worker_account_id": worker_id,
                    "poster_account_id": aid,
                    "worker_pct": 101,
                    "source_reference": f"split-{task_id}",
                    "worker_tx_id": f"tx-{uuid4()}",
                    "poster_tx_id": f"tx-{uuid4()}",
                    "resolved_at": "2026-02-28T12:00:00Z",
                    "event": make_event(source="bank", event_type="escrow.split"),
                }
            )
        assert exc_info.value.error == "invalid_financial_request"

        # Escrow still locked
        row = db_writer._db.execute(
            "SELECT status FROM bank_escrow WHERE escrow_id = ?",
            (esc_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "locked"

        assert _count_rows(db_writer._db, "bank_transactions") == tx_before
        assert _count_rows(db_writer._db, "events") == events_before

    def test_rebuttal_with_fk_violation_is_atomic(
        self, db_writer: DbWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ATOM-05: A real event FK failure rolls back all preceding rebuttal writes."""
        alice = _register_agent(db_writer, name="Alice")
        bob = _register_agent(db_writer, name="Bob")
        _create_funded_account(db_writer, alice, balance=500)
        esc_id = _lock_escrow(db_writer, alice, amount=100)
        task_id = _create_task_for_agent(db_writer, alice, esc_id)
        claim_id = _prepare_disputed_claim(db_writer, task_id, alice, bob, esc_id)

        rebuttals_before = _count_rows(db_writer._db, "court_rebuttals")
        events_before = _count_rows(db_writer._db, "events")

        before = tuple(db_writer.connection.iterdump())
        real_insert_event = court_writer.insert_event
        reached_event: list[bool] = []

        def fail_event(cursor: sqlite3.Cursor, event: dict[str, Any]) -> int:
            assert cursor.execute("SELECT COUNT(*) FROM court_rebuttals").fetchone()[0] == 1
            assert (
                cursor.execute(
                    "SELECT status FROM court_claims WHERE claim_id=?", (claim_id,)
                ).fetchone()[0]
                == "rebuttal"
            )
            assert (
                cursor.execute(
                    "SELECT rebuttal_submitted_at FROM board_tasks WHERE task_id=?", (task_id,)
                ).fetchone()[0]
                == "2026-02-28T13:00:00Z"
            )
            reached_event.append(True)
            return real_insert_event(cursor, {**event, "agent_id": f"a-missing-{uuid4()}"})

        monkeypatch.setattr(court_writer, "insert_event", fail_event)
        # A valid worker reaches the write path; the real event INSERT raises the FK failure.
        with pytest.raises(ServiceError) as exc_info:
            db_writer.submit_rebuttal(
                {
                    "rebuttal_id": f"reb-{uuid4()}",
                    "claim_id": claim_id,
                    "agent_id": bob,
                    "content": "My rebuttal",
                    "submitted_at": "2026-02-28T13:00:00Z",
                    "claim_status_update": "rebuttal",
                    "event": make_event(source="court", event_type="rebuttal.submitted"),
                },
                None,
            )
        assert exc_info.value.error == "foreign_key_violation"
        assert reached_event == [True]
        assert tuple(db_writer.connection.iterdump()) == before

        # Claim status unchanged
        row = db_writer._db.execute(
            "SELECT status FROM court_claims WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "filed"

        assert _count_rows(db_writer._db, "court_rebuttals") == rebuttals_before
        assert _count_rows(db_writer._db, "events") == events_before


# ===================================================================
# Category 18: Concurrency
# ===================================================================


@pytest.mark.unit
class TestConcurrency:
    """Concurrency tests — CONC-01 through CONC-03."""

    def test_concurrent_escrow_locks_serialize(self, app_with_writer: TestClient) -> None:
        """CONC-01: Concurrent escrow locks serialize correctly."""
        # Setup: register, fund account with 100
        aid = f"a-{uuid4()}"
        app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        app_with_writer.post(
            "/bank/accounts",
            json={
                "operation_id": f"op-{uuid4()}",
                "account_id": aid,
                "balance": 100,
                "created_at": "2026-02-28T10:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": 100,
                    "reference": "initial_balance",
                    "timestamp": "2026-02-28T10:00:00Z",
                },
                "event": make_event(source="bank", event_type="account.created"),
            },
        )

        def _lock(task_suffix: int) -> int:
            resp = app_with_writer.post(
                "/bank/escrow/lock",
                json={
                    "operation_id": f"op-{uuid4()}",
                    "escrow_id": f"esc-{uuid4()}",
                    "payer_account_id": aid,
                    "amount": 60,
                    "task_id": f"t-{uuid4()}-{task_suffix}",
                    "created_at": "2026-02-28T10:01:00Z",
                    "tx_id": f"tx-{uuid4()}",
                    "event": make_event(source="bank", event_type="escrow.locked"),
                },
            )
            return resp.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_lock, i) for i in range(2)]
            results = [f.result() for f in as_completed(futures)]

        # Exactly one succeeds (201), one fails (402)
        assert sorted(results) == [201, 402]

    def test_concurrent_duplicate_registrations_safe(self, app_with_writer: TestClient) -> None:
        """CONC-02: Concurrent duplicate registrations are safe."""
        aid = f"a-{uuid4()}"
        pk = f"ed25519:{uuid4()}"
        body = {
            "agent_id": aid,
            "name": "Alice",
            "public_key": pk,
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        }

        def _register() -> int:
            resp = app_with_writer.post("/identity/agents", json=body)
            return resp.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_register) for _ in range(2)]
            results = [f.result() for f in as_completed(futures)]

        # One 201 and one 201 (idempotent) or 409 (conflict)
        assert 201 in results
        # The other must be 201 (idempotent replay) or 409
        other_codes = [r for r in results if r != 201]
        for code in other_codes:
            assert code in (201, 409)

    def test_concurrent_credits_same_reference_idempotent(
        self, app_with_writer: TestClient
    ) -> None:
        """CONC-03: Concurrent credits with same reference are idempotent."""
        aid = f"a-{uuid4()}"
        app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": aid,
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )

        tx_id = f"tx-{uuid4()}"
        ref = f"salary_{uuid4()}"
        body = {
            "operation_id": f"op-{uuid4()}",
            "tx_id": tx_id,
            "account_id": aid,
            "amount": 500,
            "reference": ref,
            "timestamp": "2026-02-28T10:01:00Z",
            "event": make_event(source="bank", event_type="salary.paid"),
        }

        def _credit() -> tuple[int, dict]:  # type: ignore[type-arg]
            resp = app_with_writer.post("/bank/credit", json=body)
            return resp.status_code, resp.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_credit) for _ in range(2)]
            results = [f.result() for f in as_completed(futures)]

        # Both should return 200
        for status_code, _ in results:
            assert status_code == 200

        # Both return the exact immutable receipt.
        bodies = [r[1] for r in results]
        assert bodies[0]["receipt"] == bodies[1]["receipt"]
        assert bodies[0]["receipt"]["result"]["balance_after"] == 500


# ===================================================================
# Category 19: HTTP Method and Content Type Misuse
# ===================================================================


@pytest.mark.unit
class TestHTTPMisuse:
    """HTTP method/content-type misuse tests — HTTP-01 through HTTP-04."""

    def test_wrong_method_on_defined_routes(self, app_with_writer: TestClient) -> None:
        """HTTP-01: Wrong method on defined routes returns 405."""
        # GET /board/tasks, GET /reputation/feedback, and GET /court/claims are
        # real list-read endpoints (Category 22) and are deliberately excluded
        # here, same as GET /identity/agents above them in the spec — sending
        # GET to those exact paths hits the real reader route, not a 405
        # wrong-method rejection (WP-13 item 8: they used to incorrectly
        # return 405 for an unrelated reason — an uninitialized reader — which
        # this list conflated with genuine method misuse; see
        # test_reader_not_ready_503.py for that scenario).
        post_only_routes = [
            "/bank/accounts",
            "/bank/credit",
            "/bank/escrow/lock",
            "/bank/escrow/release",
            "/bank/escrow/split",
            "/board/bids",
            "/board/tasks/t-fake/status",
            "/board/assets",
            "/court/rebuttals",
            "/court/rulings",
        ]
        for route in post_only_routes:
            resp = app_with_writer.get(route)
            assert resp.status_code == 405, f"GET {route} should be 405"
            assert resp.json()["error"] == "method_not_allowed"

        # POST /health (GET only)
        resp = app_with_writer.post(
            "/health",
            json={},
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 405
        assert resp.json()["error"] == "method_not_allowed"

    def test_wrong_content_type(self, app_with_writer: TestClient) -> None:
        """HTTP-02: Wrong content type returns 415."""
        resp = app_with_writer.post(
            "/identity/agents",
            content=b'{"agent_id": "a-test"}',
            headers={"content-type": "text/plain"},
        )
        assert resp.status_code == 415
        assert resp.json()["error"] == "unsupported_media_type"

    def test_oversized_request_body(self, app_with_writer: TestClient) -> None:
        """HTTP-03: Oversized request body returns 413."""
        # max_body_size is 1048576 (1 MiB) — send more than that
        oversized_body = b'{"data": "' + b"x" * 1048577 + b'"}'
        resp = app_with_writer.post(
            "/identity/agents",
            content=oversized_body,
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"] == "payload_too_large"

    def test_malformed_json_on_various_endpoints(self, app_with_writer: TestClient) -> None:
        """HTTP-04: Malformed JSON on various endpoints returns 400."""
        endpoints = [
            "/bank/credit",
            "/board/tasks",
            "/court/claims",
        ]
        for endpoint in endpoints:
            resp = app_with_writer.post(
                endpoint,
                content=b"{invalid json",
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 400, f"{endpoint} should return 400"
            assert resp.json()["error"] == "invalid_json"


# ===================================================================
# Category 20: Cross-Cutting Security Assertions
# ===================================================================


@pytest.mark.unit
class TestSecurity:
    """Cross-cutting security tests — SEC-01 through SEC-05."""

    def test_error_envelope_consistency(self, app_with_writer: TestClient) -> None:
        """SEC-01: Error envelope consistency across error codes."""
        # Trigger various error codes and check envelope structure
        error_triggers = [
            # missing_field: missing name
            (
                "/identity/agents",
                {
                    "agent_id": f"a-{uuid4()}",
                    "public_key": f"ed25519:{uuid4()}",
                    "registered_at": "2026-02-28T10:00:00Z",
                    "event": make_event(),
                },
            ),
            # invalid_json
            None,  # handled separately below
        ]

        # missing_field
        resp = app_with_writer.post("/identity/agents", json=error_triggers[0][1])
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body
        assert isinstance(body["error"], str)
        assert "message" in body
        assert isinstance(body["message"], str)
        assert "details" in body
        assert isinstance(body["details"], dict)

        # invalid_json
        resp = app_with_writer.post(
            "/identity/agents",
            content=b"{bad",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body
        assert isinstance(body["error"], str)
        assert "message" in body
        assert isinstance(body["message"], str)
        assert "details" in body
        assert isinstance(body["details"], dict)

        # account_not_found
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "operation_id": f"op-{uuid4()}",
                "tx_id": f"tx-{uuid4()}",
                "account_id": f"a-{uuid4()}",
                "amount": 100,
                "reference": "test",
                "timestamp": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body and isinstance(body["error"], str)
        assert "message" in body and isinstance(body["message"], str)
        assert "details" in body and isinstance(body["details"], dict)

        # foreign_key_violation (create account for non-existent agent)
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": f"a-{uuid4()}",
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert "error" in body and isinstance(body["error"], str)
        assert "message" in body and isinstance(body["message"], str)
        assert "details" in body and isinstance(body["details"], dict)

    def test_no_internal_error_leakage(self, app_with_writer: TestClient) -> None:
        """SEC-02: No internal error leakage in error messages."""
        leak_patterns = [
            "Traceback",
            "File ",
            "line ",
            ".py",
            "sqlite3",
            "IntegrityError",
            "OperationalError",
            "INSERT INTO",
            "SELECT ",
            "UPDATE ",
            "DELETE ",
            "PRAGMA",
        ]

        # invalid_json
        resp = app_with_writer.post(
            "/bank/credit",
            content=b"{bad",
            headers={"content-type": "application/json"},
        )
        msg = resp.json()["message"]
        for pattern in leak_patterns:
            assert pattern not in msg, f"Leaked: {pattern}"

        # missing_field
        resp = app_with_writer.post(
            "/identity/agents",
            json={"agent_id": f"a-{uuid4()}", "event": make_event()},
        )
        msg = resp.json()["message"]
        for pattern in leak_patterns:
            assert pattern not in msg, f"Leaked: {pattern}"

        # account_not_found
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "operation_id": f"op-{uuid4()}",
                "tx_id": f"tx-{uuid4()}",
                "account_id": f"a-{uuid4()}",
                "amount": 100,
                "reference": "test",
                "timestamp": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        msg = resp.json()["message"]
        for pattern in leak_patterns:
            assert pattern not in msg, f"Leaked: {pattern}"

        # foreign_key_violation
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": f"a-{uuid4()}",
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        msg = resp.json()["message"]
        for pattern in leak_patterns:
            assert pattern not in msg, f"Leaked: {pattern}"

        # insufficient_funds — need a funded account with low balance
        aid = f"a-{uuid4()}"
        app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "LowFunds",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        app_with_writer.post(
            "/bank/accounts",
            json={
                "operation_id": f"op-{uuid4()}",
                "account_id": aid,
                "balance": 10,
                "created_at": "2026-02-28T10:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": 10,
                    "reference": "initial_balance",
                    "timestamp": "2026-02-28T10:00:00Z",
                },
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        resp = app_with_writer.post(
            "/bank/escrow/lock",
            json={
                "operation_id": f"op-{uuid4()}",
                "escrow_id": f"esc-{uuid4()}",
                "payer_account_id": aid,
                "amount": 100,
                "task_id": f"t-{uuid4()}",
                "created_at": "2026-02-28T10:01:00Z",
                "tx_id": f"tx-{uuid4()}",
                "event": make_event(source="bank", event_type="escrow.locked"),
            },
        )
        msg = resp.json()["message"]
        for pattern in leak_patterns:
            assert pattern not in msg, f"Leaked: {pattern}"

    def test_ids_in_responses_match_expected_formats(self, app_with_writer: TestClient) -> None:
        """SEC-03: IDs in responses match expected formats."""
        # Register agent
        aid = f"a-{uuid4()}"
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["agent_id"] == aid
        assert isinstance(data["event_id"], int)
        assert data["event_id"] > 0

        # Create account
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "operation_id": f"op-{uuid4()}",
                "account_id": aid,
                "balance": 500,
                "created_at": "2026-02-28T10:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": 500,
                    "reference": "initial_balance",
                    "timestamp": "2026-02-28T10:00:00Z",
                },
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["receipt"]["result"]["account_id"] == aid
        assert isinstance(data["receipt"]["event_id"], int)
        assert data["receipt"]["event_id"] > 0

        # Credit
        tx_id = f"tx-{uuid4()}"
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "operation_id": f"op-{uuid4()}",
                "tx_id": tx_id,
                "account_id": aid,
                "amount": 100,
                "reference": f"salary_{uuid4()}",
                "timestamp": "2026-02-28T10:01:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["receipt"]["legs"][0]["transaction_id"] == tx_id
        assert isinstance(data["receipt"]["event_id"], int)
        assert data["receipt"]["event_id"] > 0
        assert isinstance(data["receipt"]["result"]["balance_after"], int)

    def test_sql_injection_strings_in_id_fields(self, app_with_writer: TestClient) -> None:
        """SEC-04: SQL injection strings in ID fields don't cause injection."""
        # SQL injection in agent_id
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": "' OR '1'='1",
                "name": "Injector",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        # Should succeed (the ID is just a string) or fail with a normal error
        assert resp.status_code in (201, 400, 409)
        if resp.status_code != 201:
            body = resp.json()
            assert "error" in body

        # SQL injection in account_id for credit
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "operation_id": f"op-{uuid4()}",
                "tx_id": f"tx-{uuid4()}",
                "account_id": "'; DROP TABLE bank_accounts;--",
                "amount": 100,
                "reference": "test",
                "timestamp": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        # Should get a proper error, not a 500
        assert resp.status_code in (400, 404, 409)
        body = resp.json()
        assert "error" in body

        # Verify database integrity — bank_accounts table still exists
        # by making a normal request
        aid = f"a-{uuid4()}"
        app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "PostInjection",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": aid,
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert resp.status_code == 201

    def test_no_500_for_documented_error_scenarios(self, app_with_writer: TestClient) -> None:
        """SEC-05: No endpoint returns 500 for documented error scenarios."""
        error_requests = [
            # missing_field
            ("POST", "/identity/agents", {"agent_id": f"a-{uuid4()}"}),
            # invalid_json — special handling
            None,
            # account_not_found
            (
                "POST",
                "/bank/credit",
                {
                    "operation_id": f"op-{uuid4()}",
                    "tx_id": f"tx-{uuid4()}",
                    "account_id": f"a-{uuid4()}",
                    "amount": 100,
                    "reference": "test",
                    "timestamp": "2026-02-28T10:00:00Z",
                    "event": make_event(source="bank", event_type="salary.paid"),
                },
            ),
            # foreign_key_violation
            (
                "POST",
                "/bank/accounts",
                {
                    "account_id": f"a-{uuid4()}",
                    "balance": 0,
                    "created_at": "2026-02-28T10:00:00Z",
                    "event": make_event(source="bank", event_type="account.created"),
                },
            ),
            # empty_updates
            (
                "POST",
                "/board/tasks/t-fake/status",
                {
                    "updates": {},
                    "event": make_event(source="board", event_type="task.updated"),
                },
            ),
            # invalid_amount (negative amount)
            (
                "POST",
                "/bank/credit",
                {
                    "operation_id": f"op-{uuid4()}",
                    "tx_id": f"tx-{uuid4()}",
                    "account_id": f"a-{uuid4()}",
                    "amount": -5,
                    "reference": "test",
                    "timestamp": "2026-02-28T10:00:00Z",
                    "event": make_event(source="bank", event_type="salary.paid"),
                },
            ),
        ]

        for item in error_requests:
            if item is None:
                # invalid_json
                resp = app_with_writer.post(
                    "/identity/agents",
                    content=b"{bad",
                    headers={"content-type": "application/json"},
                )
                assert resp.status_code != 500, "invalid_json returned 500"
                continue

            _, path, body = item
            resp = app_with_writer.post(path, json=body)
            assert resp.status_code != 500, f"{path} returned 500"
