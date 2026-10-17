"""Persistence helpers for immutable native financial receipts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    import sqlite3

    from db_gateway_service.financial_requests import FinancialRequest


def _json_object(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class FinancialRecords:
    """Read and write receipt rows through a caller-owned transaction cursor."""

    def replay(
        self,
        cursor: sqlite3.Cursor,
        operation_id: str,
        request: FinancialRequest,
    ) -> dict[str, Any] | None:
        row = cursor.execute(
            "SELECT operation_kind, request_sha256 FROM bank_operation_receipts "
            "WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        if row[0] != request.operation_kind or row[1] != request.request_sha256:
            raise ServiceError(
                "operation_id_conflict",
                "Operation identity already belongs to a different financial command",
                409,
                {"winning_operation_id": operation_id},
            )
        return self.envelope(cursor, operation_id)

    def credit_winner(
        self,
        cursor: sqlite3.Cursor,
        account_id: str,
        reference: str,
    ) -> str | None:
        row = cursor.execute(
            "SELECT r.operation_id FROM bank_operation_receipts AS r "
            "JOIN bank_operation_legs AS l ON l.operation_id = r.operation_id "
            "JOIN bank_transactions AS t ON t.tx_id = l.transaction_id "
            "WHERE r.operation_kind IN ('account_issuance', 'credit') "
            "AND t.account_id = ? AND t.reference = ? AND t.type = 'credit'",
            (account_id, reference),
        ).fetchone()
        return None if row is None else str(row[0])

    def lock_winner(
        self,
        cursor: sqlite3.Cursor,
        payer_account_id: str,
        task_id: str,
    ) -> str | None:
        row = cursor.execute(
            "SELECT r.operation_id FROM bank_operation_receipts AS r "
            "JOIN bank_escrow AS e ON e.escrow_id = r.escrow_id "
            "WHERE r.operation_kind = 'escrow_lock' "
            "AND e.payer_account_id = ? AND e.task_id = ?",
            (payer_account_id, task_id),
        ).fetchone()
        return None if row is None else str(row[0])

    def settlement_winner(self, cursor: sqlite3.Cursor, escrow_id: str) -> str | None:
        row = cursor.execute(
            "SELECT operation_id FROM bank_operation_receipts "
            "WHERE escrow_id = ? AND operation_kind IN ('escrow_release', 'escrow_split')",
            (escrow_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def persist(
        self,
        cursor: sqlite3.Cursor,
        operation_id: str,
        request: FinancialRequest,
        committed_at: str,
        event_id: int,
        escrow_id: str | None,
        result: dict[str, Any],
        legs: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        cursor.execute(
            "INSERT INTO bank_operation_receipts "
            "(operation_id, receipt_version, operation_kind, canonical_request_json, "
            "request_sha256, source_reference, committed_at, event_id, escrow_id, result_json) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation_id,
                request.operation_kind,
                request.canonical_json,
                request.request_sha256,
                request.source_reference,
                committed_at,
                event_id,
                escrow_id,
                _json_object(result),
            ),
        )
        for index, leg in enumerate(legs):
            cursor.execute(
                "INSERT INTO bank_operation_legs "
                "(operation_id, leg_index, role, account_id, direction, amount, "
                "transaction_id, balance_after) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    index,
                    leg["role"],
                    leg["account_id"],
                    leg["direction"],
                    leg["amount"],
                    leg["transaction_id"],
                    leg["balance_after"],
                ),
            )
        return self.envelope(cursor, operation_id)

    def envelope(self, cursor: sqlite3.Cursor, operation_id: str) -> dict[str, Any]:
        row = cursor.execute(
            "SELECT receipt_version, operation_id, operation_kind, request_sha256, "
            "source_reference, committed_at, event_id, escrow_id, result_json "
            "FROM bank_operation_receipts WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise ServiceError(
                "receipt_ownership_missing",
                "Native financial effect has no receipt",
                500,
                {"operation_id": operation_id},
            )
        leg_rows = cursor.execute(
            "SELECT role, account_id, direction, amount, transaction_id, balance_after "
            "FROM bank_operation_legs WHERE operation_id = ? ORDER BY leg_index",
            (operation_id,),
        ).fetchall()
        escrow_id = None if row[7] is None else str(row[7])
        receipt = {
            "receipt_version": int(row[0]),
            "operation_id": str(row[1]),
            "operation_kind": str(row[2]),
            "request_sha256": str(row[3]),
            "source_reference": str(row[4]),
            "committed_at": str(row[5]),
            "event_id": int(row[6]),
            "escrow_id": escrow_id,
            "result": json.loads(str(row[8])),
            "legs": [
                {
                    "role": str(leg[0]),
                    "account_id": str(leg[1]),
                    "direction": str(leg[2]),
                    "amount": int(leg[3]),
                    "transaction_id": None if leg[4] is None else str(leg[4]),
                    "balance_after": None if leg[5] is None else int(leg[5]),
                }
                for leg in leg_rows
            ],
        }
        return {
            "outcome": "native_receipt",
            "receipt": receipt,
            "current_escrow": self.current_escrow(cursor, escrow_id),
        }

    def current_escrow(
        self,
        cursor: sqlite3.Cursor,
        escrow_id: str | None,
    ) -> dict[str, Any] | None:
        if escrow_id is None:
            return None
        row = cursor.execute(
            "SELECT escrow_id, payer_account_id, task_id, amount, status, created_at, resolved_at "
            "FROM bank_escrow WHERE escrow_id = ?",
            (escrow_id,),
        ).fetchone()
        if row is None:
            raise ServiceError(
                "receipt_projection_missing",
                "Receipt escrow projection is missing",
                500,
                {"escrow_id": escrow_id},
            )
        return {
            "escrow_id": str(row[0]),
            "payer_account_id": str(row[1]),
            "task_id": str(row[2]),
            "amount": int(row[3]),
            "status": str(row[4]),
            "created_at": str(row[5]),
            "resolved_at": None if row[6] is None else str(row[6]),
        }


def ownership_conflict(winning_operation_id: str) -> ServiceError:
    return ServiceError(
        "financial_operation_already_committed",
        "Financial effect already belongs to another operation identity",
        409,
        {"winning_operation_id": winning_operation_id},
    )


def missing_ownership(record_kind: str, record_id: str) -> ServiceError:
    return ServiceError(
        "receipt_ownership_missing",
        "Financial effect has neither a native receipt nor an interpreted legacy marker",
        500,
        {"record_kind": record_kind, "record_id": record_id},
    )
