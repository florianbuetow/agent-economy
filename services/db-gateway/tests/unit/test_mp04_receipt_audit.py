"""Failing-first tests for the MP-04 receipt-v2 sidecar and ownership audit."""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service.schema import SchemaState, load_schema_sql

if TYPE_CHECKING:
    from db_gateway_service.receipt_audit import ReceiptOwnershipAuditResult

pytestmark = pytest.mark.unit

_RECEIPT_TABLES = {
    "bank_operation_receipts",
    "bank_operation_legs",
    "bank_legacy_transaction_observations",
    "bank_legacy_escrow_observations",
    "bank_receipt_cutover",
}
_EMPTY_SET_HASH = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
_EMPTY_MARKER_HASH = "a81b1a47e8cd319a73362c83a992f181986e799b1e82753bf139e60b35d0a4f6"


def _receipt_schema_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "db_gateway_service"
        / "schema_resources"
        / "receipt_v2.sql"
    )


def _v1_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
    return connection


def _v2_connection() -> sqlite3.Connection:
    connection = _v1_connection()
    connection.executescript(_receipt_schema_path().read_text())
    return connection


def _hash_json(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _insert_fresh_cutover(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO bank_receipt_cutover "
        "(singleton, attestation_version, cutover_kind, legacy_transaction_count, "
        "legacy_escrow_count, legacy_transaction_set_sha256, legacy_escrow_set_sha256, "
        "marker_set_sha256) VALUES (1, 1, 'fresh_v2', 0, 0, ?, ?, ?)",
        (_EMPTY_SET_HASH, _EMPTY_SET_HASH, _EMPTY_MARKER_HASH),
    )


def _insert_migrated_cutover(
    connection: sqlite3.Connection,
    transaction_markers: list[dict[str, object]],
    escrow_markers: list[dict[str, object]],
) -> None:
    transaction_set_hash = _hash_json(transaction_markers)
    escrow_set_hash = _hash_json(escrow_markers)
    marker_hash = _hash_json(
        {
            "attestation_version": 1,
            "legacy_escrow_count": len(escrow_markers),
            "legacy_escrow_set_sha256": escrow_set_hash,
            "legacy_transaction_count": len(transaction_markers),
            "legacy_transaction_set_sha256": transaction_set_hash,
        }
    )
    connection.execute(
        "INSERT INTO bank_receipt_cutover "
        "(singleton, attestation_version, cutover_kind, source_schema_version, "
        "migration_backup_id, migration_backup_manifest_sha256, migration_plan_sha256, "
        "source_snapshot_sha256, legacy_transaction_count, legacy_escrow_count, "
        "legacy_transaction_set_sha256, legacy_escrow_set_sha256, marker_set_sha256) "
        "VALUES (1, 1, 'v1_to_v2', 1, 'backup-v1', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "a" * 64,
            "b" * 64,
            "c" * 64,
            len(transaction_markers),
            len(escrow_markers),
            transaction_set_hash,
            escrow_set_hash,
            marker_hash,
        ),
    )


def _add_account(connection: sqlite3.Connection, account_id: str, balance: int) -> None:
    connection.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, 'now')",
        (account_id, account_id, f"key-{account_id}"),
    )
    connection.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, 'now')",
        (account_id, balance),
    )


def _add_event(connection: sqlite3.Connection, event_type: str, agent_id: str) -> int:
    cursor = connection.execute(
        "INSERT INTO events "
        "(event_source, event_type, timestamp, agent_id, summary, payload) "
        "VALUES ('bank', ?, 'now', ?, 'summary', '{}')",
        (event_type, agent_id),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _add_credit(
    connection: sqlite3.Connection,
    *,
    tx_id: str,
    account_id: str,
    amount: int,
    event_id: int | None,
) -> None:
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES (?, ?, 'credit', ?, ?, 'seed', 'now', ?)",
        (tx_id, account_id, amount, amount, event_id),
    )


def _transaction_fact(
    *,
    tx_id: str,
    account_id: str,
    amount: int,
    event_id: int | None,
) -> dict[str, object]:
    return {
        "account_id": account_id,
        "amount": amount,
        "balance_after": amount,
        "event_id": event_id,
        "reference": "seed",
        "timestamp": "now",
        "tx_id": tx_id,
        "type": "credit",
    }


def _lock_fact(*, event_id: int) -> dict[str, object]:
    return {
        "amount": 100,
        "created_at": "now",
        "escrow_id": "esc-zero-worker",
        "event_id": event_id,
        "payer_account_id": "a-poster",
        "task_id": "t-zero-worker",
    }


def _insert_legacy_lock_cutover(
    connection: sqlite3.Connection,
    *,
    credit_event: int,
    lock_event: int,
    escrow_id: str,
    task_id: str,
) -> None:
    transaction_markers = [
        {
            "tx_id": "tx-credit",
            "observed_kind": "observed_credit",
            "observed_fact_sha256": _hash_json(
                _transaction_fact(
                    tx_id="tx-credit",
                    account_id="a-poster",
                    amount=100,
                    event_id=credit_event,
                )
            ),
        },
        {
            "tx_id": "tx-lock",
            "observed_kind": "observed_escrow_lock",
            "observed_fact_sha256": _hash_json(
                {
                    "account_id": "a-poster",
                    "amount": 100,
                    "balance_after": 0,
                    "event_id": lock_event,
                    "reference": task_id,
                    "timestamp": "now",
                    "tx_id": "tx-lock",
                    "type": "escrow_lock",
                }
            ),
        },
    ]
    escrow_marker = {
        "escrow_id": escrow_id,
        "observed_status": "locked",
        "lock_fact_sha256": _hash_json(
            {
                "amount": 100,
                "created_at": "now",
                "escrow_id": escrow_id,
                "event_id": lock_event,
                "payer_account_id": "a-poster",
                "task_id": task_id,
            }
        ),
        "resolution_fact_sha256": None,
    }
    _insert_migrated_cutover(connection, transaction_markers, [escrow_marker])
    for marker in transaction_markers:
        connection.execute(
            "INSERT INTO bank_legacy_transaction_observations VALUES (?, 1, ?, ?)",
            (
                marker["tx_id"],
                marker["observed_kind"],
                marker["observed_fact_sha256"],
            ),
        )
    connection.execute(
        "INSERT INTO bank_legacy_escrow_observations VALUES (?, 1, 'locked', ?, NULL)",
        (escrow_id, escrow_marker["lock_fact_sha256"]),
    )


