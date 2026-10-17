"""Native MP-04 receipt atomicity and concurrency component proofs.

These tests install the receipt-v2 sidecar after opening the v1 writers. They
exercise the Bank writer component directly; startup admission and migration
coverage belongs to the separate schema-v2 integration slice.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.services.db_writer import DbWriter
from tests.conftest import make_event

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_EMPTY_SET_HASH = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
_EMPTY_MARKER_HASH = "a81b1a47e8cd319a73362c83a992f181986e799b1e82753bf139e60b35d0a4f6"


def _receipt_schema() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "db_gateway_service"
        / "schema_resources"
        / "receipt_v2.sql"
    )
    return path.read_text()


def _install_fresh_receipt_sidecar(writer: DbWriter) -> None:
    writer.connection.executescript(_receipt_schema())
    writer.connection.execute(
        "INSERT INTO bank_receipt_cutover "
        "(singleton, attestation_version, cutover_kind, legacy_transaction_count, "
        "legacy_escrow_count, legacy_transaction_set_sha256, "
        "legacy_escrow_set_sha256, marker_set_sha256) "
        "VALUES (1, 1, 'fresh_v2', 0, 0, ?, ?, ?)",
        (_EMPTY_SET_HASH, _EMPTY_SET_HASH, _EMPTY_MARKER_HASH),
    )
    writer.connection.commit()


@pytest.fixture
def receipt_writer(initialized_db: str) -> Iterator[DbWriter]:
    writer = DbWriter(initialized_db, 5000, "wal", None)
    yield writer
    writer.close()


def _register_zero_account(writer: DbWriter, account_id: str) -> None:
    writer.register_agent(
        {
            "agent_id": account_id,
            "name": account_id,
            "public_key": f"ed25519:{account_id}",
            "registered_at": "2026-10-16T09:00:00Z",
            "event": make_event(agent_id=account_id),
        }
    )
    result = writer.create_account(
        {
            "account_id": account_id,
            "balance": 0,
            "created_at": "2026-10-16T09:01:00Z",
            "event": make_event(source="bank", event_type="account.created"),
        }
    )
    assert result["account_id"] == account_id


def _register_funded_account(
    writer: DbWriter,
    account_id: str,
    balance: int,
    operation_id: str,
) -> dict[str, Any]:
    writer.register_agent(
        {
            "agent_id": account_id,
            "name": account_id,
            "public_key": f"ed25519:{account_id}",
            "registered_at": "2026-10-16T09:00:00Z",
            "event": make_event(agent_id=account_id),
        }
    )
    return writer.create_account(
        {
            "operation_id": operation_id,
            "account_id": account_id,
            "balance": balance,
            "created_at": "2026-10-16T09:01:00Z",
            "initial_credit": {
                "tx_id": f"tx-initial-{account_id}",
                "amount": balance,
                "reference": "initial_balance",
                "timestamp": "2026-10-16T09:01:00Z",
            },
            "event": make_event(source="bank", event_type="account.created"),
        }
    )


def _credit_data(account_id: str, operation_id: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "tx_id": f"tx-{uuid4()}",
        "account_id": account_id,
        "amount": 25,
        "reference": "atomic-credit",
        "timestamp": "2026-10-16T09:02:00Z",
        "event": make_event(source="bank", event_type="account.credited"),
    }


def _financial_snapshot(writer: DbWriter) -> dict[str, tuple[tuple[object, ...], ...]]:
    tables = [
        str(row[0])
        for row in writer.connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND (name = 'events' OR name LIKE 'bank_%') "
            "ORDER BY name"
        ).fetchall()
    ]
    snapshot: dict[str, tuple[tuple[object, ...], ...]] = {}
    for table in tables:
        assert table == "events" or table.startswith("bank_")
        rows = writer.connection.execute(f'SELECT * FROM "{table}"').fetchall()
        snapshot[table] = tuple(sorted((tuple(row) for row in rows), key=repr))
    return snapshot


@pytest.mark.parametrize("failure_table", ["bank_operation_receipts", "bank_operation_legs"])
def test_receipt_or_leg_failure_rolls_back_entire_credit(
    receipt_writer: DbWriter,
    failure_table: str,
) -> None:
    """The monetary effect, event, receipt, and every leg form one transaction."""
    account_id = f"a-atomic-{uuid4()}"
    _register_zero_account(receipt_writer, account_id)
    assert failure_table in {"bank_operation_receipts", "bank_operation_legs"}
    receipt_writer.connection.execute(
        f"CREATE TRIGGER force_receipt_failure BEFORE INSERT ON {failure_table} "
        "BEGIN SELECT RAISE(ABORT, 'forced receipt persistence failure'); END"
    )
    before = _financial_snapshot(receipt_writer)

    with pytest.raises(sqlite3.IntegrityError, match="forced receipt persistence failure"):
        receipt_writer.credit_account(_credit_data(account_id, f"op-{uuid4()}"))

    assert _financial_snapshot(receipt_writer) == before
    assert not receipt_writer.connection.in_transaction


def test_credit_receipt_is_immutable_across_exact_replay(
    receipt_writer: DbWriter,
) -> None:
    account_id = f"a-credit-{uuid4()}"
    _register_zero_account(receipt_writer, account_id)
    operation_id = f"op-{uuid4()}"
    original_data = _credit_data(account_id, operation_id)
    original = receipt_writer.credit_account(original_data)
    intervening = receipt_writer.credit_account(
        {
            **_credit_data(account_id, f"op-{uuid4()}"),
            "amount": 5,
            "reference": "intervening-credit",
        }
    )
    assert intervening["outcome"] == "native_receipt"
    before_replay = _financial_snapshot(receipt_writer)

    replay = receipt_writer.credit_account(original_data)

    assert replay["outcome"] == "native_receipt"
    assert replay["receipt"] == original["receipt"]
    assert (
        original["receipt"]["operation_id"],
        original["receipt"]["operation_kind"],
        original["receipt"]["source_reference"],
        original["receipt"]["result"]["balance_after"],
        original["receipt"]["legs"][0]["role"],
    ) == (operation_id, "credit", "atomic-credit", 25, "account")
    assert replay["current_escrow"] is None
    assert _financial_snapshot(receipt_writer) == before_replay
    balance = receipt_writer.connection.execute(
        "SELECT balance FROM bank_accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    assert balance is not None
    assert int(balance[0]) == 30


def test_exact_replay_ignores_only_allowed_candidate_metadata(
    receipt_writer: DbWriter,
) -> None:
    account_id = f"a-metadata-{uuid4()}"
    _register_zero_account(receipt_writer, account_id)
    operation_id = f"op-{uuid4()}"
    original_data = _credit_data(account_id, operation_id)
    original = receipt_writer.credit_account(original_data)
    before = _financial_snapshot(receipt_writer)
    changed_event = {
        **make_event(source="bank", event_type="candidate.changed"),
        "timestamp": "2026-10-16T19:59:00Z",
        "summary": "Changed retry presentation",
        "payload": '{"candidate":true}',
    }

    replay = receipt_writer.credit_account(
        {
            **original_data,
            "tx_id": f"tx-candidate-{uuid4()}",
            "timestamp": "2026-10-16T19:59:00Z",
            "event": changed_event,
        }
    )

    assert replay["receipt"] == original["receipt"]
    assert replay["receipt"]["legs"][0]["transaction_id"] == original_data["tx_id"]
    assert replay["receipt"]["committed_at"] == original_data["timestamp"]
    assert _financial_snapshot(receipt_writer) == before


def test_credit_identity_conflicts_preserve_the_true_winner_without_aliasing(
    receipt_writer: DbWriter,
) -> None:
    account_id = f"a-conflict-{uuid4()}"
    _register_zero_account(receipt_writer, account_id)
    winning_operation_id = f"op-{uuid4()}"
    winning_data = _credit_data(account_id, winning_operation_id)
    receipt_writer.credit_account(winning_data)
    before = _financial_snapshot(receipt_writer)

    with pytest.raises(ServiceError) as changed_identity:
        receipt_writer.credit_account(
            {
                **winning_data,
                "reference": "changed-command",
                "tx_id": f"tx-{uuid4()}",
            }
        )
    assert changed_identity.value.error == "operation_id_conflict"
    assert changed_identity.value.details["winning_operation_id"] == winning_operation_id
    assert _financial_snapshot(receipt_writer) == before

    with pytest.raises(ServiceError) as natural_key_conflict:
        receipt_writer.credit_account(
            {
                **winning_data,
                "operation_id": f"op-{uuid4()}",
                "tx_id": f"tx-{uuid4()}",
            }
        )
    assert natural_key_conflict.value.error == "financial_operation_already_committed"
    assert natural_key_conflict.value.details["winning_operation_id"] == winning_operation_id
    assert _financial_snapshot(receipt_writer) == before
    receipt_count = receipt_writer.connection.execute(
        "SELECT COUNT(*) FROM bank_operation_receipts"
    ).fetchone()
    assert receipt_count is not None
    assert int(receipt_count[0]) == 1


def test_lock_replay_keeps_receipt_and_projects_settled_escrow(
    receipt_writer: DbWriter,
) -> None:
    payer_id = f"a-payer-{uuid4()}"
    recipient_id = f"a-recipient-{uuid4()}"
    _register_funded_account(receipt_writer, payer_id, 80, f"op-{uuid4()}")
    _register_zero_account(receipt_writer, recipient_id)
    operation_id = f"op-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    lock_data = {
        "operation_id": operation_id,
        "escrow_id": escrow_id,
        "payer_account_id": payer_id,
        "amount": 50,
        "task_id": f"t-{uuid4()}",
        "created_at": "2026-10-16T09:03:00Z",
        "tx_id": f"tx-{uuid4()}",
        "event": make_event(source="bank", event_type="escrow.locked"),
    }
    locked = receipt_writer.escrow_lock(lock_data)
    released = receipt_writer.escrow_release(
        {
            "operation_id": f"op-{uuid4()}",
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_id,
            "source_reference": f"release-{escrow_id}",
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": "2026-10-16T09:04:00Z",
            "event": make_event(source="bank", event_type="escrow.released"),
        }
    )
    assert released["receipt"]["result"]["escrow_status_at_commit"] == "released"
    before_replay = _financial_snapshot(receipt_writer)

    replay = receipt_writer.escrow_lock(lock_data)

    assert replay["receipt"] == locked["receipt"]
    assert replay["receipt"]["result"]["escrow_status_at_commit"] == "locked"
    assert replay["current_escrow"]["status"] == "released"
    assert _financial_snapshot(receipt_writer) == before_replay


@pytest.mark.parametrize("worker_pct", [0, 100])
def test_split_persists_ordered_logical_zero_legs(
    receipt_writer: DbWriter,
    worker_pct: int,
) -> None:
    payer_id = f"a-payer-{uuid4()}"
    worker_id = f"a-worker-{uuid4()}"
    _register_funded_account(receipt_writer, payer_id, 100, f"op-{uuid4()}")
    if worker_pct > 0:
        _register_zero_account(receipt_writer, worker_id)
    escrow_id = f"esc-{uuid4()}"
    receipt_writer.escrow_lock(
        {
            "operation_id": f"op-{uuid4()}",
            "escrow_id": escrow_id,
            "payer_account_id": payer_id,
            "amount": 100,
            "task_id": f"t-{uuid4()}",
            "created_at": "2026-10-16T09:03:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked"),
        }
    )

    result = receipt_writer.escrow_split(
        {
            "operation_id": f"op-{uuid4()}",
            "escrow_id": escrow_id,
            "worker_account_id": worker_id,
            "poster_account_id": payer_id,
            "worker_pct": worker_pct,
            "source_reference": f"split-{escrow_id}",
            "worker_tx_id": f"tx-{uuid4()}",
            "poster_tx_id": f"tx-{uuid4()}",
            "resolved_at": "2026-10-16T09:05:00Z",
            "event": make_event(source="bank", event_type="escrow.split"),
        }
    )

    worker_leg, poster_leg = result["receipt"]["legs"]
    assert (worker_leg["role"], worker_leg["amount"]) == ("worker", worker_pct)
    assert (poster_leg["role"], poster_leg["amount"]) == ("poster", 100 - worker_pct)
    zero_leg = worker_leg if worker_pct == 0 else poster_leg
    assert zero_leg["transaction_id"] is None
    assert zero_leg["balance_after"] is None
    audit = audit_receipt_ownership(receipt_writer.connection)
    assert audit.financially_clean
    assert audit.ownership_clean


def _settlement_attempt(
    barrier: Barrier,
    writer: DbWriter,
    operation: str,
    operation_id: str,
    escrow_id: str,
    payer_id: str,
    worker_id: str,
    recipient_id: str,
) -> dict[str, Any] | ServiceError:
    barrier.wait(timeout=10)
    try:
        if operation == "release":
            return writer.escrow_release(
                {
                    "operation_id": operation_id,
                    "escrow_id": escrow_id,
                    "recipient_account_id": recipient_id,
                    "source_reference": f"release-{escrow_id}",
                    "tx_id": f"tx-{uuid4()}",
                    "resolved_at": "2026-10-16T09:05:00Z",
                    "event": make_event(source="bank", event_type="escrow.released"),
                }
            )
        return writer.escrow_split(
            {
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "worker_account_id": worker_id,
                "poster_account_id": payer_id,
                "worker_pct": 40,
                "source_reference": f"split-{escrow_id}",
                "worker_tx_id": f"tx-{uuid4()}",
                "poster_tx_id": f"tx-{uuid4()}",
                "resolved_at": "2026-10-16T09:05:00Z",
                "event": make_event(source="bank", event_type="escrow.split"),
            }
        )
    except ServiceError as error:
        return error


def test_opposing_settlements_across_independent_writers_have_one_receipt_winner(
    initialized_db: str,
) -> None:
    """Direct sidecar fixture isolates the writer race, not startup admission."""
    first_writer = DbWriter(initialized_db, 5000, "wal", None)
    second_writer = DbWriter(initialized_db, 5000, "wal", None)
    try:
        payer_id = f"a-payer-{uuid4()}"
        worker_id = f"a-worker-{uuid4()}"
        recipient_id = f"a-recipient-{uuid4()}"
        _register_funded_account(first_writer, payer_id, 100, f"op-{uuid4()}")
        _register_zero_account(first_writer, worker_id)
        _register_zero_account(first_writer, recipient_id)
        escrow_id = f"esc-{uuid4()}"
        first_writer.escrow_lock(
            {
                "operation_id": f"op-{uuid4()}",
                "escrow_id": escrow_id,
                "payer_account_id": payer_id,
                "amount": 100,
                "task_id": f"t-{uuid4()}",
                "created_at": "2026-10-16T09:03:00Z",
                "tx_id": f"tx-{uuid4()}",
                "event": make_event(source="bank", event_type="escrow.locked"),
            }
        )
        before = _financial_snapshot(first_writer)
        release_id = f"op-{uuid4()}"
        split_id = f"op-{uuid4()}"
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            release_future = executor.submit(
                _settlement_attempt,
                barrier,
                first_writer,
                "release",
                release_id,
                escrow_id,
                payer_id,
                worker_id,
                recipient_id,
            )
            split_future = executor.submit(
                _settlement_attempt,
                barrier,
                second_writer,
                "split",
                split_id,
                escrow_id,
                payer_id,
                worker_id,
                recipient_id,
            )
            outcomes = [release_future.result(timeout=15), split_future.result(timeout=15)]

        successes = [outcome for outcome in outcomes if isinstance(outcome, dict)]
        conflicts = [outcome for outcome in outcomes if isinstance(outcome, ServiceError)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        winner_id = successes[0]["receipt"]["operation_id"]
        assert conflicts[0].error == "financial_operation_already_committed"
        assert conflicts[0].details["winning_operation_id"] == winner_id
        assert winner_id in {release_id, split_id}

        receipt_rows = first_writer.connection.execute(
            "SELECT operation_id, operation_kind FROM bank_operation_receipts "
            "WHERE operation_kind IN ('escrow_release', 'escrow_split')"
        ).fetchall()
        assert [tuple(row) for row in receipt_rows] == [
            (
                winner_id,
                "escrow_release" if winner_id == release_id else "escrow_split",
            )
        ]
        before_event_count = len(before["events"])
        before_transaction_count = len(before["bank_transactions"])
        after = _financial_snapshot(first_writer)
        assert len(after["events"]) == before_event_count + 1
        expected_transaction_delta = 1 if winner_id == release_id else 2
        assert (
            len(after["bank_transactions"]) == before_transaction_count + expected_transaction_delta
        )
        audit = audit_receipt_ownership(first_writer.connection)
        assert audit.financially_clean
        assert audit.ownership_clean
        assert not first_writer.connection.in_transaction
        assert not second_writer.connection.in_transaction
    finally:
        second_writer.close()
        first_writer.close()
