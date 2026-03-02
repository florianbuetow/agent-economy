"""Bank domain tests — Categories 2-6 (ACCT, CR, ELOCK, EREL, ESPL)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_agent(client: TestClient, agent_id: str | None = None, name: str = "Test") -> str:
    """Register an agent and return agent_id."""
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


def _create_account(client: TestClient, agent_id: str, balance: int = 0) -> None:
    """Create account, optionally with initial credit."""
    data: dict[str, object] = {
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
    client.post("/bank/accounts", json=data)


def _fund_account(
    client: TestClient,
    agent_id: str,
    amount: int,
    reference: str | None = None,
) -> TestClient:
    """Credit an account and return the response."""
    ref = reference or f"fund-{uuid4()}"
    return client.post(
        "/bank/credit",
        json={
            "tx_id": f"tx-{uuid4()}",
            "account_id": agent_id,
            "amount": amount,
            "reference": ref,
            "timestamp": "2026-02-28T10:05:00Z",
            "event": make_event(source="bank", event_type="salary.paid"),
        },
    )


def _setup_funded_account(
    client: TestClient,
    agent_id: str | None = None,
    balance: int = 500,
) -> str:
    """Register agent + create account + fund."""
    aid = _register_agent(client, agent_id)
    _create_account(client, aid, balance)
    return aid


def _lock_escrow(
    client: TestClient,
    payer_id: str,
    amount: int,
    task_id: str | None = None,
    escrow_id: str | None = None,
) -> tuple[object, str, str]:
    """Lock escrow and return (response, escrow_id, task_id)."""
    eid = escrow_id or f"esc-{uuid4()}"
    tid = task_id or f"t-{uuid4()}"
    resp = client.post(
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
    return resp, eid, tid


# ===========================================================================
# Category 2: Account Creation (ACCT-01 through ACCT-09)
# ===========================================================================


@pytest.mark.unit
class TestAccountCreation:
    """Tests for POST /bank/accounts."""

    def test_create_account_with_positive_balance(self, app_with_writer: TestClient) -> None:
        """ACCT-01: Create account with positive initial balance."""
        aid = _register_agent(app_with_writer, name="Alice")
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": aid,
                "balance": 50,
                "created_at": "2026-02-28T10:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": 50,
                    "reference": "initial_balance",
                    "timestamp": "2026-02-28T10:00:00Z",
                },
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["account_id"] == aid
        assert "event_id" in data

    def test_create_account_zero_balance(self, app_with_writer: TestClient) -> None:
        """ACCT-02: Create account with zero balance (no initial_credit)."""
        aid = _register_agent(app_with_writer, name="Bob")
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

    def test_initial_credit_transaction_written(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """ACCT-03: Initial credit transaction is written."""
        aid = _register_agent(app_with_writer, name="Alice")
        _create_account(app_with_writer, aid, balance=50)

        cursor = db_writer._db.execute(
            "SELECT account_id, type, amount, reference, balance_after "
            "FROM bank_transactions WHERE account_id = ? AND type = 'credit'",
            (aid,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == aid
        assert row[1] == "credit"
        assert row[2] == 50
        assert row[3] == "initial_balance"
        assert row[4] == 50

    def test_duplicate_account_rejected(self, app_with_writer: TestClient) -> None:
        """ACCT-04: Duplicate account is rejected."""
        aid = _register_agent(app_with_writer)
        _create_account(app_with_writer, aid, balance=0)
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
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
        assert resp.status_code == 409
        assert resp.json()["error"] == "account_exists"

    def test_foreign_key_violation_agent_not_exists(self, app_with_writer: TestClient) -> None:
        """ACCT-05: Foreign key violation when agent does not exist."""
        fake_id = f"a-{uuid4()}"
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": fake_id,
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "foreign_key_violation"

    def test_negative_balance_rejected(self, app_with_writer: TestClient) -> None:
        """ACCT-06: Negative balance is rejected."""
        aid = _register_agent(app_with_writer)
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": aid,
                "balance": -1,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_amount"

    def test_missing_account_id(self, app_with_writer: TestClient) -> None:
        """ACCT-07: Missing required field: account_id."""
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """ACCT-08: Missing event object."""
        aid = _register_agent(app_with_writer)
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": aid,
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_event_written_atomically(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """ACCT-09: Event is written atomically with the account."""
        aid = _register_agent(app_with_writer, name="Alice")
        resp = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": aid,
                "balance": 50,
                "created_at": "2026-02-28T10:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": 50,
                    "reference": "initial_balance",
                    "timestamp": "2026-02-28T10:00:00Z",
                },
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert resp.status_code == 201
        event_id = resp.json()["event_id"]

        # Check account exists
        acct_cursor = db_writer._db.execute(
            "SELECT account_id FROM bank_accounts WHERE account_id = ?",
            (aid,),
        )
        assert acct_cursor.fetchone() is not None

        # Check event exists
        evt_cursor = db_writer._db.execute(
            "SELECT event_id FROM events WHERE event_id = ?",
            (event_id,),
        )
        assert evt_cursor.fetchone() is not None


# ===========================================================================
# Category 3: Credit (CR-01 through CR-11)
# ===========================================================================


@pytest.mark.unit
class TestCredit:
    """Tests for POST /bank/credit."""

    def test_valid_credit_increases_balance(self, app_with_writer: TestClient) -> None:
        """CR-01: Valid credit increases balance."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = _fund_account(app_with_writer, aid, amount=10, reference="salary_round_3")
        assert resp.status_code == 200
        data = resp.json()
        assert "tx_id" in data
        assert "balance_after" in data
        assert "event_id" in data
        assert data["balance_after"] == 110

    def test_multiple_credits_accumulate(self, app_with_writer: TestClient) -> None:
        """CR-02: Multiple credits accumulate correctly."""
        aid = _setup_funded_account(app_with_writer, balance=0)
        resp1 = _fund_account(app_with_writer, aid, amount=30, reference="bonus_1")
        assert resp1.status_code == 200
        assert resp1.json()["balance_after"] == 30

        resp2 = _fund_account(app_with_writer, aid, amount=20, reference="bonus_2")
        assert resp2.status_code == 200
        assert resp2.json()["balance_after"] == 50

    def test_idempotent_credit_returns_same_tx_id(self, app_with_writer: TestClient) -> None:
        """CR-03: Idempotent credit returns same tx_id."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        tx_id = f"tx-{uuid4()}"
        body = {
            "tx_id": tx_id,
            "account_id": aid,
            "amount": 25,
            "reference": "salary_round_1",
            "timestamp": "2026-02-28T10:05:00Z",
            "event": make_event(source="bank", event_type="salary.paid"),
        }
        resp1 = app_with_writer.post("/bank/credit", json=body)
        assert resp1.status_code == 200
        data1 = resp1.json()
        original_balance = data1["balance_after"]

        resp2 = app_with_writer.post("/bank/credit", json=body)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["tx_id"] == data1["tx_id"]
        assert data2["balance_after"] == original_balance

    def test_duplicate_reference_different_amount_rejected(
        self, app_with_writer: TestClient
    ) -> None:
        """CR-04: Duplicate reference with different amount is rejected."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        _fund_account(app_with_writer, aid, amount=25, reference="salary_round_1")
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "tx_id": f"tx-{uuid4()}",
                "account_id": aid,
                "amount": 30,
                "reference": "salary_round_1",
                "timestamp": "2026-02-28T10:05:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "reference_conflict"

    def test_account_not_found(self, app_with_writer: TestClient) -> None:
        """CR-05: Account not found."""
        fake_id = f"a-{uuid4()}"
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "tx_id": f"tx-{uuid4()}",
                "account_id": fake_id,
                "amount": 10,
                "reference": "test",
                "timestamp": "2026-02-28T10:05:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "account_not_found"

    def test_zero_amount_rejected(self, app_with_writer: TestClient) -> None:
        """CR-06: Zero amount is rejected."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "tx_id": f"tx-{uuid4()}",
                "account_id": aid,
                "amount": 0,
                "reference": f"ref-{uuid4()}",
                "timestamp": "2026-02-28T10:05:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_amount"

    def test_negative_amount_rejected(self, app_with_writer: TestClient) -> None:
        """CR-07: Negative amount is rejected."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "tx_id": f"tx-{uuid4()}",
                "account_id": aid,
                "amount": -10,
                "reference": f"ref-{uuid4()}",
                "timestamp": "2026-02-28T10:05:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_amount"

    def test_missing_reference(self, app_with_writer: TestClient) -> None:
        """CR-08: Missing required field: reference."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "tx_id": f"tx-{uuid4()}",
                "account_id": aid,
                "amount": 10,
                "timestamp": "2026-02-28T10:05:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_amount(self, app_with_writer: TestClient) -> None:
        """CR-09: Missing required field: amount."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = app_with_writer.post(
            "/bank/credit",
            json={
                "tx_id": f"tx-{uuid4()}",
                "account_id": aid,
                "reference": "test",
                "timestamp": "2026-02-28T10:05:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_credit_transaction_written(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """CR-10: Credit transaction is written to bank_transactions."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        tx_id = f"tx-{uuid4()}"
        app_with_writer.post(
            "/bank/credit",
            json={
                "tx_id": tx_id,
                "account_id": aid,
                "amount": 10,
                "reference": "bonus",
                "timestamp": "2026-02-28T10:05:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        cursor = db_writer._db.execute(
            "SELECT tx_id, account_id, type, amount, reference "
            "FROM bank_transactions WHERE tx_id = ?",
            (tx_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == tx_id
        assert row[1] == aid
        assert row[2] == "credit"
        assert row[3] == 10
        assert row[4] == "bonus"

    def test_event_written_atomically(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """CR-11: Event is written atomically with the credit."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = _fund_account(app_with_writer, aid, amount=10)
        assert resp.status_code == 200
        event_id = resp.json()["event_id"]

        cursor = db_writer._db.execute(
            "SELECT event_id FROM events WHERE event_id = ?",
            (event_id,),
        )
        assert cursor.fetchone() is not None


# ===========================================================================
# Category 4: Escrow Lock (ELOCK-01 through ELOCK-15)
# ===========================================================================


@pytest.mark.unit
class TestEscrowLock:
    """Tests for POST /bank/escrow/lock."""

    def test_valid_escrow_lock(self, app_with_writer: TestClient) -> None:
        """ELOCK-01: Valid escrow lock."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp, eid, _tid = _lock_escrow(app_with_writer, aid, amount=30)
        assert resp.status_code == 201
        data = resp.json()
        assert data["escrow_id"] == eid
        assert "balance_after" in data
        assert data["balance_after"] == 70
        assert "event_id" in data

    def test_balance_decreases_by_lock_amount(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """ELOCK-02: Balance decreases by lock amount."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        _lock_escrow(app_with_writer, aid, amount=30)
        cursor = db_writer._db.execute(
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            (aid,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 70

    def test_escrow_record_created_locked(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """ELOCK-03: Escrow record is created with status locked."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        _resp, eid, _tid = _lock_escrow(app_with_writer, aid, amount=30)
        cursor = db_writer._db.execute(
            "SELECT escrow_id, payer_account_id, amount, task_id, status, resolved_at "
            "FROM bank_escrow WHERE escrow_id = ?",
            (eid,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == eid
        assert row[1] == aid
        assert row[2] == 30
        assert row[4] == "locked"
        assert row[5] is None

    def test_escrow_lock_transaction_written(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """ELOCK-04: Escrow lock transaction is written."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        tid = f"t-{uuid4()}"
        _lock_escrow(app_with_writer, aid, amount=30, task_id=tid)
        cursor = db_writer._db.execute(
            "SELECT type, amount, reference, balance_after "
            "FROM bank_transactions "
            "WHERE account_id = ? AND type = 'escrow_lock'",
            (aid,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "escrow_lock"
        assert row[1] == 30
        assert row[2] == tid
        assert row[3] == 70

    def test_insufficient_funds(self, app_with_writer: TestClient) -> None:
        """ELOCK-05: Insufficient funds."""
        aid = _setup_funded_account(app_with_writer, balance=10)
        resp, _eid, _tid = _lock_escrow(app_with_writer, aid, amount=50)
        assert resp.status_code == 402
        assert resp.json()["error"] == "insufficient_funds"

    def test_insufficient_funds_no_balance_change(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """ELOCK-06: Insufficient funds does not modify balance."""
        aid = _setup_funded_account(app_with_writer, balance=10)
        _lock_escrow(app_with_writer, aid, amount=50)
        cursor = db_writer._db.execute(
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            (aid,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 10

    def test_account_not_found(self, app_with_writer: TestClient) -> None:
        """ELOCK-07: Account not found."""
        fake_id = f"a-{uuid4()}"
        resp, _eid, _tid = _lock_escrow(app_with_writer, fake_id, amount=30)
        assert resp.status_code == 404
        assert resp.json()["error"] == "account_not_found"

    def test_idempotent_lock_returns_same_escrow_id(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """ELOCK-08: Idempotent lock returns same escrow_id."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        tid = f"t-{uuid4()}"
        eid1 = f"esc-{uuid4()}"
        resp1, _, _ = _lock_escrow(app_with_writer, aid, amount=30, task_id=tid, escrow_id=eid1)
        assert resp1.status_code == 201

        # Second lock with same payer+task+amount but different escrow_id
        eid2 = f"esc-{uuid4()}"
        resp2, _, _ = _lock_escrow(app_with_writer, aid, amount=30, task_id=tid, escrow_id=eid2)
        # Should return existing escrow
        data2 = resp2.json()
        assert data2["escrow_id"] == eid1

        # Verify balance not double-debited
        cursor = db_writer._db.execute(
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            (aid,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 70

    def test_duplicate_payer_task_different_amount_rejected(
        self, app_with_writer: TestClient
    ) -> None:
        """ELOCK-09: Duplicate (payer, task) with different amount is rejected."""
        aid = _setup_funded_account(app_with_writer, balance=200)
        tid = f"t-{uuid4()}"
        _lock_escrow(app_with_writer, aid, amount=30, task_id=tid)
        resp, _eid, _tid = _lock_escrow(app_with_writer, aid, amount=50, task_id=tid)
        assert resp.status_code == 409
        assert resp.json()["error"] == "escrow_already_locked"

    def test_zero_amount_rejected(self, app_with_writer: TestClient) -> None:
        """ELOCK-10: Zero amount is rejected."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = app_with_writer.post(
            "/bank/escrow/lock",
            json={
                "escrow_id": f"esc-{uuid4()}",
                "payer_account_id": aid,
                "amount": 0,
                "task_id": f"t-{uuid4()}",
                "created_at": "2026-02-28T10:10:00Z",
                "tx_id": f"tx-{uuid4()}",
                "event": make_event(source="bank", event_type="escrow.locked"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_amount"

    def test_negative_amount_rejected(self, app_with_writer: TestClient) -> None:
        """ELOCK-11: Negative amount is rejected."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = app_with_writer.post(
            "/bank/escrow/lock",
            json={
                "escrow_id": f"esc-{uuid4()}",
                "payer_account_id": aid,
                "amount": -10,
                "task_id": f"t-{uuid4()}",
                "created_at": "2026-02-28T10:10:00Z",
                "tx_id": f"tx-{uuid4()}",
                "event": make_event(source="bank", event_type="escrow.locked"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_amount"

    def test_missing_task_id(self, app_with_writer: TestClient) -> None:
        """ELOCK-12: Missing required field: task_id."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = app_with_writer.post(
            "/bank/escrow/lock",
            json={
                "escrow_id": f"esc-{uuid4()}",
                "payer_account_id": aid,
                "amount": 30,
                "created_at": "2026-02-28T10:10:00Z",
                "tx_id": f"tx-{uuid4()}",
                "event": make_event(source="bank", event_type="escrow.locked"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """ELOCK-13: Missing event object."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        resp = app_with_writer.post(
            "/bank/escrow/lock",
            json={
                "escrow_id": f"esc-{uuid4()}",
                "payer_account_id": aid,
                "amount": 30,
                "task_id": f"t-{uuid4()}",
                "created_at": "2026-02-28T10:10:00Z",
                "tx_id": f"tx-{uuid4()}",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_exact_balance_lock_succeeds(self, app_with_writer: TestClient) -> None:
        """ELOCK-14: Exact balance lock succeeds."""
        aid = _setup_funded_account(app_with_writer, balance=50)
        resp, _eid, _tid = _lock_escrow(app_with_writer, aid, amount=50)
        assert resp.status_code == 201
        assert resp.json()["balance_after"] == 0

    def test_multiple_escrow_locks_different_tasks(self, app_with_writer: TestClient) -> None:
        """ELOCK-15: Multiple escrow locks for different tasks."""
        aid = _setup_funded_account(app_with_writer, balance=100)
        tid1 = f"t-{uuid4()}"
        tid2 = f"t-{uuid4()}"
        resp1, _eid1, _tid1 = _lock_escrow(app_with_writer, aid, amount=30, task_id=tid1)
        resp2, _eid2, _tid2 = _lock_escrow(app_with_writer, aid, amount=20, task_id=tid2)
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        assert resp2.json()["balance_after"] == 50


# ===========================================================================
# Category 5: Escrow Release (EREL-01 through EREL-09)
# ===========================================================================


@pytest.mark.unit
class TestEscrowRelease:
    """Tests for POST /bank/escrow/release."""

    def _setup_escrow(
        self,
        client: TestClient,
        alice_balance: int = 100,
        bob_balance: int = 0,
        escrow_amount: int = 50,
    ) -> tuple[str, str, str]:
        """Register alice+bob, create accounts, lock escrow. Return (alice_id, bob_id, eid)."""
        alice = _setup_funded_account(client, balance=alice_balance)
        bob = _setup_funded_account(client, balance=bob_balance)
        _resp, eid, _tid = _lock_escrow(client, alice, amount=escrow_amount)
        return alice, bob, eid

    def test_valid_full_release(self, app_with_writer: TestClient) -> None:
        """EREL-01: Valid full release to recipient."""
        _alice, bob, eid = self._setup_escrow(app_with_writer)
        resp = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": eid,
                "recipient_account_id": bob,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["escrow_id"] == eid
        assert data["status"] == "released"
        assert data["amount"] == 50
        assert data["recipient_account_id"] == bob
        assert "event_id" in data

    def test_recipient_balance_increases(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """EREL-02: Recipient balance increases by escrow amount."""
        _alice, bob, eid = self._setup_escrow(app_with_writer, bob_balance=0, escrow_amount=50)
        app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": eid,
                "recipient_account_id": bob,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        cursor = db_writer._db.execute(
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            (bob,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 50

    def test_release_creates_transaction(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """EREL-03: Release creates escrow_release transaction on recipient."""
        _alice, bob, eid = self._setup_escrow(app_with_writer)
        app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": eid,
                "recipient_account_id": bob,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        cursor = db_writer._db.execute(
            "SELECT type, reference, amount "
            "FROM bank_transactions WHERE account_id = ? AND type = 'escrow_release'",
            (bob,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "escrow_release"
        assert row[1] == eid
        assert row[2] == 50

    def test_escrow_status_released(self, app_with_writer: TestClient, db_writer: DbWriter) -> None:
        """EREL-04: Escrow status changes to released."""
        _alice, bob, eid = self._setup_escrow(app_with_writer)
        app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": eid,
                "recipient_account_id": bob,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        cursor = db_writer._db.execute(
            "SELECT status, resolved_at FROM bank_escrow WHERE escrow_id = ?",
            (eid,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "released"
        assert row[1] is not None

    def test_escrow_not_found(self, app_with_writer: TestClient) -> None:
        """EREL-05: Escrow not found."""
        bob = _setup_funded_account(app_with_writer, balance=0)
        resp = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": f"esc-{uuid4()}",
                "recipient_account_id": bob,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "escrow_not_found"

    def test_already_resolved_escrow(self, app_with_writer: TestClient) -> None:
        """EREL-06: Already resolved escrow."""
        _alice, bob, eid = self._setup_escrow(app_with_writer)
        # First release
        app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": eid,
                "recipient_account_id": bob,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        # Second release attempt
        resp = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": eid,
                "recipient_account_id": bob,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:01:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "escrow_already_resolved"

    def test_recipient_account_not_found(self, app_with_writer: TestClient) -> None:
        """EREL-07: Recipient account not found."""
        alice = _setup_funded_account(app_with_writer, balance=100)
        _resp, eid, _tid = _lock_escrow(app_with_writer, alice, amount=50)
        fake_recipient = f"a-{uuid4()}"
        resp = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": eid,
                "recipient_account_id": fake_recipient,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "account_not_found"

    def test_missing_recipient_account_id(self, app_with_writer: TestClient) -> None:
        """EREL-08: Missing required field: recipient_account_id."""
        resp = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": f"esc-{uuid4()}",
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_escrow_id(self, app_with_writer: TestClient) -> None:
        """EREL-09: Missing required field: escrow_id."""
        bob = _setup_funded_account(app_with_writer, balance=0)
        resp = app_with_writer.post(
            "/bank/escrow/release",
            json={
                "recipient_account_id": bob,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"


# ===========================================================================
# Category 6: Escrow Split (ESPL-01 through ESPL-16)
# ===========================================================================


@pytest.mark.unit
class TestEscrowSplit:
    """Tests for POST /bank/escrow/split."""

    def _setup_split(
        self,
        client: TestClient,
        alice_balance: int = 1000,
        bob_balance: int = 0,
        escrow_amount: int = 500,
    ) -> tuple[str, str, str]:
        """Register alice+bob, create accounts, lock escrow. Return (alice_id, bob_id, eid)."""
        alice = _setup_funded_account(client, balance=alice_balance)
        bob = _setup_funded_account(client, balance=bob_balance)
        _resp, eid, _tid = _lock_escrow(client, alice, amount=escrow_amount)
        return alice, bob, eid

    def _do_split(
        self,
        client: TestClient,
        escrow_id: str,
        worker_id: str,
        poster_id: str,
        worker_amount: int,
        poster_amount: int,
    ) -> object:
        """Execute escrow split."""
        return client.post(
            "/bank/escrow/split",
            json={
                "escrow_id": escrow_id,
                "worker_account_id": worker_id,
                "worker_amount": worker_amount,
                "poster_account_id": poster_id,
                "poster_amount": poster_amount,
                "worker_tx_id": f"tx-{uuid4()}",
                "poster_tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.split"),
            },
        )

    def test_even_50_50_split(self, app_with_writer: TestClient) -> None:
        """ESPL-01: Even 50/50 split."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=500
        )
        resp = self._do_split(app_with_writer, eid, bob, alice, 250, 250)
        assert resp.status_code == 200
        data = resp.json()
        assert data["escrow_id"] == eid
        assert data["status"] == "split"
        assert data["worker_amount"] == 250
        assert data["poster_amount"] == 250
        assert "event_id" in data

    def test_uneven_split(self, app_with_writer: TestClient) -> None:
        """ESPL-02: Uneven split."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=100
        )
        resp = self._do_split(app_with_writer, eid, bob, alice, 70, 30)
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_amount"] == 70
        assert data["poster_amount"] == 30

    def test_worker_gets_all(self, app_with_writer: TestClient) -> None:
        """ESPL-03: Worker gets all (100/0 split)."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=100
        )
        resp = self._do_split(app_with_writer, eid, bob, alice, 100, 0)
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_amount"] == 100
        assert data["poster_amount"] == 0

    def test_poster_gets_all(self, app_with_writer: TestClient) -> None:
        """ESPL-04: Poster gets all (0/100 split)."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=100
        )
        resp = self._do_split(app_with_writer, eid, bob, alice, 0, 100)
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_amount"] == 0
        assert data["poster_amount"] == 100

    def test_both_balances_updated(self, app_with_writer: TestClient, db_writer: DbWriter) -> None:
        """ESPL-05: Both account balances updated correctly after split."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=200, bob_balance=10, escrow_amount=100
        )
        self._do_split(app_with_writer, eid, bob, alice, 60, 40)

        # alice: 200 - 100 (escrow) + 40 (poster share) = 140
        alice_cursor = db_writer._db.execute(
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            (alice,),
        )
        alice_row = alice_cursor.fetchone()
        assert alice_row is not None
        assert alice_row[0] == 140

        # bob: 10 + 60 (worker share) = 70
        bob_cursor = db_writer._db.execute(
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            (bob,),
        )
        bob_row = bob_cursor.fetchone()
        assert bob_row is not None
        assert bob_row[0] == 70

    def test_split_creates_transactions_on_both(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """ESPL-06: Split creates escrow_release transactions on both accounts."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=100
        )
        self._do_split(app_with_writer, eid, bob, alice, 60, 40)

        # bob (worker) should have escrow_release
        bob_cursor = db_writer._db.execute(
            "SELECT type, amount, reference "
            "FROM bank_transactions "
            "WHERE account_id = ? AND type = 'escrow_release'",
            (bob,),
        )
        bob_row = bob_cursor.fetchone()
        assert bob_row is not None
        assert bob_row[1] == 60
        assert bob_row[2] == eid

        # alice (poster) should have escrow_release
        alice_cursor = db_writer._db.execute(
            "SELECT type, amount, reference "
            "FROM bank_transactions "
            "WHERE account_id = ? AND type = 'escrow_release'",
            (alice,),
        )
        alice_row = alice_cursor.fetchone()
        assert alice_row is not None
        assert alice_row[1] == 40
        assert alice_row[2] == eid

    def test_zero_amount_no_transaction(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """ESPL-07: Zero-amount share creates no transaction."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=100
        )
        self._do_split(app_with_writer, eid, bob, alice, 100, 0)

        # alice (poster) should have NO escrow_release with this escrow_id
        cursor = db_writer._db.execute(
            "SELECT type FROM bank_transactions "
            "WHERE account_id = ? AND type = 'escrow_release' AND reference = ?",
            (alice, eid),
        )
        assert cursor.fetchone() is None

    def test_escrow_status_split(self, app_with_writer: TestClient, db_writer: DbWriter) -> None:
        """ESPL-08: Escrow status changes to split."""
        alice, bob, eid = self._setup_split(app_with_writer, escrow_amount=100)
        self._do_split(app_with_writer, eid, bob, alice, 50, 50)

        cursor = db_writer._db.execute(
            "SELECT status, resolved_at FROM bank_escrow WHERE escrow_id = ?",
            (eid,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "split"
        assert row[1] is not None

    def test_amount_mismatch(self, app_with_writer: TestClient) -> None:
        """ESPL-09: Amounts do not sum to escrow amount."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=100
        )
        resp = self._do_split(app_with_writer, eid, bob, alice, 60, 60)
        assert resp.status_code == 400
        assert resp.json()["error"] == "amount_mismatch"

    def test_escrow_not_found(self, app_with_writer: TestClient) -> None:
        """ESPL-10: Escrow not found."""
        alice = _setup_funded_account(app_with_writer, balance=100)
        bob = _setup_funded_account(app_with_writer, balance=0)
        resp = self._do_split(app_with_writer, f"esc-{uuid4()}", bob, alice, 50, 50)
        assert resp.status_code == 404
        assert resp.json()["error"] == "escrow_not_found"

    def test_already_resolved_escrow(self, app_with_writer: TestClient) -> None:
        """ESPL-11: Already resolved escrow."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=100
        )
        # Release first
        app_with_writer.post(
            "/bank/escrow/release",
            json={
                "escrow_id": eid,
                "recipient_account_id": bob,
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.released"),
            },
        )
        # Try to split
        resp = self._do_split(app_with_writer, eid, bob, alice, 50, 50)
        assert resp.status_code == 409
        assert resp.json()["error"] == "escrow_already_resolved"

    def test_worker_account_not_found(self, app_with_writer: TestClient) -> None:
        """ESPL-12: Worker account not found."""
        alice = _setup_funded_account(app_with_writer, balance=1000)
        _resp, eid, _tid = _lock_escrow(app_with_writer, alice, amount=100)
        fake_worker = f"a-{uuid4()}"
        resp = self._do_split(app_with_writer, eid, fake_worker, alice, 60, 40)
        assert resp.status_code == 404
        assert resp.json()["error"] == "account_not_found"

    def test_poster_account_not_found(self, app_with_writer: TestClient) -> None:
        """ESPL-13: Poster account not found."""
        alice = _setup_funded_account(app_with_writer, balance=1000)
        bob = _setup_funded_account(app_with_writer, balance=0)
        _resp, eid, _tid = _lock_escrow(app_with_writer, alice, amount=100)
        fake_poster = f"a-{uuid4()}"
        resp = self._do_split(app_with_writer, eid, bob, fake_poster, 60, 40)
        assert resp.status_code == 404
        assert resp.json()["error"] == "account_not_found"

    def test_negative_worker_amount_rejected(self, app_with_writer: TestClient) -> None:
        """ESPL-14: Negative worker_amount is rejected."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=100
        )
        resp = self._do_split(app_with_writer, eid, bob, alice, -10, 110)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_amount"

    def test_negative_poster_amount_rejected(self, app_with_writer: TestClient) -> None:
        """ESPL-15: Negative poster_amount is rejected."""
        alice, bob, eid = self._setup_split(
            app_with_writer, alice_balance=1000, bob_balance=0, escrow_amount=100
        )
        resp = self._do_split(app_with_writer, eid, bob, alice, 110, -10)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_amount"

    def test_missing_worker_account_id(self, app_with_writer: TestClient) -> None:
        """ESPL-16: Missing required field: worker_account_id."""
        resp = app_with_writer.post(
            "/bank/escrow/split",
            json={
                "escrow_id": f"esc-{uuid4()}",
                "worker_amount": 50,
                "poster_account_id": f"a-{uuid4()}",
                "poster_amount": 50,
                "worker_tx_id": f"tx-{uuid4()}",
                "poster_tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="escrow.split"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"