def _finding_codes(result: ReceiptOwnershipAuditResult) -> set[str]:
    return {finding.code for finding in result.findings}


def test_receipt_v2_sidecar_adds_all_ownership_tables() -> None:
    path = _receipt_schema_path()
    assert path.is_file(), "receipt-v2 schema sidecar is not implemented"
    connection = _v1_connection()
    try:
        connection.executescript(path.read_text())
        actual = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()

    assert actual >= _RECEIPT_TABLES


def test_receipt_ownership_audit_api_exists_without_collection_failure() -> None:
    try:
        module = importlib.import_module("db_gateway_service.receipt_audit")
    except ModuleNotFoundError:
        pytest.fail("receipt ownership audit API is not implemented")

    assert callable(module.audit_receipt_ownership)


def _audit(connection: sqlite3.Connection) -> ReceiptOwnershipAuditResult:
    from db_gateway_service.receipt_audit import audit_receipt_ownership

    return audit_receipt_ownership(connection)


def _add_native_credit_receipt(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    tx_id: str,
    account_id: str,
    amount: int,
    event_id: int,
    leg_amount: int,
    result: dict[str, object],
) -> None:
    from db_gateway_service.financial_requests import canonical_request

    request = canonical_request(
        "credit",
        {"account_id": account_id, "amount": amount, "reference": "seed"},
    )
    connection.execute(
        "INSERT INTO bank_operation_receipts "
        "(operation_id, receipt_version, operation_kind, canonical_request_json, "
        "request_sha256, source_reference, committed_at, event_id, escrow_id, result_json) "
        "VALUES (?, 1, 'credit', ?, ?, ?, 'now', ?, NULL, ?)",
        (
            operation_id,
            request.canonical_json,
            request.request_sha256,
            request.source_reference,
            event_id,
            json.dumps(result, sort_keys=True, separators=(",", ":")),
        ),
    )
    connection.execute(
        "INSERT INTO bank_operation_legs "
        "(operation_id, leg_index, role, account_id, direction, amount, "
        "transaction_id, balance_after) VALUES (?, 0, 'account', ?, 'credit', ?, ?, ?)",
        (operation_id, account_id, leg_amount, tx_id, amount),
    )


def _add_native_account_receipt_fixture(
    connection: sqlite3.Connection,
    *,
    operation_kind: str,
    result: dict[str, object],
) -> str:
    from db_gateway_service.financial_requests import canonical_request

    account_id = "a-strict-result"
    reference = "initial_balance" if operation_kind == "account_issuance" else "seed"
    _add_account(connection, account_id, 1)
    event_id = _add_event(connection, "account.credited", account_id)
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-strict-result', ?, 'credit', 1, 1, ?, 'now', ?)",
        (account_id, reference, event_id),
    )
    request = canonical_request(
        operation_kind,
        {"account_id": account_id, "amount": 1, "reference": reference},
    )
    operation_id = f"op-{uuid4()}"
    connection.execute(
        "INSERT INTO bank_operation_receipts "
        "(operation_id, receipt_version, operation_kind, canonical_request_json, "
        "request_sha256, source_reference, committed_at, event_id, result_json) "
        "VALUES (?, 1, ?, ?, ?, ?, 'now', ?, ?)",
        (
            operation_id,
            operation_kind,
            request.canonical_json,
            request.request_sha256,
            request.source_reference,
            event_id,
            json.dumps(result, sort_keys=True, separators=(",", ":")),
        ),
    )
    connection.execute(
        "INSERT INTO bank_operation_legs "
        "(operation_id, leg_index, role, account_id, direction, amount, "
        "transaction_id, balance_after) "
        "VALUES (?, 0, 'account', ?, 'credit', 1, 'tx-strict-result', 1)",
        (operation_id, account_id),
    )
    _insert_fresh_cutover(connection)
    return operation_id


def _add_clean_native_settlement_fixture(
    connection: sqlite3.Connection,
    *,
    operation_kind: str,
) -> str:
    from db_gateway_service.financial_requests import canonical_request

    escrow_id = "esc-native-settlement"
    task_id = "t-native-settlement"
    is_release = operation_kind == "escrow_release"
    _add_account(connection, "a-poster", 0 if is_release else 50)
    _add_account(connection, "a-worker", 100 if is_release else 50)
    credit_event = _add_event(connection, "account.credited", "a-poster")
    lock_event = _add_event(connection, "escrow.locked", "a-poster")
    settlement_event = _add_event(connection, "escrow.settled", "a-poster")
    _add_credit(
        connection,
        tx_id="tx-credit",
        account_id="a-poster",
        amount=100,
        event_id=credit_event,
    )
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-lock', 'a-poster', 'escrow_lock', 100, 0, ?, 'now', ?)",
        (task_id, lock_event),
    )
    status = "released" if is_release else "split"
    connection.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, "
        "resolved_at, event_id) VALUES (?, 'a-poster', 100, ?, ?, 'now', 'now', ?)",
        (escrow_id, task_id, status, lock_event),
    )
    settlement_legs: list[tuple[str, str, int]]
    if is_release:
        settlement_legs = [("recipient", "a-worker", 100)]
    else:
        settlement_legs = [("worker", "a-worker", 50), ("poster", "a-poster", 50)]
    for role, account_id, amount in settlement_legs:
        connection.execute(
            "INSERT INTO bank_transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
            "VALUES (?, ?, 'escrow_release', ?, ?, ?, 'now', ?)",
            (f"tx-{role}", account_id, amount, amount, escrow_id, settlement_event),
        )
    _insert_legacy_lock_cutover(
        connection,
        credit_event=credit_event,
        lock_event=lock_event,
        escrow_id=escrow_id,
        task_id=task_id,
    )
    fields: dict[str, object]
    result: dict[str, object]
    if is_release:
        fields = {
            "escrow_id": escrow_id,
            "recipient_account_id": "a-worker",
            "source_reference": "task:settle",
        }
        result = {"escrow_status_at_commit": "released"}
    else:
        fields = {
            "escrow_id": escrow_id,
            "worker_account_id": "a-worker",
            "poster_account_id": "a-poster",
            "worker_pct": 50,
            "source_reference": "task:settle",
        }
        result = {"escrow_status_at_commit": "split", "worker_pct": 50}
    request = canonical_request(operation_kind, fields)
    operation_id = f"op-{uuid4()}"
    connection.execute(
        "INSERT INTO bank_operation_receipts "
        "(operation_id, receipt_version, operation_kind, canonical_request_json, "
        "request_sha256, source_reference, committed_at, event_id, escrow_id, result_json) "
        "VALUES (?, 1, ?, ?, ?, ?, 'now', ?, ?, ?)",
        (
            operation_id,
            operation_kind,
            request.canonical_json,
            request.request_sha256,
            request.source_reference,
            settlement_event,
            escrow_id,
            json.dumps(result, sort_keys=True, separators=(",", ":")),
        ),
    )
    connection.executemany(
        "INSERT INTO bank_operation_legs "
        "(operation_id, leg_index, role, account_id, direction, amount, "
        "transaction_id, balance_after) VALUES (?, ?, ?, ?, 'credit', ?, ?, ?)",
        tuple(
            (
                operation_id,
                index,
                role,
                account_id,
                amount,
                f"tx-{role}",
                amount,
            )
            for index, (role, account_id, amount) in enumerate(settlement_legs)
        ),
    )
    return operation_id


