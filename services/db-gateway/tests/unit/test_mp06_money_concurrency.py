"""MP-06 concurrent monetary boundary proofs across independent writers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.money_audit import audit_money
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.services.db_writer import DbWriter
from tests.conftest import make_event

pytestmark = pytest.mark.unit


def _register(writer: DbWriter, agent_id: str) -> None:
    writer.register_agent(
        {
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{agent_id}",
            "registered_at": "2026-10-16T00:00:00Z",
            "event": make_event(agent_id=agent_id),
        }
    )


def _account(writer: DbWriter, account_id: str, balance: int) -> None:
    _register(writer, account_id)
    data: dict[str, Any] = {
        "account_id": account_id,
        "balance": balance,
        "created_at": "2026-10-16T00:00:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        data["initial_credit"] = {
            "tx_id": f"tx-initial-{account_id}",
            "amount": balance,
            "reference": "initial",
            "timestamp": "2026-10-16T00:00:00Z",
        }
    writer.create_account(data)


def _lock_data(contender: str) -> dict[str, Any]:
    return {
        "escrow_id": f"esc-{contender}",
        "payer_account_id": "a-payer",
        "amount": SQLITE_MAX_INTEGER,
        "task_id": f"t-{contender}",
        "created_at": "2026-10-16T00:05:00Z",
        "tx_id": f"tx-lock-{contender}",
        "event": make_event(
            source="bank",
            event_type="escrow.locked",
            task_id=f"t-{contender}",
        ),
    }


def _run_competing(
    barrier: Barrier,
    contenders: tuple[tuple[DbWriter, str], tuple[DbWriter, str]],
    operation: str,
) -> list[dict[str, Any] | str]:
    def attempt(writer: DbWriter, contender: str) -> dict[str, Any] | str:
        barrier.wait(timeout=10)
        try:
            if operation == "lock":
                return writer.escrow_lock(_lock_data(contender))
            if operation == "release":
                return writer.escrow_release(
                    {
                        "escrow_id": "esc-settle",
                        "recipient_account_id": f"a-worker-{contender}",
                        "tx_id": f"tx-release-{contender}",
                        "resolved_at": "2026-10-16T00:10:00Z",
                        "event": make_event(source="bank", event_type="escrow.released"),
                    },
                    None,
                )
            return writer.escrow_split(
                {
                    "escrow_id": "esc-settle",
                    "worker_account_id": f"a-worker-{contender}",
                    "worker_amount": SQLITE_MAX_INTEGER - 1,
                    "poster_account_id": "a-payer",
                    "poster_amount": 1,
                    "worker_tx_id": f"tx-worker-{contender}",
                    "poster_tx_id": f"tx-poster-{contender}",
                    "resolved_at": "2026-10-16T00:10:00Z",
                    "event": make_event(source="bank", event_type="escrow.split"),
                },
                None,
            )
        except ServiceError as error:
            return error.error

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(attempt, writer, contender) for writer, contender in contenders]
        return [future.result(timeout=15) for future in futures]


def test_competing_maximum_locks_have_one_atomic_winner(
    db_writer: DbWriter,
    initialized_db: str,
) -> None:
    _account(db_writer, "a-payer", SQLITE_MAX_INTEGER)
    before_events = db_writer.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    before_transactions = db_writer.connection.execute(
        "SELECT COUNT(*) FROM bank_transactions"
    ).fetchone()[0]
    other = DbWriter(initialized_db, 5000, "wal", None)
    try:
        outcomes = _run_competing(
            Barrier(2),
            ((db_writer, "left"), (other, "right")),
            "lock",
        )

        assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
        assert outcomes.count("insufficient_funds") == 1
        account = db_writer.connection.execute(
            "SELECT balance, typeof(balance) FROM bank_accounts WHERE account_id = 'a-payer'"
        ).fetchone()
        assert tuple(account) == (0, "integer")
        escrows = db_writer.connection.execute(
            "SELECT escrow_id, amount, typeof(amount), status FROM bank_escrow"
        ).fetchall()
        assert len(escrows) == 1
        assert tuple(escrows[0])[1:] == (SQLITE_MAX_INTEGER, "integer", "locked")
        lock_transactions = db_writer.connection.execute(
            "SELECT tx_id, amount, typeof(amount), balance_after, typeof(balance_after) "
            "FROM bank_transactions WHERE type = 'escrow_lock'"
        ).fetchall()
        assert len(lock_transactions) == 1
        assert tuple(lock_transactions[0])[1:] == (
            SQLITE_MAX_INTEGER,
            "integer",
            0,
            "integer",
        )
        assert (
            db_writer.connection.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0]
            == before_transactions + 1
        )
        assert (
            db_writer.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            == before_events + 1
        )
        audit = audit_money(db_writer.connection)
        assert audit.financially_clean
        assert audit.total_account_balances == 0
        assert audit.total_locked_escrow == SQLITE_MAX_INTEGER
        assert not db_writer.connection.in_transaction
        assert not other.connection.in_transaction
    finally:
        other.close()


@pytest.mark.parametrize("settlement", ["release", "split"])
def test_competing_maximum_settlements_have_one_atomic_winner(
    db_writer: DbWriter,
    initialized_db: str,
    settlement: str,
) -> None:
    _account(db_writer, "a-payer", SQLITE_MAX_INTEGER)
    _account(db_writer, "a-worker-left", 0)
    _account(db_writer, "a-worker-right", 0)
    db_writer.escrow_lock(
        {
            **_lock_data("settle"),
            "escrow_id": "esc-settle",
            "task_id": "t-settle",
            "tx_id": "tx-lock-settle",
        }
    )
    before_events = db_writer.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    before_transactions = db_writer.connection.execute(
        "SELECT COUNT(*) FROM bank_transactions"
    ).fetchone()[0]
    other = DbWriter(initialized_db, 5000, "wal", None)
    try:
        outcomes = _run_competing(
            Barrier(2),
            ((db_writer, "left"), (other, "right")),
            settlement,
        )

        assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
        assert outcomes.count("escrow_already_resolved") == 1
        escrow = db_writer.connection.execute(
            "SELECT amount, typeof(amount), status, resolved_at FROM bank_escrow "
            "WHERE escrow_id = 'esc-settle'"
        ).fetchone()
        assert tuple(escrow) == (
            SQLITE_MAX_INTEGER,
            "integer",
            "released" if settlement == "release" else "split",
            "2026-10-16T00:10:00Z",
        )
        release_transactions = [
            tuple(row)
            for row in db_writer.connection.execute(
                "SELECT tx_id, account_id, amount, typeof(amount), balance_after, "
                "typeof(balance_after), reference FROM bank_transactions "
                "WHERE type = 'escrow_release' ORDER BY tx_id"
            ).fetchall()
        ]
        if settlement == "release":
            assert len(release_transactions) == 1
            assert release_transactions[0][0] in {"tx-release-left", "tx-release-right"}
            assert release_transactions[0][2:] == (
                SQLITE_MAX_INTEGER,
                "integer",
                SQLITE_MAX_INTEGER,
                "integer",
                "esc-settle",
            )
        else:
            tx_ids = {row[0] for row in release_transactions}
            assert tx_ids in (
                {"tx-worker-left", "tx-poster-left"},
                {"tx-worker-right", "tx-poster-right"},
            )
            assert sorted(row[2] for row in release_transactions) == [
                1,
                SQLITE_MAX_INTEGER - 1,
            ]
            assert all(row[3] == "integer" and row[5] == "integer" for row in release_transactions)
            assert all(row[6] == "esc-settle" for row in release_transactions)
        expected_legs = 1 if settlement == "release" else 2
        assert (
            db_writer.connection.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0]
            == before_transactions + expected_legs
        )
        assert (
            db_writer.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            == before_events + 1
        )
        account_rows = db_writer.connection.execute(
            "SELECT balance, typeof(balance) FROM bank_accounts ORDER BY account_id"
        ).fetchall()
        assert all(row[1] == "integer" for row in account_rows)
        assert sum(row[0] for row in account_rows) == SQLITE_MAX_INTEGER
        audit = audit_money(db_writer.connection)
        assert audit.financially_clean
        assert audit.total_account_balances == SQLITE_MAX_INTEGER
        assert audit.total_locked_escrow == 0
        assert not db_writer.connection.in_transaction
        assert not other.connection.in_transaction
    finally:
        other.close()
