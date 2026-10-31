"""Temporary MP-06/T-120 red proofs against the real DB Gateway writer and schema.

This file intentionally lives outside the repository.  Each red test asserts both
the required rejection and transaction-wide absence of unwanted mutations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.routers.helpers import (
    validate_non_negative_integer,
    validate_positive_integer,
)
from db_gateway_service.services.db_writer import DbWriter

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

REPO_ROOT = Path("/Users/flo/Developer/github/agent-economy")
SCHEMA_SQL = (REPO_ROOT / "docs/specifications/schema.sql").read_text()
SQLITE_MAX_INTEGER = 2**63 - 1


@pytest.fixture
def writer(tmp_path: Path) -> Iterator[DbWriter]:
    db_writer = DbWriter(
        db_path=str(tmp_path / "economy.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=SCHEMA_SQL,
    )
    yield db_writer
    db_writer.close()


def _event(event_type: str, *, task_id: str | None = None) -> dict[str, object]:
    return {
        "event_source": "bank",
        "event_type": event_type,
        "timestamp": "2026-10-15T10:00:00Z",
        "task_id": task_id,
        "agent_id": None,
        "summary": f"MP-06 {event_type}",
        "payload": "{}",
    }


def _register_agent(writer: DbWriter, agent_id: str) -> None:
    writer.register_agent(
        {
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-10-15T10:00:00Z",
            "event": {
                **_event("agent.registered"),
                "event_source": "identity",
                "agent_id": agent_id,
            },
        }
    )


def _create_account(
    writer: DbWriter,
    agent_id: str,
    balance: object,
    *,
    issuance_amount: object | None = None,
) -> None:
    _register_agent(writer, agent_id)
    data: dict[str, object] = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2026-10-15T10:00:00Z",
        "event": _event("account.created"),
    }
    if issuance_amount is not None:
        data["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": issuance_amount,
            "reference": "initial_balance",
            "timestamp": "2026-10-15T10:00:00Z",
        }
    writer.create_account(data)


def _lock(
    writer: DbWriter,
    payer: str,
    *,
    task_id: str,
    escrow_id: str,
    amount: int,
) -> dict[str, Any]:
    return writer.escrow_lock(
        {
            "escrow_id": escrow_id,
            "payer_account_id": payer,
            "amount": amount,
            "task_id": task_id,
            "created_at": "2026-10-15T10:05:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": _event("escrow.locked", task_id=task_id),
        }
    )


def _capture_error(operation: Callable[[], object]) -> BaseException | None:
    try:
        operation()
    except BaseException as exc:  # the assertion reports unexpected exception classes too
        return exc
    return None


def _money_state(writer: DbWriter) -> dict[str, object]:
    db = writer.connection
    return {
        "accounts": [
            tuple(row)
            for row in db.execute(
                "SELECT account_id, balance, typeof(balance) FROM bank_accounts "
                "ORDER BY account_id"
            ).fetchall()
        ],
        "escrows": [
            tuple(row)
            for row in db.execute(
                "SELECT escrow_id, payer_account_id, amount, typeof(amount), task_id, status, "
                "resolved_at FROM bank_escrow ORDER BY escrow_id"
            ).fetchall()
        ],
        "transactions": [
            tuple(row)
            for row in db.execute(
                "SELECT tx_id, account_id, type, amount, typeof(amount), balance_after, "
                "typeof(balance_after), reference FROM bank_transactions ORDER BY tx_id"
            ).fetchall()
        ],
        "event_count": db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
    }


def _assert_rejected_without_writes(
    *,
    error: BaseException | None,
    before: dict[str, object],
    after: dict[str, object],
    expected_error: str,
) -> None:
    violations: list[str] = []
    if not isinstance(error, ServiceError):
        violations.append(f"expected ServiceError({expected_error!r}), got {error!r}")
    elif error.error != expected_error:
        violations.append(f"expected error={expected_error!r}, got {error.error!r}")
    if after != before:
        violations.append(
            f"invalid operation mutated money state\nbefore={before!r}\nafter={after!r}"
        )
    assert not violations, "\n".join(violations)


@pytest.mark.unit
@pytest.mark.parametrize("poster_amount", [0, 40])
def test_split_rejects_unrelated_poster_atomically(
    writer: DbWriter,
    poster_amount: int,
) -> None:
    payer = "a-payer"
    worker = "a-worker"
    impostor = "a-impostor"
    _create_account(writer, payer, 1000, issuance_amount=1000)
    _create_account(writer, worker, 0)
    _create_account(writer, impostor, 0)
    escrow_id = "esc-wrong-payer"
    _lock(writer, payer, task_id="t-wrong-payer", escrow_id=escrow_id, amount=100)
    before = _money_state(writer)

    error = _capture_error(
        lambda: writer.escrow_split(
            {
                "escrow_id": escrow_id,
                "worker_account_id": worker,
                "worker_amount": 100 - poster_amount,
                "poster_account_id": impostor,
                "poster_amount": poster_amount,
                "worker_tx_id": f"tx-worker-{poster_amount}",
                "poster_tx_id": f"tx-poster-{poster_amount}",
                "resolved_at": "2026-10-15T10:10:00Z",
                "event": _event("escrow.split"),
            },
            None,
        )
    )

    _assert_rejected_without_writes(
        error=error,
        before=before,
        after=_money_state(writer),
        expected_error="payload_mismatch",
    )


@pytest.mark.unit
def test_account_rejects_mismatched_initial_issuance_atomically(writer: DbWriter) -> None:
    agent_id = "a-mismatched-issuance"
    _register_agent(writer, agent_id)
    before = _money_state(writer)
    error = _capture_error(
        lambda: writer.create_account(
            {
                "account_id": agent_id,
                "balance": 100,
                "created_at": "2026-10-15T10:00:00Z",
                "initial_credit": {
                    "tx_id": "tx-mismatched-issuance",
                    "amount": 1,
                    "reference": "initial_balance",
                    "timestamp": "2026-10-15T10:00:00Z",
                },
                "event": _event("account.created"),
            }
        )
    )
    _assert_rejected_without_writes(
        error=error,
        before=before,
        after=_money_state(writer),
        expected_error="payload_mismatch",
    )


@pytest.mark.unit
@pytest.mark.parametrize("amount", [True, 1.5], ids=["boolean", "noninteger-real"])
def test_writer_rejects_noninteger_money_atomically(writer: DbWriter, amount: object) -> None:
    agent_id = f"a-invalid-money-{type(amount).__name__}"
    _register_agent(writer, agent_id)
    before = _money_state(writer)
    error = _capture_error(
        lambda: writer.create_account(
            {
                "account_id": agent_id,
                "balance": amount,
                "created_at": "2026-10-15T10:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-invalid-{type(amount).__name__}",
                    "amount": amount,
                    "reference": "initial_balance",
                    "timestamp": "2026-10-15T10:00:00Z",
                },
                "event": _event("account.created"),
            }
        )
    )
    _assert_rejected_without_writes(
        error=error,
        before=before,
        after=_money_state(writer),
        expected_error="invalid_amount",
    )


@pytest.mark.unit
def test_credit_cannot_overflow_sqlite_integer_or_mutate_ledger(writer: DbWriter) -> None:
    account_id = "a-overflow"
    _create_account(
        writer,
        account_id,
        SQLITE_MAX_INTEGER,
        issuance_amount=SQLITE_MAX_INTEGER,
    )
    before = _money_state(writer)
    error = _capture_error(
        lambda: writer.credit_account(
            {
                "tx_id": "tx-overflow",
                "account_id": account_id,
                "amount": 1,
                "reference": "overflow-attempt",
                "timestamp": "2026-10-15T10:05:00Z",
                "event": _event("account.credited"),
            }
        )
    )
    _assert_rejected_without_writes(
        error=error,
        before=before,
        after=_money_state(writer),
        expected_error="amount_out_of_range",
    )
    stored = writer.connection.execute(
        "SELECT balance, typeof(balance) FROM bank_accounts WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    assert tuple(stored) == (SQLITE_MAX_INTEGER, "integer")


@pytest.mark.unit
@pytest.mark.parametrize(
    "preexisting_sql",
    [
        "CREATE TABLE unrelated_table (id INTEGER PRIMARY KEY);",
        "CREATE TABLE bank_accounts (account_id TEXT PRIMARY KEY, balance INTEGER NOT NULL, "
        "created_at TEXT NOT NULL);",
    ],
    ids=["unknown-layout", "partial-money-layout"],
)
def test_startup_rejects_partial_or_unknown_schema_before_readiness(
    tmp_path: Path,
    preexisting_sql: str,
) -> None:
    db_path = tmp_path / "partial.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(preexisting_sql)
    before = [
        tuple(row)
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'index') ORDER BY name"
        ).fetchall()
    ]
    connection.close()

    opened: DbWriter | None = None
    error: BaseException | None = None
    try:
        opened = DbWriter(
            db_path=str(db_path),
            busy_timeout_ms=5000,
            journal_mode="wal",
            schema_sql=SCHEMA_SQL,
        )
    except BaseException as exc:
        error = exc
    finally:
        if opened is not None:
            opened.close()

    check = sqlite3.connect(db_path)
    after = [
        tuple(row)
        for row in check.execute(
            "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'index') ORDER BY name"
        ).fetchall()
    ]
    check.close()
    violations = []
    if error is None:
        violations.append("partial/unknown schema was accepted as ready")
    if after != before:
        violations.append(
            f"failed readiness check mutated schema\nbefore={before!r}\nafter={after!r}"
        )
    assert not violations, "\n".join(violations)


@pytest.mark.unit
@pytest.mark.parametrize("settlement", ["release", "split"])
@pytest.mark.parametrize("replay_amount", [100, 50], ids=["exact", "changed-amount"])
def test_escrow_identity_is_unique_for_its_lifetime(
    writer: DbWriter,
    settlement: str,
    replay_amount: int,
) -> None:
    payer = f"a-lifetime-payer-{settlement}-{replay_amount}"
    recipient = f"a-lifetime-recipient-{settlement}-{replay_amount}"
    _create_account(writer, payer, 1000, issuance_amount=1000)
    _create_account(writer, recipient, 0)
    task_id = f"t-lifetime-{settlement}-{replay_amount}"
    original_escrow = f"esc-original-{settlement}-{replay_amount}"
    _lock(writer, payer, task_id=task_id, escrow_id=original_escrow, amount=100)
    if settlement == "release":
        writer.escrow_release(
            {
                "escrow_id": original_escrow,
                "recipient_account_id": recipient,
                "tx_id": f"tx-release-{replay_amount}",
                "resolved_at": "2026-10-15T10:10:00Z",
                "event": _event("escrow.released"),
            },
            None,
        )
    else:
        writer.escrow_split(
            {
                "escrow_id": original_escrow,
                "worker_account_id": recipient,
                "worker_amount": 60,
                "poster_account_id": payer,
                "poster_amount": 40,
                "worker_tx_id": f"tx-split-worker-{replay_amount}",
                "poster_tx_id": f"tx-split-poster-{replay_amount}",
                "resolved_at": "2026-10-15T10:10:00Z",
                "event": _event("escrow.split"),
            },
            None,
        )
    before = _money_state(writer)

    _capture_error(
        lambda: _lock(
            writer,
            payer,
            task_id=task_id,
            escrow_id=f"esc-replay-{settlement}-{replay_amount}",
            amount=replay_amount,
        )
    )
    after = _money_state(writer)

    assert after == before, (
        "a settled (payer, task) identity was debited and recorded again\n"
        f"before={before!r}\nafter={after!r}"
    )


@pytest.mark.unit
def test_control_matching_issuance_is_auditable(writer: DbWriter) -> None:
    _create_account(writer, "a-control-match", 25, issuance_amount=25)
    row = writer.connection.execute(
        "SELECT a.balance, typeof(a.balance), t.amount, typeof(t.amount), t.balance_after "
        "FROM bank_accounts AS a JOIN bank_transactions AS t ON t.account_id = a.account_id "
        "WHERE a.account_id = 'a-control-match'"
    ).fetchone()
    assert tuple(row) == (25, "integer", 25, "integer", 25)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("validator", "value"),
    [
        (validate_positive_integer, True),
        (validate_positive_integer, 1.5),
        (validate_non_negative_integer, False),
        (validate_non_negative_integer, 0.5),
    ],
)
def test_control_http_amount_validators_reject_boolean_and_real(
    validator: Callable[[dict[str, object], str], None],
    value: object,
) -> None:
    with pytest.raises(ServiceError, match="must be") as exc_info:
        validator({"amount": value}, "amount")
    assert exc_info.value.error == "invalid_amount"


@pytest.mark.unit
def test_control_fresh_database_initializes_complete_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    db_writer = DbWriter(
        db_path=str(db_path),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=SCHEMA_SQL,
    )
    tables = {
        row[0]
        for row in db_writer.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    db_writer.close()
    assert {"identity_agents", "bank_accounts", "bank_transactions", "bank_escrow"} <= tables