def _add_clean_native_lock_fixture(connection: sqlite3.Connection) -> str:
    from db_gateway_service.financial_requests import canonical_request

    _add_account(connection, "a-poster", 0)
    credit_event = _add_event(connection, "account.credited", "a-poster")
    lock_event = _add_event(connection, "escrow.locked", "a-poster")
    _add_credit(
        connection,
        tx_id="tx-credit",
        account_id="a-poster",
        amount=100,
        event_id=credit_event,
    )
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-lock', 'a-poster', 'escrow_lock', 100, 0, 't-native-lock', 'now', ?)",
        (lock_event,),
    )
    connection.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, event_id) "
        "VALUES ('esc-native-lock', 'a-poster', 100, 't-native-lock', 'locked', 'now', ?)",
        (lock_event,),
    )
    credit_fact = _transaction_fact(
        tx_id="tx-credit",
        account_id="a-poster",
        amount=100,
        event_id=credit_event,
    )
    marker = {
        "tx_id": "tx-credit",
        "observed_kind": "observed_credit",
        "observed_fact_sha256": _hash_json(credit_fact),
    }
    _insert_migrated_cutover(connection, [marker], [])
    connection.execute(
        "INSERT INTO bank_legacy_transaction_observations VALUES (?, 1, ?, ?)",
        (marker["tx_id"], marker["observed_kind"], marker["observed_fact_sha256"]),
    )
    request = canonical_request(
        "escrow_lock",
        {"payer_account_id": "a-poster", "task_id": "t-native-lock", "amount": 100},
    )
    operation_id = f"op-{uuid4()}"
    connection.execute(
        "INSERT INTO bank_operation_receipts "
        "(operation_id, receipt_version, operation_kind, canonical_request_json, "
        "request_sha256, source_reference, committed_at, event_id, escrow_id, result_json) "
        "VALUES (?, 1, 'escrow_lock', ?, ?, ?, 'now', ?, 'esc-native-lock', ?)",
        (
            operation_id,
            request.canonical_json,
            request.request_sha256,
            request.source_reference,
            lock_event,
            '{"escrow_status_at_commit":"locked"}',
        ),
    )
    connection.execute(
        "INSERT INTO bank_operation_legs "
        "(operation_id, leg_index, role, account_id, direction, amount, "
        "transaction_id, balance_after) "
        "VALUES (?, 0, 'payer', 'a-poster', 'debit', 100, 'tx-lock', 0)",
        (operation_id,),
    )
    return operation_id


def _add_clean_legacy_release_fixture(connection: sqlite3.Connection) -> None:
    _add_account(connection, "a-poster", 0)
    _add_account(connection, "a-worker", 100)
    credit_event = _add_event(connection, "account.credited", "a-poster")
    lock_event = _add_event(connection, "escrow.locked", "a-poster")
    release_event = _add_event(connection, "escrow.released", "a-worker")
    _add_credit(
        connection,
        tx_id="tx-credit",
        account_id="a-poster",
        amount=100,
        event_id=credit_event,
    )
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-lock', 'a-poster', 'escrow_lock', 100, 0, 't-legacy', 'now', ?)",
        (lock_event,),
    )
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-release', 'a-worker', 'escrow_release', 100, 100, "
        "'esc-legacy', 'now', ?)",
        (release_event,),
    )
    connection.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, "
        "resolved_at, event_id) VALUES "
        "('esc-legacy', 'a-poster', 100, 't-legacy', 'released', 'now', 'now', ?)",
        (lock_event,),
    )
    facts = {
        "tx-credit": _transaction_fact(
            tx_id="tx-credit",
            account_id="a-poster",
            amount=100,
            event_id=credit_event,
        ),
        "tx-lock": {
            "account_id": "a-poster",
            "amount": 100,
            "balance_after": 0,
            "event_id": lock_event,
            "reference": "t-legacy",
            "timestamp": "now",
            "tx_id": "tx-lock",
            "type": "escrow_lock",
        },
        "tx-release": {
            "account_id": "a-worker",
            "amount": 100,
            "balance_after": 100,
            "event_id": release_event,
            "reference": "esc-legacy",
            "timestamp": "now",
            "tx_id": "tx-release",
            "type": "escrow_release",
        },
    }
    kinds = {
        "tx-credit": "observed_credit",
        "tx-lock": "observed_escrow_lock",
        "tx-release": "observed_escrow_release",
    }
    transaction_markers = [
        {
            "tx_id": tx_id,
            "observed_kind": kinds[tx_id],
            "observed_fact_sha256": _hash_json(fact),
        }
        for tx_id, fact in sorted(facts.items())
    ]
    escrow_marker = {
        "escrow_id": "esc-legacy",
        "observed_status": "released",
        "lock_fact_sha256": _hash_json(
            {
                "amount": 100,
                "created_at": "now",
                "escrow_id": "esc-legacy",
                "event_id": lock_event,
                "payer_account_id": "a-poster",
                "task_id": "t-legacy",
            }
        ),
        "resolution_fact_sha256": _hash_json(
            {
                "escrow_id": "esc-legacy",
                "observed_status": "released",
                "resolved_at": "now",
                "transaction_ids": ["tx-release"],
            }
        ),
    }
    _insert_migrated_cutover(connection, transaction_markers, [escrow_marker])
    for marker in transaction_markers:
        connection.execute(
            "INSERT INTO bank_legacy_transaction_observations VALUES (?, 1, ?, ?)",
            (
                marker["tx_id"],
                marker["observed_kind"],
                marker["observed_fact_sha256"],
            ),
        )
    connection.execute(
        "INSERT INTO bank_legacy_escrow_observations VALUES (?, 1, 'released', ?, ?)",
        (
            "esc-legacy",
            escrow_marker["lock_fact_sha256"],
            escrow_marker["resolution_fact_sha256"],
        ),
    )


