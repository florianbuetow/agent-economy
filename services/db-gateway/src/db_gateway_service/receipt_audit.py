"""Read-only audit for MP-04 financial receipt and legacy-marker ownership."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from db_gateway_service.financial_requests import parse_canonical_request, validate_operation_id
from db_gateway_service.money_audit import audit_money

if TYPE_CHECKING:
    import sqlite3

_REQUIRED_TABLES = (
    "bank_operation_receipts",
    "bank_operation_legs",
    "bank_legacy_transaction_observations",
    "bank_legacy_escrow_observations",
    "bank_receipt_cutover",
)


@dataclass(frozen=True, order=True, slots=True)
class ReceiptAuditFinding:
    """Stable machine-readable receipt-ownership finding."""

    code: str
    table: str
    record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReceiptOwnershipAuditResult:
    """Receipt ownership status; this object never authorizes startup or migration."""

    ownership_clean: bool
    financially_clean: bool
    findings: tuple[ReceiptAuditFinding, ...]
    transaction_count: int
    escrow_count: int
    native_receipt_count: int
    legacy_transaction_count: int
    legacy_escrow_count: int


@dataclass(frozen=True, slots=True)
class _Transaction:
    tx_id: str
    account_id: str
    transaction_type: str
    amount: object
    balance_after: object
    reference: str
    timestamp: str
    event_id: object


@dataclass(frozen=True, slots=True)
class _Escrow:
    escrow_id: str
    payer_account_id: str
    amount: object
    task_id: str
    status: str
    created_at: str
    resolved_at: object
    event_id: object


def audit_receipt_ownership(connection: sqlite3.Connection) -> ReceiptOwnershipAuditResult:
    """Verify money plus total, disjoint native/legacy ownership without mutation."""
    findings: list[ReceiptAuditFinding] = []
    money_result = audit_money(connection)
    if not money_result.financially_clean:
        findings.append(ReceiptAuditFinding("money_audit_failed", "bank_transactions", ()))

    existing_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = [table for table in _REQUIRED_TABLES if table not in existing_tables]
    findings.extend(ReceiptAuditFinding("missing_receipt_table", table, ()) for table in missing)
    transactions = _load_transactions(connection)
    escrows = _load_escrows(connection)
    if missing:
        return _result(
            findings,
            financially_clean=money_result.financially_clean,
            transaction_count=len(transactions),
            escrow_count=len(escrows),
            native_receipt_count=0,
            legacy_transaction_count=0,
            legacy_escrow_count=0,
        )

    transaction_owners = dict.fromkeys(transactions, 0)
    lock_owners = dict.fromkeys(escrows, 0)
    resolution_owners = dict.fromkeys(escrows, 0)
    legacy_transaction_markers = _audit_legacy_transactions(
        connection,
        transactions,
        transaction_owners,
        findings,
    )
    legacy_escrow_markers = _audit_legacy_escrows(
        connection,
        transactions,
        escrows,
        lock_owners,
        resolution_owners,
        findings,
    )
    native_receipt_count = _audit_native_receipts(
        connection,
        transactions,
        escrows,
        transaction_owners,
        lock_owners,
        resolution_owners,
        findings,
    )
    _audit_orphan_legs(connection, findings)
    _audit_cutover(
        connection,
        legacy_transaction_markers,
        legacy_escrow_markers,
        findings,
    )
    _audit_total_ownership(
        transactions,
        escrows,
        transaction_owners,
        lock_owners,
        resolution_owners,
        findings,
    )
    return _result(
        findings,
        financially_clean=money_result.financially_clean,
        transaction_count=len(transactions),
        escrow_count=len(escrows),
        native_receipt_count=native_receipt_count,
        legacy_transaction_count=len(legacy_transaction_markers),
        legacy_escrow_count=len(legacy_escrow_markers),
    )


def _load_transactions(connection: sqlite3.Connection) -> dict[str, _Transaction]:
    rows = connection.execute(
        "SELECT tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id "
        "FROM bank_transactions ORDER BY tx_id"
    ).fetchall()
    return {
        str(row[0]): _Transaction(
            str(row[0]),
            str(row[1]),
            str(row[2]),
            row[3],
            row[4],
            str(row[5]),
            str(row[6]),
            row[7],
        )
        for row in rows
    }


def _load_escrows(connection: sqlite3.Connection) -> dict[str, _Escrow]:
    rows = connection.execute(
        "SELECT escrow_id, payer_account_id, amount, task_id, status, created_at, "
        "resolved_at, event_id FROM bank_escrow ORDER BY escrow_id"
    ).fetchall()
    return {
        str(row[0]): _Escrow(
            str(row[0]),
            str(row[1]),
            row[2],
            str(row[3]),
            str(row[4]),
            str(row[5]),
            row[6],
            row[7],
        )
        for row in rows
    }


def _audit_legacy_transactions(
    connection: sqlite3.Connection,
    transactions: dict[str, _Transaction],
    transaction_owners: dict[str, int],
    findings: list[ReceiptAuditFinding],
) -> tuple[dict[str, object], ...]:
    markers: list[dict[str, object]] = []
    rows = connection.execute(
        "SELECT tx_id, observed_kind, observed_fact_sha256 "
        "FROM bank_legacy_transaction_observations ORDER BY tx_id"
    ).fetchall()
    expected_kinds = {
        "credit": "observed_credit",
        "escrow_lock": "observed_escrow_lock",
        "escrow_release": "observed_escrow_release",
    }
    for row in rows:
        tx_id, observed_kind, stored_hash = str(row[0]), str(row[1]), str(row[2])
        marker: dict[str, object] = {
            "tx_id": tx_id,
            "observed_kind": observed_kind,
            "observed_fact_sha256": stored_hash,
        }
        markers.append(marker)
        transaction = transactions.get(tx_id)
        if transaction is None:
            findings.append(
                ReceiptAuditFinding(
                    "orphan_legacy_transaction", "bank_legacy_transaction_observations", (tx_id,)
                )
            )
            continue
        transaction_owners[tx_id] += 1
        if expected_kinds.get(transaction.transaction_type) != observed_kind:
            findings.append(
                ReceiptAuditFinding(
                    "legacy_transaction_kind_mismatch",
                    "bank_legacy_transaction_observations",
                    (tx_id,),
                )
            )
        if stored_hash != _hash_json(_transaction_fact(transaction)):
            findings.append(
                ReceiptAuditFinding(
                    "legacy_transaction_fact_mismatch",
                    "bank_legacy_transaction_observations",
                    (tx_id,),
                )
            )
    return tuple(markers)


def _audit_legacy_escrows(
    connection: sqlite3.Connection,
    transactions: dict[str, _Transaction],
    escrows: dict[str, _Escrow],
    lock_owners: dict[str, int],
    resolution_owners: dict[str, int],
    findings: list[ReceiptAuditFinding],
) -> tuple[dict[str, object], ...]:
    markers: list[dict[str, object]] = []
    rows = connection.execute(
        "SELECT escrow_id, observed_status, lock_fact_sha256, resolution_fact_sha256 "
        "FROM bank_legacy_escrow_observations ORDER BY escrow_id"
    ).fetchall()
    for row in rows:
        escrow_id = str(row[0])
        observed_status = str(row[1])
        lock_hash = str(row[2])
        resolution_hash = None if row[3] is None else str(row[3])
        marker: dict[str, object] = {
            "escrow_id": escrow_id,
            "observed_status": observed_status,
            "lock_fact_sha256": lock_hash,
            "resolution_fact_sha256": resolution_hash,
        }
        markers.append(marker)
        escrow = escrows.get(escrow_id)
        if escrow is None:
            findings.append(
                ReceiptAuditFinding(
                    "orphan_legacy_escrow", "bank_legacy_escrow_observations", (escrow_id,)
                )
            )
            continue
        lock_owners[escrow_id] += 1
        if observed_status not in {"locked", escrow.status}:
            findings.append(
                ReceiptAuditFinding(
                    "legacy_escrow_status_mismatch", "bank_legacy_escrow_observations", (escrow_id,)
                )
            )
        if lock_hash != _hash_json(_lock_fact(escrow)):
            findings.append(
                ReceiptAuditFinding(
                    "legacy_escrow_lock_fact_mismatch",
                    "bank_legacy_escrow_observations",
                    (escrow_id,),
                )
            )
        if observed_status in {"released", "split"}:
            resolution_owners[escrow_id] += 1
            expected_resolution = _hash_json(_resolution_fact(escrow, transactions))
            if resolution_hash != expected_resolution:
                findings.append(
                    ReceiptAuditFinding(
                        "legacy_escrow_resolution_fact_mismatch",
                        "bank_legacy_escrow_observations",
                        (escrow_id,),
                    )
                )
    return tuple(markers)


def _audit_native_receipts(
    connection: sqlite3.Connection,
    transactions: dict[str, _Transaction],
    escrows: dict[str, _Escrow],
    transaction_owners: dict[str, int],
    lock_owners: dict[str, int],
    resolution_owners: dict[str, int],
    findings: list[ReceiptAuditFinding],
) -> int:
    rows = connection.execute(
        "SELECT operation_id, receipt_version, operation_kind, canonical_request_json, "
        "request_sha256, source_reference, committed_at, event_id, escrow_id, result_json "
        "FROM bank_operation_receipts ORDER BY operation_id"
    ).fetchall()
    for row in rows:
        _audit_native_receipt(
            connection,
            row,
            transactions,
            escrows,
            transaction_owners,
            lock_owners,
            resolution_owners,
            findings,
        )
    return len(rows)


def _audit_native_receipt(
    connection: sqlite3.Connection,
    row: tuple[object, ...],
    transactions: dict[str, _Transaction],
    escrows: dict[str, _Escrow],
    transaction_owners: dict[str, int],
    lock_owners: dict[str, int],
    resolution_owners: dict[str, int],
    findings: list[ReceiptAuditFinding],
) -> None:
    operation_id = str(row[0])
    operation_kind = str(row[2])
    try:
        validate_operation_id(row[0])
    except ValueError:
        findings.append(
            ReceiptAuditFinding("invalid_operation_id", "bank_operation_receipts", (operation_id,))
        )
    if row[1] != 1:
        findings.append(
            ReceiptAuditFinding(
                "invalid_receipt_version", "bank_operation_receipts", (operation_id,)
            )
        )
    request = None
    try:
        request = parse_canonical_request(str(row[3]))
    except (TypeError, ValueError, json.JSONDecodeError):
        findings.append(
            ReceiptAuditFinding(
                "invalid_canonical_request", "bank_operation_receipts", (operation_id,)
            )
        )
    if request is not None and (
        request.operation_kind != operation_kind
        or request.request_sha256 != row[4]
        or request.source_reference != row[5]
    ):
        findings.append(
            ReceiptAuditFinding(
                "receipt_request_mismatch", "bank_operation_receipts", (operation_id,)
            )
        )
    result = _parse_result_json(row[9])
    if result is None:
        findings.append(
            ReceiptAuditFinding(
                "invalid_receipt_result", "bank_operation_receipts", (operation_id,)
            )
        )
        result = {}
    escrow_id = None if row[8] is None else str(row[8])
    event_exists = connection.execute(
        "SELECT 1 FROM events WHERE event_id = ?",
        (row[7],),
    ).fetchone()
    if event_exists is None:
        findings.append(
            ReceiptAuditFinding(
                "receipt_event_missing",
                "bank_operation_receipts",
                (operation_id,),
            )
        )
    legs = connection.execute(
        "SELECT leg_index, role, account_id, direction, amount, transaction_id, balance_after "
        "FROM bank_operation_legs WHERE operation_id = ? ORDER BY leg_index",
        (operation_id,),
    ).fetchall()
    _audit_receipt_effect(
        operation_id=operation_id,
        operation_kind=operation_kind,
        request=request,
        result=result,
        committed_at=str(row[6]),
        event_id=row[7],
        escrow_id=escrow_id,
        legs=legs,
        transactions=transactions,
        escrows=escrows,
        transaction_owners=transaction_owners,
        lock_owners=lock_owners,
        resolution_owners=resolution_owners,
        findings=findings,
    )


def _audit_receipt_effect(
    *,
    operation_id: str,
    operation_kind: str,
    request: object,
    result: dict[str, object],
    committed_at: str,
    event_id: object,
    escrow_id: str | None,
    legs: list[tuple[object, ...]],
    transactions: dict[str, _Transaction],
    escrows: dict[str, _Escrow],
    transaction_owners: dict[str, int],
    lock_owners: dict[str, int],
    resolution_owners: dict[str, int],
    findings: list[ReceiptAuditFinding],
) -> None:
    from db_gateway_service.financial_requests import FinancialRequest

    if not isinstance(request, FinancialRequest):
        return
    fields = json.loads(request.canonical_json)
    expected = _expected_effect(
        operation_kind,
        fields,
        escrow_id,
        escrows,
        legs,
        transactions,
    )
    if expected is None:
        findings.append(
            ReceiptAuditFinding(
                "receipt_effect_unverifiable", "bank_operation_receipts", (operation_id,)
            )
        )
        return
    expected_result, expected_legs = expected
    if not _strict_result_matches(result, expected_result):
        findings.append(
            ReceiptAuditFinding(
                "receipt_result_mismatch", "bank_operation_receipts", (operation_id,)
            )
        )
    if len(legs) != len(expected_legs):
        findings.append(
            ReceiptAuditFinding("receipt_leg_cardinality", "bank_operation_legs", (operation_id,))
        )
    for index, expected_leg in enumerate(expected_legs):
        if index >= len(legs):
            continue
        _audit_leg(
            operation_id,
            index,
            legs[index],
            expected_leg,
            committed_at,
            event_id,
            transactions,
            transaction_owners,
            findings,
        )
    if operation_kind == "escrow_lock" and escrow_id in lock_owners:
        lock_owners[escrow_id] += 1
    if operation_kind in {"escrow_release", "escrow_split"} and escrow_id in resolution_owners:
        resolution_owners[escrow_id] += 1
    if escrow_id is not None and escrow_id in escrows:
        escrow = escrows[escrow_id]
        escrow_effect_mismatch = (
            operation_kind == "escrow_lock"
            and (escrow.created_at != committed_at or escrow.event_id != event_id)
        ) or (
            operation_kind in {"escrow_release", "escrow_split"}
            and escrow.resolved_at != committed_at
        )
        if escrow_effect_mismatch:
            findings.append(
                ReceiptAuditFinding(
                    "receipt_escrow_effect_mismatch",
                    "bank_operation_receipts",
                    (operation_id, escrow_id),
                )
            )


def _parse_result_json(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, str):
        return None

    def object_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON result key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        raise ValueError("invalid JSON numeric constant")

    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=object_hook,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _strict_result_matches(actual: dict[str, object], expected: dict[str, object]) -> bool:
    if set(actual) != set(expected):
        return False
    return all(
        type(actual[key]) is type(expected_value) and actual[key] == expected_value
        for key, expected_value in expected.items()
    )


def _expected_effect(
    operation_kind: str,
    fields: dict[str, object],
    escrow_id: str | None,
    escrows: dict[str, _Escrow],
    legs: list[tuple[object, ...]],
    transactions: dict[str, _Transaction],
) -> tuple[dict[str, object], tuple[tuple[str, str, str, int, str, str], ...]] | None:
    expected: tuple[dict[str, object], tuple[tuple[str, str, str, int, str, str], ...]] | None = (
        None
    )
    if operation_kind in {"account_issuance", "credit"}:
        account_id = str(fields["account_id"])
        amount = _canonical_int(fields["amount"])
        reference = str(fields["reference"])
        transaction = transactions.get(str(legs[0][5])) if legs and legs[0][5] is not None else None
        balance_after = None if transaction is None else _stored_int(transaction.balance_after)
        expected_result: dict[str, object] = (
            {"account_id": account_id, "issued_amount": amount, "balance_after": balance_after}
            if operation_kind == "account_issuance"
            else {
                "account_id": account_id,
                "amount": amount,
                "balance_after": balance_after,
                "reference": reference,
            }
        )
        expected = (
            expected_result,
            (("account", account_id, "credit", amount, "credit", reference),),
        )
    elif escrow_id is not None and escrow_id in escrows:
        escrow = escrows[escrow_id]
        escrow_amount = _stored_int(escrow.amount)
        request_escrow_matches = fields.get("escrow_id") == escrow_id
        lock_mismatch = (
            (
                escrow.payer_account_id != fields["payer_account_id"]
                or escrow.task_id != fields["task_id"]
                or escrow.amount != fields["amount"]
            )
            if operation_kind == "escrow_lock"
            else False
        )
        if operation_kind == "escrow_lock" and not lock_mismatch:
            expected = (
                {"escrow_status_at_commit": "locked"},
                (
                    (
                        "payer",
                        str(fields["payer_account_id"]),
                        "debit",
                        _canonical_int(fields["amount"]),
                        "escrow_lock",
                        str(fields["task_id"]),
                    ),
                ),
            )
        elif (
            operation_kind == "escrow_release"
            and request_escrow_matches
            and escrow.status == "released"
            and escrow_amount is not None
        ):
            expected = (
                {"escrow_status_at_commit": "released"},
                (
                    (
                        "recipient",
                        str(fields["recipient_account_id"]),
                        "credit",
                        escrow_amount,
                        "escrow_release",
                        escrow_id,
                    ),
                ),
            )
        elif (
            operation_kind == "escrow_split"
            and request_escrow_matches
            and escrow.status == "split"
            and escrow.payer_account_id == fields["poster_account_id"]
            and escrow_amount is not None
        ):
            worker_pct = _canonical_int(fields["worker_pct"])
            worker_amount = escrow_amount * worker_pct // 100
            expected = (
                {"escrow_status_at_commit": "split", "worker_pct": worker_pct},
                (
                    (
                        "worker",
                        str(fields["worker_account_id"]),
                        "credit",
                        worker_amount,
                        "escrow_release",
                        escrow_id,
                    ),
                    (
                        "poster",
                        str(fields["poster_account_id"]),
                        "credit",
                        escrow_amount - worker_amount,
                        "escrow_release",
                        escrow_id,
                    ),
                ),
            )
    return expected


def _canonical_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("validated canonical integer is invalid")
    return value


def _stored_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _audit_leg(
    operation_id: str,
    expected_index: int,
    row: tuple[object, ...],
    expected: tuple[str, str, str, int, str, str],
    committed_at: str,
    event_id: object,
    transactions: dict[str, _Transaction],
    transaction_owners: dict[str, int],
    findings: list[ReceiptAuditFinding],
) -> None:
    role, account_id, direction, amount, transaction_type, reference = expected
    if row[0] != expected_index or (row[1], row[2], row[3], row[4]) != (
        role,
        account_id,
        direction,
        amount,
    ):
        findings.append(
            ReceiptAuditFinding("receipt_leg_mismatch", "bank_operation_legs", (operation_id, role))
        )
    if amount == 0:
        if row[5] is not None or row[6] is not None or role not in {"worker", "poster"}:
            findings.append(
                ReceiptAuditFinding(
                    "invalid_zero_receipt_leg", "bank_operation_legs", (operation_id, role)
                )
            )
        return
    tx_id = None if row[5] is None else str(row[5])
    transaction = transactions.get(tx_id or "")
    if transaction is None:
        findings.append(
            ReceiptAuditFinding(
                "receipt_transaction_missing", "bank_operation_legs", (operation_id, role)
            )
        )
        return
    transaction_owners[transaction.tx_id] += 1
    if (
        transaction.account_id,
        transaction.transaction_type,
        transaction.amount,
        transaction.balance_after,
        transaction.reference,
        transaction.timestamp,
        transaction.event_id,
    ) != (account_id, transaction_type, amount, row[6], reference, committed_at, event_id):
        findings.append(
            ReceiptAuditFinding(
                "receipt_transaction_mismatch",
                "bank_operation_legs",
                (operation_id, role, transaction.tx_id),
            )
        )


def _audit_cutover(
    connection: sqlite3.Connection,
    transaction_markers: tuple[dict[str, object], ...],
    escrow_markers: tuple[dict[str, object], ...],
    findings: list[ReceiptAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT singleton, attestation_version, legacy_transaction_count, legacy_escrow_count, "
        "legacy_transaction_set_sha256, legacy_escrow_set_sha256, marker_set_sha256 "
        "FROM bank_receipt_cutover ORDER BY singleton"
    ).fetchall()
    if len(rows) != 1:
        findings.append(
            ReceiptAuditFinding(
                "invalid_cutover_row_count", "bank_receipt_cutover", (str(len(rows)),)
            )
        )
        return
    row = rows[0]
    transaction_set_hash = _hash_json(list(transaction_markers))
    escrow_set_hash = _hash_json(list(escrow_markers))
    combined = {
        "attestation_version": 1,
        "legacy_escrow_count": len(escrow_markers),
        "legacy_escrow_set_sha256": escrow_set_hash,
        "legacy_transaction_count": len(transaction_markers),
        "legacy_transaction_set_sha256": transaction_set_hash,
    }
    expected = (
        1,
        1,
        len(transaction_markers),
        len(escrow_markers),
        transaction_set_hash,
        escrow_set_hash,
        _hash_json(combined),
    )
    if tuple(row) != expected:
        findings.append(
            ReceiptAuditFinding("cutover_attestation_mismatch", "bank_receipt_cutover", ("1",))
        )


def _audit_orphan_legs(
    connection: sqlite3.Connection,
    findings: list[ReceiptAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT legs.operation_id, legs.leg_index FROM bank_operation_legs AS legs "
        "LEFT JOIN bank_operation_receipts AS receipts "
        "ON receipts.operation_id = legs.operation_id "
        "WHERE receipts.operation_id IS NULL ORDER BY legs.operation_id, legs.leg_index"
    ).fetchall()
    for row in rows:
        findings.append(
            ReceiptAuditFinding(
                "orphan_receipt_leg",
                "bank_operation_legs",
                (str(row[0]), str(row[1])),
            )
        )


def _audit_total_ownership(
    transactions: dict[str, _Transaction],
    escrows: dict[str, _Escrow],
    transaction_owners: dict[str, int],
    lock_owners: dict[str, int],
    resolution_owners: dict[str, int],
    findings: list[ReceiptAuditFinding],
) -> None:
    for tx_id in sorted(transactions):
        owner_count = transaction_owners[tx_id]
        if owner_count != 1:
            code = "unowned_transaction" if owner_count == 0 else "multiple_transaction_owners"
            findings.append(ReceiptAuditFinding(code, "bank_transactions", (tx_id,)))
    for escrow_id, escrow in sorted(escrows.items()):
        if lock_owners[escrow_id] != 1:
            code = (
                "unowned_escrow_lock"
                if lock_owners[escrow_id] == 0
                else "multiple_escrow_lock_owners"
            )
            findings.append(ReceiptAuditFinding(code, "bank_escrow", (escrow_id,)))
        expected_resolution_owners = 0 if escrow.status == "locked" else 1
        if resolution_owners[escrow_id] != expected_resolution_owners:
            code = (
                "unexpected_escrow_resolution_owner"
                if expected_resolution_owners == 0
                else "invalid_escrow_resolution_ownership"
            )
            findings.append(ReceiptAuditFinding(code, "bank_escrow", (escrow_id,)))


def _transaction_fact(transaction: _Transaction) -> dict[str, object]:
    return {
        "account_id": transaction.account_id,
        "amount": transaction.amount,
        "balance_after": transaction.balance_after,
        "event_id": transaction.event_id,
        "reference": transaction.reference,
        "timestamp": transaction.timestamp,
        "tx_id": transaction.tx_id,
        "type": transaction.transaction_type,
    }


def _lock_fact(escrow: _Escrow) -> dict[str, object]:
    return {
        "amount": escrow.amount,
        "created_at": escrow.created_at,
        "escrow_id": escrow.escrow_id,
        "event_id": escrow.event_id,
        "payer_account_id": escrow.payer_account_id,
        "task_id": escrow.task_id,
    }


def _resolution_fact(
    escrow: _Escrow,
    transactions: dict[str, _Transaction],
) -> dict[str, object]:
    release_ids = sorted(
        transaction.tx_id
        for transaction in transactions.values()
        if transaction.transaction_type == "escrow_release"
        and transaction.reference == escrow.escrow_id
    )
    return {
        "escrow_id": escrow.escrow_id,
        "observed_status": escrow.status,
        "resolved_at": escrow.resolved_at,
        "transaction_ids": release_ids,
    }


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _result(
    findings: list[ReceiptAuditFinding],
    *,
    financially_clean: bool,
    transaction_count: int,
    escrow_count: int,
    native_receipt_count: int,
    legacy_transaction_count: int,
    legacy_escrow_count: int,
) -> ReceiptOwnershipAuditResult:
    stable_findings = tuple(sorted(set(findings)))
    return ReceiptOwnershipAuditResult(
        ownership_clean=not stable_findings,
        financially_clean=financially_clean,
        findings=stable_findings,
        transaction_count=transaction_count,
        escrow_count=escrow_count,
        native_receipt_count=native_receipt_count,
        legacy_transaction_count=legacy_transaction_count,
        legacy_escrow_count=legacy_escrow_count,
    )


__all__ = [
    "ReceiptAuditFinding",
    "ReceiptOwnershipAuditResult",
    "audit_receipt_ownership",
]
