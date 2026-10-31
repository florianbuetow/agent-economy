"""Strict interpretation of attested pre-receipt financial effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from service_commons.exceptions import ServiceError

from db_gateway_service.receipt_audit import (
    financial_fact_sha256,
    legacy_marker_set_attested,
)
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.services.financial_records import missing_ownership, ownership_conflict

if TYPE_CHECKING:
    import sqlite3

    from db_gateway_service.financial_requests import FinancialRequest

_SQLITE_MIN_INTEGER = -(1 << 63)


@dataclass(frozen=True, slots=True)
class _Transaction:
    tx_id: str
    account_id: str
    transaction_type: str
    amount: int
    balance_after: int
    reference: str
    timestamp: str
    event_id: int | None

    def fact(self) -> dict[str, object]:
        return {
            "tx_id": self.tx_id,
            "account_id": self.account_id,
            "type": self.transaction_type,
            "amount": self.amount,
            "balance_after": self.balance_after,
            "reference": self.reference,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
        }


@dataclass(frozen=True, slots=True)
class _Escrow:
    escrow_id: str
    payer_account_id: str
    amount: int
    task_id: str
    status: str
    created_at: str
    resolved_at: str | None
    event_id: int | None

    def lock_fact(self) -> dict[str, object]:
        return {
            "escrow_id": self.escrow_id,
            "payer_account_id": self.payer_account_id,
            "amount": self.amount,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "event_id": self.event_id,
        }

    def projection(self) -> dict[str, object]:
        return {
            "escrow_id": self.escrow_id,
            "payer_account_id": self.payer_account_id,
            "task_id": self.task_id,
            "amount": self.amount,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }


class LegacyFinancialRecords:
    """Verify and expose immutable history without assigning caller ownership."""

    def credit_collision(
        self,
        cursor: sqlite3.Cursor,
        operation_id: str,
        request: FinancialRequest,
        tx_id: object,
        requested_amount: int,
    ) -> dict[str, object]:
        native_owner = self._native_transaction_owner(cursor, tx_id)
        marker = self._transaction_marker(cursor, tx_id)
        if native_owner is not None and marker is None:
            raise ownership_conflict(native_owner)
        self._verify_attestation(cursor)
        strict_tx_id = self._required_text(tx_id)
        if native_owner is not None:
            raise self._ownership_failed()
        if marker is None:
            raise missing_ownership("credit", strict_tx_id)
        transaction = self._transaction(cursor, strict_tx_id)
        self._verify_transaction_marker(transaction, marker, "observed_credit")
        if transaction.amount != requested_amount:
            raise ServiceError(
                "legacy_operation_conflict",
                "Historical credit has different immutable terms",
                409,
                self._credit_details(transaction, marker),
            )
        return {
            "outcome": "legacy_observation",
            "attempt": self._attempt(operation_id, request),
            "observation": {
                "operation_id": None,
                "observed_kind": "observed_credit",
                "transaction": transaction.fact(),
                "observed_fact_sha256": marker[2],
            },
            "current_escrow": None,
        }

    def lock_collision(
        self,
        cursor: sqlite3.Cursor,
        operation_id: str,
        request: FinancialRequest,
        escrow_id: object,
        requested_amount: int,
    ) -> dict[str, object]:
        native_owner = self._native_lock_owner(cursor, escrow_id)
        escrow_marker = self._escrow_marker(cursor, escrow_id)
        if native_owner is not None and escrow_marker is None:
            raise ownership_conflict(native_owner)
        self._verify_attestation(cursor)
        strict_escrow_id = self._required_text(escrow_id)
        if native_owner is not None:
            raise self._ownership_failed()
        if escrow_marker is None:
            raise missing_ownership("escrow", strict_escrow_id)
        escrow = self._escrow(cursor, strict_escrow_id)
        transaction = self._lock_transaction(cursor, escrow)
        transaction_marker = self._transaction_marker(cursor, transaction.tx_id)
        if transaction_marker is None:
            raise self._ownership_failed()
        if self._native_transaction_owner(cursor, transaction.tx_id) is not None:
            raise self._ownership_failed()
        self._verify_transaction_marker(
            transaction,
            transaction_marker,
            "observed_escrow_lock",
        )
        resolution = self._verify_escrow_marker(cursor, escrow, escrow_marker)
        if escrow.amount != requested_amount:
            raise ServiceError(
                "legacy_operation_conflict",
                "Historical escrow lock has different immutable terms",
                409,
                self._lock_details(
                    transaction,
                    transaction_marker,
                    escrow,
                    escrow_marker,
                ),
            )
        return {
            "outcome": "legacy_observation",
            "attempt": self._attempt(operation_id, request),
            "observation": {
                "operation_id": None,
                "observed_kind": "observed_escrow_lock",
                "transaction": transaction.fact(),
                "observed_fact_sha256": transaction_marker[2],
                "escrow_lock": escrow.lock_fact(),
                "observed_status": escrow_marker[1],
                "lock_fact_sha256": escrow_marker[2],
                "resolution_fact": resolution,
                "resolution_fact_sha256": escrow_marker[3],
            },
            "current_escrow": escrow.projection(),
        }

    def admit_settlement(self, cursor: sqlite3.Cursor, escrow_id: str) -> None:
        row = cursor.execute(
            "SELECT 1 FROM bank_escrow WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone()
        if row is None:
            return
        native_lock = self._native_lock_owner(cursor, escrow_id)
        marker = self._escrow_marker(cursor, escrow_id)
        if native_lock is not None and marker is None:
            return
        self._verify_attestation(cursor)
        if native_lock is not None or marker is None:
            if native_lock is not None:
                raise self._ownership_failed()
            raise missing_ownership("escrow", escrow_id)
        escrow = self._escrow(cursor, escrow_id)
        transaction = self._lock_transaction(cursor, escrow)
        transaction_marker = self._transaction_marker(cursor, transaction.tx_id)
        if transaction_marker is None:
            raise self._ownership_failed()
        if self._native_transaction_owner(cursor, transaction.tx_id) is not None:
            raise self._ownership_failed()
        self._verify_transaction_marker(
            transaction,
            transaction_marker,
            "observed_escrow_lock",
        )
        self._verify_escrow_marker(cursor, escrow, marker)
        if marker[1] in {"released", "split"}:
            raise ServiceError(
                "historical_settlement_unreplayable",
                "Historical terminal settlement has no replayable operation identity",
                409,
                {
                    "winning_operation_id": None,
                    "escrow_id": escrow.escrow_id,
                    "observed_status": marker[1],
                    "lock_fact_sha256": marker[2],
                    "resolution_fact_sha256": marker[3],
                },
            )
        if escrow.status == "locked":
            if self._native_settlement_owner(cursor, escrow_id) is not None:
                raise self._ownership_failed()
            return
        if self._native_settlement_owner(cursor, escrow_id) is None:
            raise self._ownership_failed()

    @staticmethod
    def _attempt(operation_id: str, request: FinancialRequest) -> dict[str, object]:
        return {
            "operation_id": operation_id,
            "operation_kind": request.operation_kind,
            "request_sha256": request.request_sha256,
        }

    def _verify_attestation(self, cursor: sqlite3.Cursor) -> None:
        try:
            attested = legacy_marker_set_attested(cursor.connection)
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise self._ownership_failed() from exc
        if not attested:
            raise self._ownership_failed()

    def _verify_transaction_marker(
        self,
        transaction: _Transaction,
        marker: tuple[object, ...],
        expected_kind: str,
    ) -> None:
        if (
            marker[0] != transaction.tx_id
            or marker[1] != expected_kind
            or marker[2] != financial_fact_sha256(transaction.fact())
        ):
            raise self._ownership_failed()

    def _verify_escrow_marker(
        self,
        cursor: sqlite3.Cursor,
        escrow: _Escrow,
        marker: tuple[object, ...],
    ) -> dict[str, object] | None:
        if marker[0] != escrow.escrow_id or marker[2] != financial_fact_sha256(escrow.lock_fact()):
            raise self._ownership_failed()
        observed_status = marker[1]
        resolution_hash = marker[3]
        if observed_status == "locked":
            if resolution_hash is not None:
                raise self._ownership_failed()
            if (
                escrow.status != "locked"
                and self._native_settlement_owner(cursor, escrow.escrow_id) is None
            ):
                raise self._ownership_failed()
            return None
        if observed_status not in {"released", "split"} or escrow.status != observed_status:
            raise self._ownership_failed()
        resolution = self._resolution_fact(cursor, escrow)
        if resolution_hash != financial_fact_sha256(resolution):
            raise self._ownership_failed()
        return resolution

    def _resolution_fact(
        self,
        cursor: sqlite3.Cursor,
        escrow: _Escrow,
    ) -> dict[str, object]:
        rows = cursor.execute(
            "SELECT tx_id FROM bank_transactions "
            "WHERE type='escrow_release' AND reference=? ORDER BY tx_id",
            (escrow.escrow_id,),
        ).fetchall()
        if (escrow.status == "released" and len(rows) != 1) or (
            escrow.status == "split" and len(rows) not in {1, 2}
        ):
            raise self._ownership_failed()
        transaction_ids: list[str] = []
        for row in rows:
            tx_id = self._required_text(row[0])
            transaction = self._transaction(cursor, tx_id)
            marker = self._transaction_marker(cursor, tx_id)
            if marker is None or self._native_transaction_owner(cursor, tx_id) is not None:
                raise self._ownership_failed()
            self._verify_transaction_marker(transaction, marker, "observed_escrow_release")
            transaction_ids.append(tx_id)
        return {
            "escrow_id": escrow.escrow_id,
            "observed_status": escrow.status,
            "resolved_at": escrow.resolved_at,
            "transaction_ids": transaction_ids,
        }

    def _transaction(self, cursor: sqlite3.Cursor, tx_id: str) -> _Transaction:
        row = cursor.execute(
            "SELECT tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id "
            "FROM bank_transactions WHERE tx_id=?",
            (tx_id,),
        ).fetchone()
        if row is None:
            raise self._ownership_failed()
        return _Transaction(
            self._required_text(row[0]),
            self._required_text(row[1]),
            self._transaction_type(row[2]),
            self._money(row[3]),
            self._money(row[4]),
            self._required_text(row[5]),
            self._historical_time(row[6]),
            self._historical_event_id(row[7]),
        )

    def _escrow(self, cursor: sqlite3.Cursor, escrow_id: str) -> _Escrow:
        row = cursor.execute(
            "SELECT escrow_id, payer_account_id, amount, task_id, status, created_at, "
            "resolved_at, event_id FROM bank_escrow WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone()
        if row is None:
            raise self._ownership_failed()
        return _Escrow(
            self._required_text(row[0]),
            self._required_text(row[1]),
            self._positive_money(row[2]),
            self._required_text(row[3]),
            self._escrow_status(row[4]),
            self._historical_time(row[5]),
            self._nullable_historical_time(row[6]),
            self._historical_event_id(row[7]),
        )

    def _lock_transaction(self, cursor: sqlite3.Cursor, escrow: _Escrow) -> _Transaction:
        rows = cursor.execute(
            "SELECT tx_id FROM bank_transactions "
            "WHERE account_id=? AND type='escrow_lock' AND reference=? ORDER BY tx_id",
            (escrow.payer_account_id, escrow.task_id),
        ).fetchall()
        if len(rows) != 1:
            raise self._ownership_failed()
        transaction = self._transaction(cursor, self._required_text(rows[0][0]))
        if transaction.amount != escrow.amount or transaction.event_id != escrow.event_id:
            raise self._ownership_failed()
        return transaction

    @staticmethod
    def _transaction_marker(
        cursor: sqlite3.Cursor,
        tx_id: object,
    ) -> tuple[object, ...] | None:
        row = cursor.execute(
            "SELECT tx_id, observed_kind, observed_fact_sha256 "
            "FROM bank_legacy_transaction_observations WHERE tx_id=?",
            (tx_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    @staticmethod
    def _escrow_marker(
        cursor: sqlite3.Cursor,
        escrow_id: object,
    ) -> tuple[object, ...] | None:
        row = cursor.execute(
            "SELECT escrow_id, observed_status, lock_fact_sha256, resolution_fact_sha256 "
            "FROM bank_legacy_escrow_observations WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    @staticmethod
    def _native_transaction_owner(cursor: sqlite3.Cursor, tx_id: object) -> str | None:
        rows = cursor.execute(
            "SELECT operation_id FROM bank_operation_legs WHERE transaction_id=?",
            (tx_id,),
        ).fetchall()
        if len(rows) > 1:
            raise LegacyFinancialRecords._ownership_failed()
        return None if not rows else LegacyFinancialRecords._required_text(rows[0][0])

    @staticmethod
    def _native_lock_owner(cursor: sqlite3.Cursor, escrow_id: object) -> str | None:
        rows = cursor.execute(
            "SELECT operation_id FROM bank_operation_receipts "
            "WHERE escrow_id=? AND operation_kind='escrow_lock'",
            (escrow_id,),
        ).fetchall()
        if len(rows) > 1:
            raise LegacyFinancialRecords._ownership_failed()
        return None if not rows else LegacyFinancialRecords._required_text(rows[0][0])

    @staticmethod
    def _native_settlement_owner(cursor: sqlite3.Cursor, escrow_id: str) -> str | None:
        rows = cursor.execute(
            "SELECT operation_id FROM bank_operation_receipts "
            "WHERE escrow_id=? AND operation_kind IN ('escrow_release', 'escrow_split')",
            (escrow_id,),
        ).fetchall()
        if len(rows) > 1:
            raise LegacyFinancialRecords._ownership_failed()
        return None if not rows else LegacyFinancialRecords._required_text(rows[0][0])

    @staticmethod
    def _credit_details(
        transaction: _Transaction,
        marker: tuple[object, ...],
    ) -> dict[str, object]:
        return {
            "winning_operation_id": None,
            "observed_kind": "observed_credit",
            "tx_id": transaction.tx_id,
            "observed_fact_sha256": marker[2],
        }

    @staticmethod
    def _lock_details(
        transaction: _Transaction,
        transaction_marker: tuple[object, ...],
        escrow: _Escrow,
        escrow_marker: tuple[object, ...],
    ) -> dict[str, object]:
        return {
            "winning_operation_id": None,
            "observed_kind": "observed_escrow_lock",
            "tx_id": transaction.tx_id,
            "observed_fact_sha256": transaction_marker[2],
            "escrow_id": escrow.escrow_id,
            "observed_status": escrow_marker[1],
            "lock_fact_sha256": escrow_marker[2],
            "resolution_fact_sha256": escrow_marker[3],
        }

    @staticmethod
    def _required_text(value: object) -> str:
        if not isinstance(value, str) or not value:
            raise LegacyFinancialRecords._ownership_failed()
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise LegacyFinancialRecords._ownership_failed() from exc
        return value

    @staticmethod
    def _historical_time(value: object) -> str:
        if not isinstance(value, str):
            raise LegacyFinancialRecords._ownership_failed()
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise LegacyFinancialRecords._ownership_failed() from exc
        return value

    @staticmethod
    def _nullable_historical_time(value: object) -> str | None:
        if value is None:
            return None
        return LegacyFinancialRecords._historical_time(value)

    @staticmethod
    def _historical_event_id(value: object) -> int | None:
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not _SQLITE_MIN_INTEGER <= value <= SQLITE_MAX_INTEGER
        ):
            raise LegacyFinancialRecords._ownership_failed()
        return value

    @staticmethod
    def _money(value: object) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= SQLITE_MAX_INTEGER
        ):
            raise LegacyFinancialRecords._ownership_failed()
        return value

    @staticmethod
    def _positive_money(value: object) -> int:
        amount = LegacyFinancialRecords._money(value)
        if amount == 0:
            raise LegacyFinancialRecords._ownership_failed()
        return amount

    @staticmethod
    def _transaction_type(value: object) -> str:
        transaction_type = LegacyFinancialRecords._required_text(value)
        if transaction_type not in {"credit", "escrow_lock", "escrow_release"}:
            raise LegacyFinancialRecords._ownership_failed()
        return transaction_type

    @staticmethod
    def _escrow_status(value: object) -> str:
        status = LegacyFinancialRecords._required_text(value)
        if status not in {"locked", "released", "split"}:
            raise LegacyFinancialRecords._ownership_failed()
        return status

    @staticmethod
    def _ownership_failed() -> ServiceError:
        return ServiceError(
            "receipt_ownership_failed",
            "Historical financial ownership verification failed",
            500,
            {},
        )


__all__ = ["LegacyFinancialRecords"]