def test_v1_without_sidecar_is_explicitly_ineligible_for_receipt_ownership() -> None:
    connection = _v1_connection()
    try:
        result = _audit(connection)
    finally:
        connection.close()

    assert result.ownership_clean is False
    assert result.financially_clean is True
    assert _finding_codes(result) == {"missing_receipt_table"}


def test_empty_fresh_v2_has_complete_zero_effect_attestation() -> None:
    connection = _v2_connection()
    try:
        _insert_fresh_cutover(connection)
        result = _audit(connection)
    finally:
        connection.close()

    assert result.ownership_clean is True
    assert result.financially_clean is True
    assert result.findings == ()
    assert result.transaction_count == 0
    assert result.escrow_count == 0


def test_valid_legacy_transaction_marker_and_cutover_are_clean() -> None:
    connection = _v2_connection()
    tx_id = "tx-legacy-credit"
    fact = _transaction_fact(tx_id=tx_id, account_id="a-one", amount=10, event_id=None)
    marker = {
        "tx_id": tx_id,
        "observed_kind": "observed_credit",
        "observed_fact_sha256": _hash_json(fact),
    }
    try:
        _add_account(connection, "a-one", 10)
        _add_credit(connection, tx_id=tx_id, account_id="a-one", amount=10, event_id=None)
        _insert_migrated_cutover(connection, [marker], [])
        connection.execute(
            "INSERT INTO bank_legacy_transaction_observations "
            "(tx_id, cutover_singleton, observed_kind, observed_fact_sha256) "
            "VALUES (?, 1, 'observed_credit', ?)",
            (tx_id, marker["observed_fact_sha256"]),
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.ownership_clean is True
    assert result.findings == ()
    assert result.legacy_transaction_count == 1


def test_valid_resolved_legacy_escrow_has_total_historical_ownership() -> None:
    connection = _v2_connection()
    try:
        _add_clean_legacy_release_fixture(connection)
        result = _audit(connection)
    finally:
        connection.close()

    assert result.ownership_clean is True
    assert result.findings == ()
    assert result.legacy_transaction_count == 3
    assert result.legacy_escrow_count == 1


def test_missing_legacy_escrow_marker_exposes_lock_and_resolution_ownership_gaps() -> None:
    connection = _v2_connection()
    try:
        _add_clean_legacy_release_fixture(connection)
        clean_result = _audit(connection)
        connection.execute(
            "DELETE FROM bank_legacy_escrow_observations WHERE escrow_id = 'esc-legacy'"
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert {
        "unowned_escrow_lock",
        "invalid_escrow_resolution_ownership",
        "cutover_attestation_mismatch",
    } <= _finding_codes(result)


def test_native_receipts_cannot_duplicate_legacy_lock_or_resolution_ownership() -> None:
    from db_gateway_service.financial_requests import canonical_request

    connection = _v2_connection()
    try:
        _add_clean_legacy_release_fixture(connection)
        clean_result = _audit(connection)
        event_rows = connection.execute(
            "SELECT event_type, event_id FROM events "
            "WHERE event_type IN ('escrow.locked', 'escrow.released')"
        ).fetchall()
        event_ids = {str(row[0]): int(row[1]) for row in event_rows}
        operations = (
            (
                "escrow_lock",
                canonical_request(
                    "escrow_lock",
                    {"payer_account_id": "a-poster", "task_id": "t-legacy", "amount": 100},
                ),
                event_ids["escrow.locked"],
                '{"escrow_status_at_commit":"locked"}',
                "payer",
                "a-poster",
                "debit",
                "tx-lock",
                0,
            ),
            (
                "escrow_release",
                canonical_request(
                    "escrow_release",
                    {
                        "escrow_id": "esc-legacy",
                        "recipient_account_id": "a-worker",
                        "source_reference": "task:legacy:release",
                    },
                ),
                event_ids["escrow.released"],
                '{"escrow_status_at_commit":"released"}',
                "recipient",
                "a-worker",
                "credit",
                "tx-release",
                100,
            ),
        )
        for (
            kind,
            request,
            event_id,
            result_json,
            role,
            account_id,
            direction,
            tx_id,
            balance_after,
        ) in operations:
            operation_id = f"op-{uuid4()}"
            connection.execute(
                "INSERT INTO bank_operation_receipts "
                "(operation_id, receipt_version, operation_kind, canonical_request_json, "
                "request_sha256, source_reference, committed_at, event_id, escrow_id, "
                "result_json) VALUES (?, 1, ?, ?, ?, ?, 'now', ?, 'esc-legacy', ?)",
                (
                    operation_id,
                    kind,
                    request.canonical_json,
                    request.request_sha256,
                    request.source_reference,
                    event_id,
                    result_json,
                ),
            )
            connection.execute(
                "INSERT INTO bank_operation_legs "
                "(operation_id, leg_index, role, account_id, direction, amount, "
                "transaction_id, balance_after) VALUES (?, 0, ?, ?, ?, 100, ?, ?)",
                (operation_id, role, account_id, direction, tx_id, balance_after),
            )
        result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert {
        "multiple_transaction_owners",
        "multiple_escrow_lock_owners",
        "invalid_escrow_resolution_ownership",
    } <= _finding_codes(result)


def test_unmarked_transaction_is_not_receipt_ownership_clean() -> None:
    connection = _v2_connection()
    try:
        _add_account(connection, "a-one", 10)
        _add_credit(
            connection,
            tx_id="tx-unowned",
            account_id="a-one",
            amount=10,
            event_id=None,
        )
        _insert_fresh_cutover(connection)
        result = _audit(connection)
    finally:
        connection.close()

    assert result.ownership_clean is False
    assert "unowned_transaction" in _finding_codes(result)


def test_valid_native_credit_receipt_owns_exact_transaction() -> None:
    connection = _v2_connection()
    operation_id = f"op-{uuid4()}"
    try:
        _add_account(connection, "a-one", 10)
        event_id = _add_event(connection, "account.credited", "a-one")
        _add_credit(
            connection,
            tx_id="tx-native",
            account_id="a-one",
            amount=10,
            event_id=event_id,
        )
        _insert_fresh_cutover(connection)
        _add_native_credit_receipt(
            connection,
            operation_id=operation_id,
            tx_id="tx-native",
            account_id="a-one",
            amount=10,
            event_id=event_id,
            leg_amount=10,
            result={
                "account_id": "a-one",
                "amount": 10,
                "balance_after": 10,
                "reference": "seed",
            },
        )
        before = connection.serialize()
        changes_before = connection.total_changes
        result = _audit(connection)
        after = connection.serialize()
        changes_after = connection.total_changes
    finally:
        connection.close()

    assert after == before
    assert changes_after == changes_before
    assert result.ownership_clean is True
    assert result.findings == ()
    assert result.native_receipt_count == 1


@pytest.mark.parametrize(
    ("operation_kind", "field", "invalid_value"),
    [
        pytest.param("account_issuance", "issued_amount", True, id="issuance-bool-amount"),
        pytest.param("account_issuance", "issued_amount", 1.0, id="issuance-float-amount"),
        pytest.param("account_issuance", "balance_after", True, id="issuance-bool-balance"),
        pytest.param("account_issuance", "balance_after", 1.0, id="issuance-float-balance"),
        pytest.param("credit", "amount", True, id="credit-bool-amount"),
        pytest.param("credit", "amount", 1.0, id="credit-float-amount"),
        pytest.param("credit", "balance_after", True, id="credit-bool-balance"),
        pytest.param("credit", "balance_after", 1.0, id="credit-float-balance"),
        pytest.param("credit", "balance_after", 2, id="credit-wrong-balance"),
    ],
)
def test_native_account_result_requires_exact_integer_types_and_ledger_balance(
    operation_kind: str,
    field: str,
    invalid_value: object,
) -> None:
    account_id = "a-strict-result"
    result: dict[str, object]
    if operation_kind == "account_issuance":
        result = {"account_id": account_id, "issued_amount": 1, "balance_after": 1}
    else:
        result = {
            "account_id": account_id,
            "amount": 1,
            "balance_after": 1,
            "reference": "seed",
        }
    result[field] = invalid_value
    connection = _v2_connection()
    try:
        valid_result = (
            {"account_id": account_id, "issued_amount": 1, "balance_after": 1}
            if operation_kind == "account_issuance"
            else {
                "account_id": account_id,
                "amount": 1,
                "balance_after": 1,
                "reference": "seed",
            }
        )
        operation_id = _add_native_account_receipt_fixture(
            connection,
            operation_kind=operation_kind,
            result=valid_result,
        )
        clean_result = _audit(connection)
        connection.execute(
            "UPDATE bank_operation_receipts SET result_json = ? WHERE operation_id = ?",
            (json.dumps(result, sort_keys=True, separators=(",", ":")), operation_id),
        )
        audit_result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert clean_result.findings == ()
    assert audit_result.ownership_clean is False
    assert "receipt_result_mismatch" in _finding_codes(audit_result)


@pytest.mark.parametrize(
    ("raw_result", "expected_code"),
    [
        pytest.param(
            '{"account_id":"a-strict-result","amount":1,"amount":1,'
            '"balance_after":1,"reference":"seed"}',
            "invalid_receipt_result",
            id="duplicate-key",
        ),
        pytest.param(
            '{"account_id":"a-strict-result","amount":1,"balance_after":1,'
            '"reference":"seed","extra":true}',
            "receipt_result_mismatch",
            id="extra-key",
        ),
    ],
)
def test_native_result_rejects_duplicate_or_extra_keys(
    raw_result: str,
    expected_code: str,
) -> None:
    connection = _v2_connection()
    valid_result = {
        "account_id": "a-strict-result",
        "amount": 1,
        "balance_after": 1,
        "reference": "seed",
    }
    try:
        operation_id = _add_native_account_receipt_fixture(
            connection,
            operation_kind="credit",
            result=valid_result,
        )
        clean_result = _audit(connection)
        connection.execute(
            "UPDATE bank_operation_receipts SET result_json = ? WHERE operation_id = ?",
            (raw_result, operation_id),
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert expected_code in _finding_codes(result)


@pytest.mark.parametrize("operation_kind", ["escrow_release", "escrow_split"])
def test_native_settlement_request_escrow_must_match_owned_effect(operation_kind: str) -> None:
    from db_gateway_service.financial_requests import canonical_request

    connection = _v2_connection()
    try:
        operation_id = _add_clean_native_settlement_fixture(
            connection,
            operation_kind=operation_kind,
        )
        clean_result = _audit(connection)
        if operation_kind == "escrow_release":
            fields: dict[str, object] = {
                "escrow_id": "esc-other",
                "recipient_account_id": "a-worker",
                "source_reference": "task:settle",
            }
        else:
            fields = {
                "escrow_id": "esc-other",
                "worker_account_id": "a-worker",
                "poster_account_id": "a-poster",
                "worker_pct": 50,
                "source_reference": "task:settle",
            }
        changed = canonical_request(operation_kind, fields)
        connection.execute(
            "UPDATE bank_operation_receipts SET canonical_request_json = ?, "
            "request_sha256 = ? WHERE operation_id = ?",
            (changed.canonical_json, changed.request_sha256, operation_id),
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert clean_result.findings == ()
    assert "receipt_effect_unverifiable" in _finding_codes(result)


def test_native_settlement_resolved_time_must_match_receipt_commit_time() -> None:
    connection = _v2_connection()
    try:
        _add_clean_native_settlement_fixture(
            connection,
            operation_kind="escrow_release",
        )
        clean_result = _audit(connection)
        connection.execute(
            "UPDATE bank_escrow SET resolved_at = 'different' "
            "WHERE escrow_id = 'esc-native-settlement'"
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert clean_result.findings == ()
    assert "receipt_escrow_effect_mismatch" in _finding_codes(result)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        pytest.param("created_at", "different", id="created-at"),
        pytest.param("event_id", 1, id="event"),
    ],
)
def test_native_lock_escrow_time_and_event_must_match_receipt(
    column: str,
    value: object,
) -> None:
    connection = _v2_connection()
    try:
        _add_clean_native_lock_fixture(connection)
        clean_result = _audit(connection)
        if column == "created_at":
            connection.execute(
                "UPDATE bank_escrow SET created_at = ? WHERE escrow_id = 'esc-native-lock'",
                (value,),
            )
        else:
            connection.execute(
                "UPDATE bank_escrow SET event_id = ? WHERE escrow_id = 'esc-native-lock'",
                (value,),
            )
        result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert clean_result.findings == ()
    assert "receipt_escrow_effect_mismatch" in _finding_codes(result)


def test_native_leg_mismatch_is_detected_even_when_money_audit_is_clean() -> None:
    connection = _v2_connection()
    try:
        _add_account(connection, "a-one", 10)
        event_id = _add_event(connection, "account.credited", "a-one")
        _add_credit(
            connection,
            tx_id="tx-native",
            account_id="a-one",
            amount=10,
            event_id=event_id,
        )
        _insert_fresh_cutover(connection)
        _add_native_credit_receipt(
            connection,
            operation_id=f"op-{uuid4()}",
            tx_id="tx-native",
            account_id="a-one",
            amount=10,
            event_id=event_id,
            leg_amount=9,
            result={
                "account_id": "a-one",
                "amount": 10,
                "balance_after": 10,
                "reference": "seed",
            },
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.financially_clean is True
    assert result.ownership_clean is False
    assert "receipt_leg_mismatch" in _finding_codes(result)


def test_native_and_legacy_transaction_ownership_are_disjoint() -> None:
    connection = _v2_connection()
    tx_id = "tx-duplicate-owner"
    operation_id = f"op-{uuid4()}"
    try:
        _add_account(connection, "a-one", 10)
        event_id = _add_event(connection, "account.credited", "a-one")
        _add_credit(
            connection,
            tx_id=tx_id,
            account_id="a-one",
            amount=10,
            event_id=event_id,
        )
        fact = _transaction_fact(
            tx_id=tx_id,
            account_id="a-one",
            amount=10,
            event_id=event_id,
        )
        marker = {
            "tx_id": tx_id,
            "observed_kind": "observed_credit",
            "observed_fact_sha256": _hash_json(fact),
        }
        _insert_migrated_cutover(connection, [marker], [])
        connection.execute(
            "INSERT INTO bank_legacy_transaction_observations VALUES (?, 1, ?, ?)",
            (tx_id, "observed_credit", marker["observed_fact_sha256"]),
        )
        _add_native_credit_receipt(
            connection,
            operation_id=operation_id,
            tx_id=tx_id,
            account_id="a-one",
            amount=10,
            event_id=event_id,
            leg_amount=10,
            result={
                "account_id": "a-one",
                "amount": 10,
                "balance_after": 10,
                "reference": "seed",
            },
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.ownership_clean is False
    assert "multiple_transaction_owners" in _finding_codes(result)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        pytest.param("request_sha256", "f" * 64, id="request-hash"),
        pytest.param("source_reference", "changed-source", id="source-reference"),
    ],
)
def test_native_receipt_request_hash_and_source_must_match_canonical_request(
    column: str,
    value: str,
) -> None:
    connection = _v2_connection()
    valid_result = {
        "account_id": "a-strict-result",
        "amount": 1,
        "balance_after": 1,
        "reference": "seed",
    }
    try:
        operation_id = _add_native_account_receipt_fixture(
            connection,
            operation_kind="credit",
            result=valid_result,
        )
        clean_result = _audit(connection)
        statement = (
            "UPDATE bank_operation_receipts SET request_sha256 = ? WHERE operation_id = ?"
            if column == "request_sha256"
            else "UPDATE bank_operation_receipts SET source_reference = ? WHERE operation_id = ?"
        )
        connection.execute(statement, (value, operation_id))
        result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert "receipt_request_mismatch" in _finding_codes(result)


def test_deleting_native_receipt_exposes_unowned_effect() -> None:
    connection = _v2_connection()
    valid_result = {
        "account_id": "a-strict-result",
        "amount": 1,
        "balance_after": 1,
        "reference": "seed",
    }
    try:
        operation_id = _add_native_account_receipt_fixture(
            connection,
            operation_kind="credit",
            result=valid_result,
        )
        clean_result = _audit(connection)
        connection.execute(
            "DELETE FROM bank_operation_legs WHERE operation_id = ?", (operation_id,)
        )
        connection.execute(
            "DELETE FROM bank_operation_receipts WHERE operation_id = ?",
            (operation_id,),
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert "unowned_transaction" in _finding_codes(result)


def test_orphan_zero_leg_is_detected_even_without_a_transaction() -> None:
    connection = _v2_connection()
    try:
        _insert_fresh_cutover(connection)
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO bank_operation_legs "
            "(operation_id, leg_index, role, account_id, direction, amount, "
            "transaction_id, balance_after) VALUES "
            "('op-00000000-0000-4000-8000-000000000001', 0, 'worker', "
            "'a-absent', 'credit', 0, NULL, NULL)"
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert "orphan_receipt_leg" in _finding_codes(result)


def test_changed_legacy_marker_cannot_hide_behind_frozen_cutover_hash() -> None:
    connection = _v2_connection()
    tx_id = "tx-frozen-marker"
    fact = _transaction_fact(tx_id=tx_id, account_id="a-one", amount=10, event_id=None)
    marker = {
        "tx_id": tx_id,
        "observed_kind": "observed_credit",
        "observed_fact_sha256": _hash_json(fact),
    }
    try:
        _add_account(connection, "a-one", 10)
        _add_credit(connection, tx_id=tx_id, account_id="a-one", amount=10, event_id=None)
        _insert_migrated_cutover(connection, [marker], [])
        connection.execute(
            "INSERT INTO bank_legacy_transaction_observations VALUES (?, 1, ?, ?)",
            (tx_id, "observed_credit", "d" * 64),
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.ownership_clean is False
    assert {
        "legacy_transaction_fact_mismatch",
        "cutover_attestation_mismatch",
    } <= _finding_codes(result)


def test_native_split_zero_leg_needs_no_worker_account_and_has_total_ownership() -> None:
    from db_gateway_service.financial_requests import canonical_request

    connection = _v2_connection()
    operation_id = f"op-{uuid4()}"
    try:
        _add_account(connection, "a-poster", 100)
        credit_event = _add_event(connection, "account.credited", "a-poster")
        lock_event = _add_event(connection, "escrow.locked", "a-poster")
        split_event = _add_event(connection, "escrow.split", "a-poster")
        _add_credit(
            connection,
            tx_id="tx-credit",
            account_id="a-poster",
            amount=100,
            event_id=credit_event,
        )
        connection.execute(
            "INSERT INTO bank_transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
            "VALUES ('tx-lock', 'a-poster', 'escrow_lock', 100, 0, "
            "'t-zero-worker', 'now', ?)",
            (lock_event,),
        )
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at, "
            "resolved_at, event_id) VALUES "
            "('esc-zero-worker', 'a-poster', 100, 't-zero-worker', 'split', "
            "'now', 'now', ?)",
            (lock_event,),
        )
        connection.execute(
            "INSERT INTO bank_transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
            "VALUES ('tx-poster-release', 'a-poster', 'escrow_release', 100, 100, "
            "'esc-zero-worker', 'now', ?)",
            (split_event,),
        )
        transaction_markers = []
        for tx_id, transaction_type, event_id in (
            ("tx-credit", "credit", credit_event),
            ("tx-lock", "escrow_lock", lock_event),
        ):
            if transaction_type == "credit":
                fact = _transaction_fact(
                    tx_id=tx_id,
                    account_id="a-poster",
                    amount=100,
                    event_id=event_id,
                )
                observed_kind = "observed_credit"
            else:
                fact = {
                    "account_id": "a-poster",
                    "amount": 100,
                    "balance_after": 0,
                    "event_id": event_id,
                    "reference": "t-zero-worker",
                    "timestamp": "now",
                    "tx_id": tx_id,
                    "type": "escrow_lock",
                }
                observed_kind = "observed_escrow_lock"
            transaction_markers.append(
                {
                    "tx_id": tx_id,
                    "observed_kind": observed_kind,
                    "observed_fact_sha256": _hash_json(fact),
                }
            )
        escrow_marker = {
            "escrow_id": "esc-zero-worker",
            "observed_status": "locked",
            "lock_fact_sha256": _hash_json(_lock_fact(event_id=lock_event)),
            "resolution_fact_sha256": None,
        }
        _insert_migrated_cutover(connection, transaction_markers, [escrow_marker])
        for marker in transaction_markers:
            connection.execute(
                "INSERT INTO bank_legacy_transaction_observations VALUES (?, 1, ?, ?)",
                (
                    marker["tx_id"],
                    marker["observed_kind"],
                    marker["observed_fact_sha256"],
                ),
            )
        connection.execute(
            "INSERT INTO bank_legacy_escrow_observations VALUES (?, 1, 'locked', ?, NULL)",
            ("esc-zero-worker", escrow_marker["lock_fact_sha256"]),
        )
        request = canonical_request(
            "escrow_split",
            {
                "escrow_id": "esc-zero-worker",
                "worker_account_id": "a-worker-without-account",
                "poster_account_id": "a-poster",
                "worker_pct": 0,
                "source_reference": "task:t-zero-worker:split",
            },
        )
        connection.execute(
            "INSERT INTO bank_operation_receipts "
            "(operation_id, receipt_version, operation_kind, canonical_request_json, "
            "request_sha256, source_reference, committed_at, event_id, escrow_id, result_json) "
            "VALUES (?, 1, 'escrow_split', ?, ?, ?, 'now', ?, 'esc-zero-worker', ?)",
            (
                operation_id,
                request.canonical_json,
                request.request_sha256,
                request.source_reference,
                split_event,
                '{"escrow_status_at_commit":"split","worker_pct":0}',
            ),
        )
        connection.executemany(
            "INSERT INTO bank_operation_legs "
            "(operation_id, leg_index, role, account_id, direction, amount, "
            "transaction_id, balance_after) VALUES (?, ?, ?, ?, 'credit', ?, ?, ?)",
            (
                (operation_id, 0, "worker", "a-worker-without-account", 0, None, None),
                (operation_id, 1, "poster", "a-poster", 100, "tx-poster-release", 100),
            ),
        )
        result = _audit(connection)
        worker_accounts = connection.execute(
            "SELECT account_id FROM bank_accounts WHERE account_id = 'a-worker-without-account'"
        ).fetchall()
    finally:
        connection.close()

    assert worker_accounts == []
    assert result.ownership_clean is True
    assert result.findings == ()


def test_native_split_result_rejects_float_worker_percentage() -> None:
    from db_gateway_service.financial_requests import canonical_request

    connection = _v2_connection()
    operation_id = f"op-{uuid4()}"
    try:
        _add_account(connection, "a-poster", 50)
        _add_account(connection, "a-worker", 50)
        credit_event = _add_event(connection, "account.credited", "a-poster")
        lock_event = _add_event(connection, "escrow.locked", "a-poster")
        split_event = _add_event(connection, "escrow.split", "a-poster")
        _add_credit(
            connection,
            tx_id="tx-credit",
            account_id="a-poster",
            amount=100,
            event_id=credit_event,
        )
        connection.execute(
            "INSERT INTO bank_transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
            "VALUES ('tx-lock', 'a-poster', 'escrow_lock', 100, 0, "
            "'t-strict-split', 'now', ?)",
            (lock_event,),
        )
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at, "
            "resolved_at, event_id) VALUES "
            "('esc-strict-split', 'a-poster', 100, 't-strict-split', 'split', "
            "'now', 'now', ?)",
            (lock_event,),
        )
        for tx_id, account_id in (
            ("tx-worker-release", "a-worker"),
            ("tx-poster-release", "a-poster"),
        ):
            connection.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, "
                "timestamp, event_id) VALUES (?, ?, 'escrow_release', 50, 50, "
                "'esc-strict-split', 'now', ?)",
                (tx_id, account_id, split_event),
            )
        transaction_markers = [
            {
                "tx_id": "tx-credit",
                "observed_kind": "observed_credit",
                "observed_fact_sha256": _hash_json(
                    _transaction_fact(
                        tx_id="tx-credit",
                        account_id="a-poster",
                        amount=100,
                        event_id=credit_event,
                    )
                ),
            },
            {
                "tx_id": "tx-lock",
                "observed_kind": "observed_escrow_lock",
                "observed_fact_sha256": _hash_json(
                    {
                        "account_id": "a-poster",
                        "amount": 100,
                        "balance_after": 0,
                        "event_id": lock_event,
                        "reference": "t-strict-split",
                        "timestamp": "now",
                        "tx_id": "tx-lock",
                        "type": "escrow_lock",
                    }
                ),
            },
        ]
        escrow_marker = {
            "escrow_id": "esc-strict-split",
            "observed_status": "locked",
            "lock_fact_sha256": _hash_json(
                {
                    "amount": 100,
                    "created_at": "now",
                    "escrow_id": "esc-strict-split",
                    "event_id": lock_event,
                    "payer_account_id": "a-poster",
                    "task_id": "t-strict-split",
                }
            ),
            "resolution_fact_sha256": None,
        }
        _insert_migrated_cutover(connection, transaction_markers, [escrow_marker])
        for marker in transaction_markers:
            connection.execute(
                "INSERT INTO bank_legacy_transaction_observations VALUES (?, 1, ?, ?)",
                (
                    marker["tx_id"],
                    marker["observed_kind"],
                    marker["observed_fact_sha256"],
                ),
            )
        connection.execute(
            "INSERT INTO bank_legacy_escrow_observations VALUES (?, 1, 'locked', ?, NULL)",
            ("esc-strict-split", escrow_marker["lock_fact_sha256"]),
        )
        request = canonical_request(
            "escrow_split",
            {
                "escrow_id": "esc-strict-split",
                "worker_account_id": "a-worker",
                "poster_account_id": "a-poster",
                "worker_pct": 50,
                "source_reference": "task:t-strict-split",
            },
        )
        connection.execute(
            "INSERT INTO bank_operation_receipts "
            "(operation_id, receipt_version, operation_kind, canonical_request_json, "
            "request_sha256, source_reference, committed_at, event_id, escrow_id, result_json) "
            "VALUES (?, 1, 'escrow_split', ?, ?, ?, 'now', ?, 'esc-strict-split', ?)",
            (
                operation_id,
                request.canonical_json,
                request.request_sha256,
                request.source_reference,
                split_event,
                '{"escrow_status_at_commit":"split","worker_pct":50}',
            ),
        )
        connection.executemany(
            "INSERT INTO bank_operation_legs "
            "(operation_id, leg_index, role, account_id, direction, amount, "
            "transaction_id, balance_after) VALUES (?, ?, ?, ?, 'credit', 50, ?, 50)",
            (
                (operation_id, 0, "worker", "a-worker", "tx-worker-release"),
                (operation_id, 1, "poster", "a-poster", "tx-poster-release"),
            ),
        )
        clean_result = _audit(connection)
        connection.execute(
            "UPDATE bank_operation_receipts SET result_json = ? WHERE operation_id = ?",
            ('{"escrow_status_at_commit":"split","worker_pct":50.0}', operation_id),
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert clean_result.ownership_clean is True
    assert clean_result.findings == ()
    assert result.financially_clean is True
    assert "receipt_result_mismatch" in _finding_codes(result)


def test_schema_rejects_positive_leg_without_transaction_and_zero_leg_with_transaction() -> None:
    from db_gateway_service.financial_requests import canonical_request

    connection = _v2_connection()
    operation_id = f"op-{uuid4()}"
    try:
        _add_account(connection, "a-one", 1)
        event_id = _add_event(connection, "account.credited", "a-one")
        _add_credit(
            connection,
            tx_id="tx-one",
            account_id="a-one",
            amount=1,
            event_id=event_id,
        )
        request = canonical_request(
            "credit",
            {"account_id": "a-one", "amount": 1, "reference": "seed"},
        )
        connection.execute(
            "INSERT INTO bank_operation_receipts "
            "(operation_id, receipt_version, operation_kind, canonical_request_json, "
            "request_sha256, source_reference, committed_at, event_id, result_json) "
            "VALUES (?, 1, 'credit', ?, ?, 'seed', 'now', ?, '{}')",
            (operation_id, request.canonical_json, request.request_sha256, event_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bank_operation_legs VALUES "
                "(?, 0, 'account', 'a-one', 'credit', 1, NULL, NULL)",
                (operation_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bank_operation_legs VALUES "
                "(?, 0, 'account', 'a-one', 'credit', 0, 'tx-one', 1)",
                (operation_id,),
            )
    finally:
        connection.close()


def test_schema_accepts_nonempty_embedded_nul_business_text() -> None:
    from db_gateway_service.financial_requests import canonical_request

    connection = _v2_connection()
    operation_id = f"op-{uuid4()}"
    try:
        _add_account(connection, "a-one", 0)
        event_id = _add_event(connection, "account.credited", "a-one")
        request = canonical_request(
            "credit",
            {"account_id": "a-one", "amount": 1, "reference": "\x00seed"},
        )
        connection.execute(
            "INSERT INTO bank_operation_receipts "
            "(operation_id, receipt_version, operation_kind, canonical_request_json, "
            "request_sha256, source_reference, committed_at, event_id, result_json) "
            "VALUES (?, 1, 'credit', ?, ?, ?, 'now', ?, '{}')",
            (
                operation_id,
                request.canonical_json,
                request.request_sha256,
                request.source_reference,
                event_id,
            ),
        )
        connection.execute(
            "INSERT INTO bank_operation_legs "
            "(operation_id, leg_index, role, account_id, direction, amount, "
            "transaction_id, balance_after) "
            "VALUES (?, 0, 'account', ?, 'credit', 0, NULL, NULL)",
            (operation_id, "\x00recipient"),
        )
    finally:
        connection.close()


def test_schema_rejects_null_text_primary_keys_and_nullable_check_loopholes() -> None:
    connection = _v2_connection()
    try:
        _insert_fresh_cutover(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bank_legacy_transaction_observations "
                "(tx_id, cutover_singleton, observed_kind, observed_fact_sha256) "
                "VALUES (NULL, 1, 'observed_credit', ?)",
                ("a" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bank_legacy_escrow_observations "
                "(escrow_id, cutover_singleton, observed_status, lock_fact_sha256, "
                "resolution_fact_sha256) VALUES (NULL, 1, 'released', ?, NULL)",
                ("a" * 64,),
            )
    finally:
        connection.close()
