"""Migrated financial history remains observable without acquiring native ownership."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service import recovery
from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.migration import migrate_v1_to_v2
from db_gateway_service.services.db_writer import DbWriter
from tests.conftest import make_event
from tests.unit import test_mp04_migration as migration_tests

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


pytestmark = pytest.mark.unit


def _hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prepare_migrated_writer(
    tmp_path: Path,
    modifier: Callable[[sqlite3.Connection], None] | None,
) -> DbWriter:
    inventory, backup_root, work_root, database = migration_tests._prepared_v1(tmp_path)
    backup_id = "before-mp04"
    if modifier is not None:
        connection = sqlite3.connect(database)
        try:
            modifier(connection)
            connection.commit()
        finally:
            connection.close()
        backup_id = f"history-{uuid4()}"
        recovery.create_backup(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
        )
    migrate_v1_to_v2(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    return DbWriter(str(database), 5000, "wal", None)


@pytest.fixture
def migrated_writer(tmp_path: Path) -> Iterator[DbWriter]:
    writer = _prepare_migrated_writer(tmp_path, None)
    yield writer
    writer.close()


def _snapshot(writer: DbWriter) -> dict[str, tuple[tuple[object, ...], ...]]:
    tables = [
        str(row[0])
        for row in writer.connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND (name='events' OR name LIKE 'bank_%') ORDER BY name"
        ).fetchall()
    ]
    return {
        table: tuple(
            sorted(
                (tuple(row) for row in writer.connection.execute(f'SELECT * FROM "{table}"')),
                key=repr,
            )
        )
        for table in tables
    }


def _credit_data(operation_id: str, amount: int, reference: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "tx_id": f"tx-candidate-{uuid4()}",
        "account_id": "a-external",
        "amount": amount,
        "reference": reference,
        "timestamp": "candidate-credit-time",
        "event": make_event(source="bank", event_type="account.credited"),
    }


def _lock_data(operation_id: str, amount: int, task_id: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "escrow_id": f"esc-candidate-{uuid4()}",
        "payer_account_id": "a-external",
        "amount": amount,
        "task_id": task_id,
        "created_at": "candidate-lock-time",
        "tx_id": f"tx-candidate-{uuid4()}",
        "event": make_event(source="bank", event_type="escrow.locked"),
    }


def _release_data(operation_id: str, escrow_id: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "escrow_id": escrow_id,
        "recipient_account_id": "a-worker",
        "source_reference": f"history:{escrow_id}:release",
        "tx_id": f"tx-candidate-{uuid4()}",
        "resolved_at": "candidate-release-time",
        "event": make_event(source="bank", event_type="escrow.released"),
    }


def _split_data(operation_id: str, escrow_id: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "escrow_id": escrow_id,
        "worker_account_id": "a-worker",
        "poster_account_id": "a-external",
        "worker_pct": 40,
        "source_reference": f"history:{escrow_id}:split",
        "worker_tx_id": f"tx-worker-{uuid4()}",
        "poster_tx_id": f"tx-poster-{uuid4()}",
        "resolved_at": "candidate-split-time",
        "event": make_event(source="bank", event_type="escrow.split"),
    }


def _assert_no_native_alias(writer: DbWriter, operation_id: str) -> None:
    assert (
        writer.connection.execute(
            "SELECT 1 FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        is None
    )


def test_exact_historical_credit_returns_observation_without_alias_or_mutation(
    migrated_writer: DbWriter,
) -> None:
    operation_id = f"op-{uuid4()}"
    data = _credit_data(operation_id, 150, "initial")
    marker = migrated_writer.connection.execute(
        "SELECT observed_kind, observed_fact_sha256 "
        "FROM bank_legacy_transaction_observations WHERE tx_id='tx-1'"
    ).fetchone()
    assert marker is not None
    before = _snapshot(migrated_writer)

    result = migrated_writer.credit_account(data)

    request = canonical_request(
        "credit",
        {"account_id": "a-external", "amount": 150, "reference": "initial"},
    )
    assert result == {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": operation_id,
            "operation_kind": "credit",
            "request_sha256": request.request_sha256,
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_credit",
            "transaction": {
                "tx_id": "tx-1",
                "account_id": "a-external",
                "type": "credit",
                "amount": 150,
                "balance_after": 150,
                "reference": "initial",
                "timestamp": "now",
                "event_id": 1,
            },
            "observed_fact_sha256": marker[1],
        },
        "current_escrow": None,
    }
    assert marker[0] == "observed_credit"
    assert _snapshot(migrated_writer) == before
    _assert_no_native_alias(migrated_writer, operation_id)


def test_changed_historical_credit_is_a_null_winner_legacy_conflict(
    migrated_writer: DbWriter,
) -> None:
    operation_id = f"op-{uuid4()}"
    marker = migrated_writer.connection.execute(
        "SELECT observed_kind, observed_fact_sha256 "
        "FROM bank_legacy_transaction_observations WHERE tx_id='tx-1'"
    ).fetchone()
    assert marker is not None
    before = _snapshot(migrated_writer)

    with pytest.raises(ServiceError) as conflict:
        migrated_writer.credit_account(_credit_data(operation_id, 151, "initial"))

    assert (conflict.value.status_code, conflict.value.error) == (409, "legacy_operation_conflict")
    assert conflict.value.details == {
        "winning_operation_id": None,
        "observed_kind": "observed_credit",
        "tx_id": "tx-1",
        "observed_fact_sha256": marker[1],
    }
    assert _snapshot(migrated_writer) == before
    _assert_no_native_alias(migrated_writer, operation_id)


def test_exact_historical_lock_returns_original_facts_and_fresh_projection(
    migrated_writer: DbWriter,
) -> None:
    operation_id = f"op-{uuid4()}"
    escrow_marker = migrated_writer.connection.execute(
        "SELECT observed_status, lock_fact_sha256, resolution_fact_sha256 "
        "FROM bank_legacy_escrow_observations WHERE escrow_id='esc-1'"
    ).fetchone()
    transaction_marker = migrated_writer.connection.execute(
        "SELECT observed_kind, observed_fact_sha256 "
        "FROM bank_legacy_transaction_observations WHERE tx_id='tx-lock'"
    ).fetchone()
    assert escrow_marker is not None
    assert transaction_marker is not None
    before = _snapshot(migrated_writer)

    result = migrated_writer.escrow_lock(_lock_data(operation_id, 50, "t-1"))

    request = canonical_request(
        "escrow_lock",
        {"payer_account_id": "a-external", "task_id": "t-1", "amount": 50},
    )
    assert result == {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": operation_id,
            "operation_kind": "escrow_lock",
            "request_sha256": request.request_sha256,
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_escrow_lock",
            "transaction": {
                "tx_id": "tx-lock",
                "account_id": "a-external",
                "type": "escrow_lock",
                "amount": 50,
                "balance_after": 100,
                "reference": "t-1",
                "timestamp": "now",
                "event_id": 2,
            },
            "observed_fact_sha256": transaction_marker[1],
            "escrow_lock": {
                "escrow_id": "esc-1",
                "payer_account_id": "a-external",
                "amount": 50,
                "task_id": "t-1",
                "created_at": "now",
                "event_id": 2,
            },
            "observed_status": escrow_marker[0],
            "lock_fact_sha256": escrow_marker[1],
            "resolution_fact": None,
            "resolution_fact_sha256": escrow_marker[2],
        },
        "current_escrow": {
            "escrow_id": "esc-1",
            "payer_account_id": "a-external",
            "task_id": "t-1",
            "amount": 50,
            "status": "locked",
            "created_at": "now",
            "resolved_at": None,
        },
    }
    assert _snapshot(migrated_writer) == before
    _assert_no_native_alias(migrated_writer, operation_id)


def test_changed_historical_lock_is_a_null_winner_legacy_conflict(
    migrated_writer: DbWriter,
) -> None:
    operation_id = f"op-{uuid4()}"
    transaction_marker = migrated_writer.connection.execute(
        "SELECT observed_kind, observed_fact_sha256 "
        "FROM bank_legacy_transaction_observations WHERE tx_id='tx-lock'"
    ).fetchone()
    escrow_marker = migrated_writer.connection.execute(
        "SELECT observed_status, lock_fact_sha256, resolution_fact_sha256 "
        "FROM bank_legacy_escrow_observations WHERE escrow_id='esc-1'"
    ).fetchone()
    assert transaction_marker is not None
    assert escrow_marker is not None
    before = _snapshot(migrated_writer)

    with pytest.raises(ServiceError) as conflict:
        migrated_writer.escrow_lock(_lock_data(operation_id, 51, "t-1"))

    assert (conflict.value.status_code, conflict.value.error) == (409, "legacy_operation_conflict")
    assert conflict.value.details == {
        "winning_operation_id": None,
        "observed_kind": "observed_escrow_lock",
        "tx_id": "tx-lock",
        "observed_fact_sha256": transaction_marker[1],
        "escrow_id": "esc-1",
        "observed_status": escrow_marker[0],
        "lock_fact_sha256": escrow_marker[1],
        "resolution_fact_sha256": escrow_marker[2],
    }
    assert _snapshot(migrated_writer) == before
    _assert_no_native_alias(migrated_writer, operation_id)


@pytest.mark.parametrize("historical_command", ["credit", "lock"])
def test_native_global_operation_id_conflict_precedes_historical_classification(
    migrated_writer: DbWriter,
    historical_command: str,
) -> None:
    operation_id = f"op-{uuid4()}"
    native = migrated_writer.credit_account(
        {
            **_credit_data(operation_id, 1, "native-global-owner"),
            "account_id": "a-worker",
        }
    )
    assert native["receipt"]["operation_id"] == operation_id
    before = _snapshot(migrated_writer)

    with pytest.raises(ServiceError) as conflict:
        if historical_command == "credit":
            migrated_writer.credit_account(_credit_data(operation_id, 150, "initial"))
        else:
            migrated_writer.escrow_lock(_lock_data(operation_id, 50, "t-1"))

    assert (conflict.value.status_code, conflict.value.error, conflict.value.details) == (
        409,
        "operation_id_conflict",
        {"winning_operation_id": operation_id},
    )
    assert _snapshot(migrated_writer) == before


def _resolve_v1_escrow(connection: sqlite3.Connection) -> None:
    cursor = connection.execute(
        "INSERT INTO events (event_source, event_type, timestamp, agent_id, summary, payload) "
        "VALUES ('bank', 'escrow.released', 'legacy-resolved', 'a-worker', 'released', '{}')"
    )
    event_id = int(cursor.lastrowid)
    connection.execute("UPDATE bank_accounts SET balance=50 WHERE account_id='a-worker'")
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-release-legacy', 'a-worker', 'escrow_release', 50, 50, "
        "'esc-1', 'legacy-resolved', ?)",
        (event_id,),
    )
    connection.execute(
        "UPDATE bank_escrow SET status='released', resolved_at='legacy-resolved' "
        "WHERE escrow_id='esc-1'"
    )


@pytest.mark.parametrize("settlement", ["release", "split"])
def test_terminal_historical_settlement_is_explicitly_unreplayable(
    tmp_path: Path,
    settlement: str,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, _resolve_v1_escrow)
    operation_id = f"op-{uuid4()}"
    marker = writer.connection.execute(
        "SELECT observed_status, lock_fact_sha256, resolution_fact_sha256 "
        "FROM bank_legacy_escrow_observations WHERE escrow_id='esc-1'"
    ).fetchone()
    assert marker is not None
    before = _snapshot(writer)
    try:
        with pytest.raises(ServiceError) as conflict:
            if settlement == "release":
                writer.escrow_release(_release_data(operation_id, "esc-1"))
            else:
                writer.escrow_split(_split_data(operation_id, "esc-1"))
        assert (conflict.value.status_code, conflict.value.error) == (
            409,
            "historical_settlement_unreplayable",
        )
        assert conflict.value.details == {
            "winning_operation_id": None,
            "escrow_id": "esc-1",
            "observed_status": marker[0],
            "lock_fact_sha256": marker[1],
            "resolution_fact_sha256": marker[2],
        }
        assert _snapshot(writer) == before
        _assert_no_native_alias(writer, operation_id)
    finally:
        writer.close()


def _add_initial_balance_history(connection: sqlite3.Connection) -> None:
    cursor = connection.execute(
        "INSERT INTO events (event_source, event_type, timestamp, agent_id, summary, payload) "
        "VALUES ('bank', 'account.created', 'legacy-initial', 'a-worker', 'initial', '{}')"
    )
    event_id = int(cursor.lastrowid)
    connection.execute("UPDATE bank_accounts SET balance=25 WHERE account_id='a-worker'")
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-initial-balance', 'a-worker', 'credit', 25, 25, "
        "'initial_balance', 'legacy-initial', ?)",
        (event_id,),
    )


def test_initial_balance_history_is_only_an_observed_credit_and_accounts_stay_conflicts(
    tmp_path: Path,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, _add_initial_balance_history)
    try:
        credit_id = f"op-{uuid4()}"
        result = writer.credit_account(
            {
                **_credit_data(credit_id, 25, "initial_balance"),
                "account_id": "a-worker",
            }
        )
        assert result["outcome"] == "legacy_observation"
        assert result["observation"]["observed_kind"] == "observed_credit"
        assert result["observation"]["operation_id"] is None
        _assert_no_native_alias(writer, credit_id)

        for balance in (0, 25):
            account_data: dict[str, Any] = {
                "account_id": "a-worker",
                "balance": balance,
                "created_at": "candidate-account-time",
                "event": make_event(source="bank", event_type="account.created"),
            }
            if balance > 0:
                account_data["operation_id"] = f"op-{uuid4()}"
                account_data["initial_credit"] = {
                    "tx_id": f"tx-candidate-{uuid4()}",
                    "amount": balance,
                    "reference": "initial_balance",
                    "timestamp": "candidate-account-time",
                }
            with pytest.raises(ServiceError) as exists:
                writer.create_account(account_data)
            assert (exists.value.status_code, exists.value.error) == (409, "account_exists")
    finally:
        writer.close()


@pytest.mark.parametrize("corruption", ["missing", "altered", "inserted", "dual"])
def test_postmigration_marker_or_ownership_corruption_fails_before_classification(
    tmp_path: Path,
    corruption: str,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, None)
    try:
        cutover_before = tuple(
            writer.connection.execute("SELECT * FROM bank_receipt_cutover").fetchone() or ()
        )
        if corruption == "missing":
            writer.connection.execute(
                "DELETE FROM bank_legacy_transaction_observations WHERE tx_id='tx-1'"
            )
        elif corruption == "altered":
            writer.connection.execute(
                "UPDATE bank_legacy_transaction_observations "
                "SET observed_fact_sha256=? WHERE tx_id='tx-1'",
                ("f" * 64,),
            )
        elif corruption == "inserted":
            _insert_unowned_credit(writer, "tx-inserted", "inserted-ref", with_marker=True)
        else:
            native = writer.credit_account(
                {
                    **_credit_data(f"op-{uuid4()}", 1, "native-dual"),
                    "account_id": "a-worker",
                }
            )
            transaction_id = native["receipt"]["legs"][0]["transaction_id"]
            _insert_marker_for_transaction(writer, str(transaction_id))
        writer.connection.commit()
        before_attempt = _snapshot(writer)

        with pytest.raises(ServiceError) as failed:
            writer.credit_account(_credit_data(f"op-{uuid4()}", 150, "initial"))

        assert (failed.value.status_code, failed.value.error) == (500, "receipt_ownership_failed")
        assert _snapshot(writer) == before_attempt
        assert (
            tuple(writer.connection.execute("SELECT * FROM bank_receipt_cutover").fetchone() or ())
            == cutover_before
        )
    finally:
        writer.close()


@pytest.mark.parametrize("corruption", ["marker", "cutover"])
@pytest.mark.parametrize("settlement", ["release", "split"])
def test_native_settlement_rejects_corrupt_ownership_of_locked_historical_escrow(
    tmp_path: Path,
    corruption: str,
    settlement: str,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, None)
    operation_id = f"op-{uuid4()}"
    try:
        if corruption == "marker":
            writer.connection.execute(
                "DELETE FROM bank_legacy_escrow_observations WHERE escrow_id='esc-1'"
            )
        else:
            writer.connection.execute(
                "UPDATE bank_receipt_cutover SET marker_set_sha256=? WHERE singleton=1",
                ("f" * 64,),
            )
        writer.connection.commit()
        before = _snapshot(writer)

        with pytest.raises(ServiceError) as failed:
            if settlement == "release":
                writer.escrow_release(_release_data(operation_id, "esc-1"))
            else:
                writer.escrow_split(_split_data(operation_id, "esc-1"))

        assert (failed.value.status_code, failed.value.error) == (
            500,
            "receipt_ownership_failed",
        )
        assert _snapshot(writer) == before
        _assert_no_native_alias(writer, operation_id)
    finally:
        writer.close()


def _insert_unowned_credit(
    writer: DbWriter,
    tx_id: str,
    reference: str,
    *,
    with_marker: bool,
) -> None:
    cursor = writer.connection.execute(
        "INSERT INTO events (event_source, event_type, timestamp, agent_id, summary, payload) "
        "VALUES ('bank', 'account.credited', 'post-cutover', 'a-worker', 'credit', '{}')"
    )
    event_id = int(cursor.lastrowid)
    balance = (
        int(
            writer.connection.execute(
                "SELECT balance FROM bank_accounts WHERE account_id='a-worker'"
            ).fetchone()[0]
        )
        + 1
    )
    writer.connection.execute(
        "UPDATE bank_accounts SET balance=? WHERE account_id='a-worker'", (balance,)
    )
    writer.connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES (?, 'a-worker', 'credit', 1, ?, ?, 'post-cutover', ?)",
        (tx_id, balance, reference, event_id),
    )
    if with_marker:
        _insert_marker_for_transaction(writer, tx_id)


def _insert_marker_for_transaction(writer: DbWriter, tx_id: str) -> None:
    row = writer.connection.execute(
        "SELECT tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id "
        "FROM bank_transactions WHERE tx_id=?",
        (tx_id,),
    ).fetchone()
    assert row is not None
    fact = {
        "tx_id": row[0],
        "account_id": row[1],
        "type": row[2],
        "amount": row[3],
        "balance_after": row[4],
        "reference": row[5],
        "timestamp": row[6],
        "event_id": row[7],
    }
    writer.connection.execute(
        "INSERT INTO bank_legacy_transaction_observations "
        "(tx_id, cutover_singleton, observed_kind, observed_fact_sha256) "
        "VALUES (?, 1, 'observed_credit', ?)",
        (tx_id, _hash(fact)),
    )


def test_valid_cutover_with_selected_unowned_postcutover_effect_keeps_missing_guard(
    tmp_path: Path,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, None)
    try:
        _insert_unowned_credit(writer, "tx-unowned", "unowned-ref", with_marker=False)
        writer.connection.commit()
        before = _snapshot(writer)
        with pytest.raises(ServiceError) as missing:
            writer.credit_account(
                {
                    **_credit_data(f"op-{uuid4()}", 1, "unowned-ref"),
                    "account_id": "a-worker",
                }
            )
        assert (missing.value.status_code, missing.value.error) == (
            500,
            "receipt_ownership_missing",
        )
        assert _snapshot(writer) == before
    finally:
        writer.close()


def test_unsupported_raw_historical_storage_is_corruption_not_coerced_observation(
    tmp_path: Path,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, None)
    try:
        writer.connection.execute(
            "UPDATE bank_transactions SET timestamp=? WHERE tx_id='tx-1'",
            (sqlite3.Binary(b"raw-time"),),
        )
        writer.connection.commit()
        before = _snapshot(writer)
        with pytest.raises(ServiceError) as failed:
            writer.credit_account(_credit_data(f"op-{uuid4()}", 150, "initial"))
        assert (failed.value.status_code, failed.value.error) == (500, "receipt_ownership_failed")
        assert _snapshot(writer) == before
    finally:
        writer.close()


@pytest.mark.parametrize("record_kind", ["credit", "lock"])
def test_raw_selected_identifiers_are_corruption_without_string_normalization(
    tmp_path: Path,
    record_kind: str,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, None)
    operation_id = f"op-{uuid4()}"
    try:
        writer.connection.execute("PRAGMA foreign_keys=OFF")
        if record_kind == "credit":
            writer.connection.execute(
                "UPDATE bank_transactions SET tx_id=? WHERE tx_id='tx-1'",
                (sqlite3.Binary(b"raw-credit-id"),),
            )
        else:
            writer.connection.execute(
                "UPDATE bank_escrow SET escrow_id=? WHERE escrow_id='esc-1'",
                (sqlite3.Binary(b"raw-escrow-id"),),
            )
        writer.connection.commit()
        before = _snapshot(writer)

        with pytest.raises(ServiceError) as failed:
            if record_kind == "credit":
                writer.credit_account(_credit_data(operation_id, 150, "initial"))
            else:
                writer.escrow_lock(_lock_data(operation_id, 50, "t-1"))

        assert (failed.value.status_code, failed.value.error, failed.value.details) == (
            500,
            "receipt_ownership_failed",
            {},
        )
        assert _snapshot(writer) == before
        _assert_no_native_alias(writer, operation_id)
    finally:
        writer.close()


def _insert_native_leg_claiming_historical_lock(writer: DbWriter) -> None:
    operation_id = f"op-{uuid4()}"
    request = canonical_request(
        "credit",
        {"account_id": "a-external", "amount": 50, "reference": "overlap"},
    )
    event = writer.connection.execute(
        "INSERT INTO events (event_source, event_type, timestamp, agent_id, summary, payload) "
        "VALUES ('bank', 'overlap.injected', 'overlap', 'a-external', 'overlap', '{}')"
    )
    event_id = int(event.lastrowid)
    writer.connection.execute(
        "INSERT INTO bank_operation_receipts "
        "(operation_id, receipt_version, operation_kind, canonical_request_json, "
        "request_sha256, source_reference, committed_at, event_id, escrow_id, result_json) "
        "VALUES (?, 1, 'credit', ?, ?, 'overlap', 'overlap', ?, NULL, ?)",
        (
            operation_id,
            request.canonical_json,
            request.request_sha256,
            event_id,
            json.dumps(
                {
                    "account_id": "a-external",
                    "amount": 50,
                    "balance_after": 100,
                    "reference": "overlap",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )
    writer.connection.execute(
        "INSERT INTO bank_operation_legs "
        "(operation_id, leg_index, role, account_id, direction, amount, "
        "transaction_id, balance_after) "
        "VALUES (?, 0, 'account', 'a-external', 'credit', 50, 'tx-lock', 100)",
        (operation_id,),
    )


@pytest.mark.parametrize("command", ["observe_lock", "release", "split"])
def test_native_leg_cannot_overlap_historical_lock_transaction(
    tmp_path: Path,
    command: str,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, None)
    operation_id = f"op-{uuid4()}"
    try:
        _insert_native_leg_claiming_historical_lock(writer)
        writer.connection.commit()
        before = _snapshot(writer)

        with pytest.raises(ServiceError) as failed:
            if command == "observe_lock":
                writer.escrow_lock(_lock_data(operation_id, 50, "t-1"))
            elif command == "release":
                writer.escrow_release(_release_data(operation_id, "esc-1"))
            else:
                writer.escrow_split(_split_data(operation_id, "esc-1"))

        assert (failed.value.status_code, failed.value.error, failed.value.details) == (
            500,
            "receipt_ownership_failed",
            {},
        )
        assert _snapshot(writer) == before
        _assert_no_native_alias(writer, operation_id)
    finally:
        writer.close()


def _admit_empty_historical_values(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO events "
        "(event_id, event_source, event_type, timestamp, agent_id, summary, payload) "
        "VALUES (0, 'bank', 'legacy.empty', '', 'a-external', 'empty', '{}')"
    )
    connection.execute("UPDATE bank_transactions SET timestamp='', event_id=0 WHERE tx_id='tx-1'")
    connection.execute(
        "UPDATE bank_transactions SET timestamp='', event_id=0 WHERE tx_id='tx-lock'"
    )
    connection.execute("UPDATE bank_escrow SET created_at='', event_id=0 WHERE escrow_id='esc-1'")


def test_admitted_empty_times_and_event_zero_are_reproduced_without_normalization(
    tmp_path: Path,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, _admit_empty_historical_values)
    try:
        credit = writer.credit_account(_credit_data(f"op-{uuid4()}", 150, "initial"))
        lock = writer.escrow_lock(_lock_data(f"op-{uuid4()}", 50, "t-1"))
        assert credit["observation"]["transaction"]["timestamp"] == ""
        assert credit["observation"]["transaction"]["event_id"] == 0
        assert lock["observation"]["transaction"]["timestamp"] == ""
        assert lock["observation"]["transaction"]["event_id"] == 0
        assert lock["observation"]["escrow_lock"]["created_at"] == ""
        assert lock["observation"]["escrow_lock"]["event_id"] == 0
    finally:
        writer.close()


def _resolve_with_empty_history(connection: sqlite3.Connection) -> None:
    _resolve_v1_escrow(connection)
    connection.execute("UPDATE bank_transactions SET timestamp='' WHERE tx_id='tx-release-legacy'")
    connection.execute("UPDATE bank_escrow SET resolved_at='' WHERE escrow_id='esc-1'")


def _resolve_with_transactions(
    connection: sqlite3.Connection,
    status: str,
    amounts: tuple[int, ...],
) -> None:
    cursor = connection.execute(
        "INSERT INTO events (event_source, event_type, timestamp, agent_id, summary, payload) "
        "VALUES ('bank', 'escrow.resolved', 'legacy-resolution', 'a-worker', 'resolved', '{}')"
    )
    event_id = int(cursor.lastrowid)
    balance = 0
    for index, amount in enumerate(amounts):
        balance += amount
        connection.execute(
            "INSERT INTO bank_transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
            "VALUES (?, 'a-worker', 'escrow_release', ?, ?, 'esc-1', 'legacy-resolution', ?)",
            (f"tx-resolution-{index}", amount, balance, event_id),
        )
    connection.execute("UPDATE bank_accounts SET balance=? WHERE account_id='a-worker'", (balance,))
    connection.execute(
        "UPDATE bank_escrow SET status=?, resolved_at='legacy-resolution' WHERE escrow_id='esc-1'",
        (status,),
    )


def _one_leg_historical_split(connection: sqlite3.Connection) -> None:
    _resolve_with_transactions(connection, "split", (50,))


def test_historical_split_with_one_positive_transaction_is_a_valid_observation(
    tmp_path: Path,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, _one_leg_historical_split)
    try:
        result = writer.escrow_lock(_lock_data(f"op-{uuid4()}", 50, "t-1"))
        assert result["observation"]["observed_status"] == "split"
        assert result["observation"]["resolution_fact"] == {
            "escrow_id": "esc-1",
            "observed_status": "split",
            "resolved_at": "legacy-resolution",
            "transaction_ids": ["tx-resolution-0"],
        }
    finally:
        writer.close()


@pytest.mark.parametrize("status", ["released", "split"])
def test_terminal_history_preserves_null_resolution_time(
    tmp_path: Path,
    status: str,
) -> None:
    def modifier(connection: sqlite3.Connection) -> None:
        _resolve_with_transactions(connection, status, (50,))
        connection.execute("UPDATE bank_escrow SET resolved_at=NULL WHERE escrow_id='esc-1'")

    writer = _prepare_migrated_writer(tmp_path, modifier)
    try:
        result = writer.escrow_lock(_lock_data(f"op-{uuid4()}", 50, "t-1"))
        assert result["observation"]["observed_status"] == status
        assert result["observation"]["resolution_fact"]["resolved_at"] is None
        assert result["current_escrow"]["status"] == status
        assert result["current_escrow"]["resolved_at"] is None
    finally:
        writer.close()


@pytest.mark.parametrize("resolved_at", ["stale-while-locked", ""])
def test_locked_history_preserves_admitted_nonnull_resolution_time(
    tmp_path: Path,
    resolved_at: str,
) -> None:
    def modifier(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE bank_escrow SET resolved_at=? WHERE escrow_id='esc-1'",
            (resolved_at,),
        )

    writer = _prepare_migrated_writer(tmp_path, modifier)
    try:
        result = writer.escrow_lock(_lock_data(f"op-{uuid4()}", 50, "t-1"))
        assert result["observation"]["observed_status"] == "locked"
        assert result["observation"]["resolution_fact"] is None
        assert result["observation"]["resolution_fact_sha256"] is None
        assert result["current_escrow"]["status"] == "locked"
        assert result["current_escrow"]["resolved_at"] == resolved_at
    finally:
        writer.close()


def test_terminal_lock_observation_preserves_empty_resolution_time(
    tmp_path: Path,
) -> None:
    writer = _prepare_migrated_writer(tmp_path, _resolve_with_empty_history)
    try:
        result = writer.escrow_lock(_lock_data(f"op-{uuid4()}", 50, "t-1"))
        assert result["observation"]["observed_status"] == "released"
        assert result["observation"]["resolution_fact"]["resolved_at"] == ""
        assert result["current_escrow"]["resolved_at"] == ""
    finally:
        writer.close()


def test_historical_lock_observation_stays_immutable_after_native_settlement(
    migrated_writer: DbWriter,
) -> None:
    first_id = f"op-{uuid4()}"
    first = migrated_writer.escrow_lock(_lock_data(first_id, 50, "t-1"))
    historical = json.loads(json.dumps(first["observation"]))
    settlement = migrated_writer.escrow_release(_release_data(f"op-{uuid4()}", "esc-1"))
    assert settlement["outcome"] == "native_receipt"
    before_second_observation = _snapshot(migrated_writer)

    second_id = f"op-{uuid4()}"
    second = migrated_writer.escrow_lock(_lock_data(second_id, 50, "t-1"))

    assert second["observation"] == historical
    assert second["attempt"]["operation_id"] == second_id
    assert second["current_escrow"]["status"] == "released"
    assert second["current_escrow"]["resolved_at"] == "candidate-release-time"
    assert _snapshot(migrated_writer) == before_second_observation
    _assert_no_native_alias(migrated_writer, first_id)
    _assert_no_native_alias(migrated_writer, second_id)
