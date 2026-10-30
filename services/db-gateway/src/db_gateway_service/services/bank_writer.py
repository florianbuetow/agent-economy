"""Central Bank writes with atomic native operation receipts."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from db_gateway_service.financial_requests import canonical_request, validate_operation_id
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.services.db_writer_helpers import insert_event, is_foreign_key_violation
from db_gateway_service.services.financial_records import (
    FinancialRecords,
    missing_ownership,
    ownership_conflict,
)
from db_gateway_service.services.legacy_financial_records import LegacyFinancialRecords

if TYPE_CHECKING:
    from db_gateway_service.financial_requests import FinancialRequest
    from db_gateway_service.services.db_writer import DbWriter


def _money_amount(value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ServiceError(
            "invalid_amount", "Amount must be an integer in the required range", 400, {}
        )
    if value > SQLITE_MAX_INTEGER:
        raise ServiceError("amount_out_of_range", "Amount exceeds SQLite integer capacity", 400, {})
    return value


def _validate_initial_issuance(data: dict[str, Any]) -> None:
    balance = _money_amount(data["balance"], minimum=0)
    initial_credit = data.get("initial_credit")
    if initial_credit is None and balance == 0:
        return
    if not isinstance(initial_credit, dict):
        raise ServiceError(
            "payload_mismatch", "Initial balance requires matching issuance", 400, {}
        )
    amount = _money_amount(initial_credit.get("amount"), minimum=1)
    if amount != balance or initial_credit.get("reference") != "initial_balance":
        raise ServiceError(
            "payload_mismatch",
            "Initial balance requires the fixed matching issuance",
            400,
            {},
        )


def _financial_request(
    data: dict[str, Any],
    operation_kind: str,
    fields: dict[str, object],
) -> tuple[str, FinancialRequest]:
    try:
        operation_id = validate_operation_id(data.get("operation_id"))
    except ValueError as exc:
        raise ServiceError(
            "invalid_operation_id",
            "operation_id must be a canonical op-UUID string",
            400,
            {"field": "operation_id"},
        ) from exc
    try:
        request = canonical_request(operation_kind, fields)
    except ValueError as exc:
        raise ServiceError("invalid_financial_request", str(exc), 400, {}) from exc
    return operation_id, request


def _settlement_event(event: dict[str, Any], escrow_task_id: str) -> dict[str, Any]:
    supplied_task_id = event.get("task_id")
    if supplied_task_id is not None and supplied_task_id != escrow_task_id:
        raise ServiceError(
            "payload_mismatch",
            "Settlement event task_id does not match the escrow task",
            400,
            {},
        )
    bound_event = dict(event)
    bound_event["task_id"] = escrow_task_id
    return bound_event


class BankWriter:
    """Serialize money effects, immutable receipts, and legs in one transaction."""

    def __init__(self, writer: DbWriter) -> None:
        self._writer = writer
        self._records = FinancialRecords()
        self._legacy = LegacyFinancialRecords()

    def create_account(self, data: dict[str, Any]) -> dict[str, Any]:
        _validate_initial_issuance(data)
        balance = int(data["balance"])
        if balance == 0:
            if "operation_id" in data:
                raise ServiceError(
                    "invalid_operation_id",
                    "Zero-balance account creation has no financial operation identity",
                    400,
                    {"field": "operation_id"},
                )
            return self._create_zero_account(data)

        initial_credit = data["initial_credit"]
        operation_id, request = _financial_request(
            data,
            "account_issuance",
            {
                "account_id": data["account_id"],
                "amount": balance,
                "reference": "initial_balance",
            },
        )
        db = self._writer.connection
        cursor = db.cursor()
        receipt_phase = False
        try:
            cursor.execute("BEGIN IMMEDIATE")
            replay = self._records.replay(cursor, operation_id, request)
            if replay is not None:
                db.rollback()
                return replay
            existing = self._lookup_account(data["account_id"])
            if existing is not None:
                winner = self._records.credit_winner(cursor, data["account_id"], "initial_balance")
                if winner is not None:
                    raise ownership_conflict(winner)
                raise ServiceError("account_exists", "Account already exists", 409, {})
            cursor.execute(
                "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
                (data["account_id"], balance, data["created_at"]),
            )
            event_id = insert_event(cursor, data["event"])
            cursor.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
                "VALUES (?, ?, 'credit', ?, ?, 'initial_balance', ?, ?)",
                (
                    initial_credit["tx_id"],
                    data["account_id"],
                    balance,
                    balance,
                    initial_credit["timestamp"],
                    event_id,
                ),
            )
            result = {
                "account_id": data["account_id"],
                "issued_amount": balance,
                "balance_after": balance,
            }
            legs = (
                {
                    "role": "account",
                    "account_id": data["account_id"],
                    "direction": "credit",
                    "amount": balance,
                    "transaction_id": initial_credit["tx_id"],
                    "balance_after": balance,
                },
            )
            receipt_phase = True
            envelope = self._records.persist(
                cursor,
                operation_id,
                request,
                initial_credit["timestamp"],
                event_id,
                None,
                result,
                legs,
            )
            db.commit()
            return envelope
        except ServiceError:
            db.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if receipt_phase:
                raise
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation", "Foreign key constraint failed", 409, {}
                ) from exc
            raise ServiceError("account_exists", "Account already exists", 409, {}) from exc
        except Exception:
            db.rollback()
            raise

    def _create_zero_account(self, data: dict[str, Any]) -> dict[str, Any]:
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, 0, ?)",
                (data["account_id"], data["created_at"]),
            )
            event_id = insert_event(cursor, data["event"])
            db.commit()
            return {"account_id": data["account_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation", "Foreign key constraint failed", 409, {}
                ) from exc
            raise ServiceError("account_exists", "Account already exists", 409, {}) from exc
        except Exception:
            db.rollback()
            raise

    def credit_account(self, data: dict[str, Any]) -> dict[str, Any]:
        amount = _money_amount(data["amount"], minimum=1)
        operation_id, request = _financial_request(
            data,
            "credit",
            {
                "account_id": data["account_id"],
                "amount": amount,
                "reference": data["reference"],
            },
        )
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            replay = self._records.replay(cursor, operation_id, request)
            if replay is not None:
                db.rollback()
                return replay
            existing = self._lookup_credit_tx(data["account_id"], data["reference"])
            if existing is not None:
                observation = self._legacy.credit_collision(
                    cursor,
                    operation_id,
                    request,
                    existing["tx_id"],
                    amount,
                )
                db.rollback()
                return observation
            self._credit_balance(cursor, data["account_id"], amount)
            event_id = insert_event(cursor, data["event"])
            cursor.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
                "VALUES (?, ?, 'credit', ?, "
                "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?, ?)",
                (
                    data["tx_id"],
                    data["account_id"],
                    amount,
                    data["account_id"],
                    data["reference"],
                    data["timestamp"],
                    event_id,
                ),
            )
            balance_after = self._balance(cursor, data["account_id"])
            result = {
                "account_id": data["account_id"],
                "amount": amount,
                "balance_after": balance_after,
                "reference": data["reference"],
            }
            legs = (
                {
                    "role": "account",
                    "account_id": data["account_id"],
                    "direction": "credit",
                    "amount": amount,
                    "transaction_id": data["tx_id"],
                    "balance_after": balance_after,
                },
            )
            envelope = self._records.persist(
                cursor,
                operation_id,
                request,
                data["timestamp"],
                event_id,
                None,
                result,
                legs,
            )
            db.commit()
            return envelope
        except ServiceError:
            db.rollback()
            raise
        except sqlite3.IntegrityError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            raise

    def escrow_lock(self, data: dict[str, Any]) -> dict[str, Any]:
        amount = _money_amount(data["amount"], minimum=1)
        operation_id, request = _financial_request(
            data,
            "escrow_lock",
            {
                "payer_account_id": data["payer_account_id"],
                "task_id": data["task_id"],
                "amount": amount,
            },
        )
        db = self._writer.connection
        cursor = db.cursor()
        receipt_phase = False
        try:
            cursor.execute("BEGIN IMMEDIATE")
            replay = self._records.replay(cursor, operation_id, request)
            if replay is not None:
                db.rollback()
                return replay
            existing = self._lookup_escrow(data["payer_account_id"], data["task_id"])
            if existing is not None:
                observation = self._legacy.lock_collision(
                    cursor,
                    operation_id,
                    request,
                    existing["escrow_id"],
                    amount,
                )
                db.rollback()
                return observation
            cursor.execute(
                "UPDATE bank_accounts SET balance = balance - ? "
                "WHERE account_id = ? AND balance >= ?",
                (amount, data["payer_account_id"], amount),
            )
            if cursor.rowcount == 0:
                if self._lookup_account(data["payer_account_id"]) is None:
                    raise ServiceError(
                        "account_not_found", "No account for payer_account_id", 404, {}
                    )
                raise ServiceError(
                    "insufficient_funds",
                    "Account balance is less than the escrow amount",
                    402,
                    {},
                )
            event_id = insert_event(cursor, data["event"])
            cursor.execute(
                "INSERT INTO bank_escrow "
                "(escrow_id, payer_account_id, amount, task_id, status, created_at, event_id) "
                "VALUES (?, ?, ?, ?, 'locked', ?, ?)",
                (
                    data["escrow_id"],
                    data["payer_account_id"],
                    amount,
                    data["task_id"],
                    data["created_at"],
                    event_id,
                ),
            )
            cursor.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
                "VALUES (?, ?, 'escrow_lock', ?, "
                "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?, ?)",
                (
                    data["tx_id"],
                    data["payer_account_id"],
                    amount,
                    data["payer_account_id"],
                    data["task_id"],
                    data["created_at"],
                    event_id,
                ),
            )
            balance_after = self._balance(cursor, data["payer_account_id"])
            result = {"escrow_status_at_commit": "locked"}
            legs = (
                {
                    "role": "payer",
                    "account_id": data["payer_account_id"],
                    "direction": "debit",
                    "amount": amount,
                    "transaction_id": data["tx_id"],
                    "balance_after": balance_after,
                },
            )
            receipt_phase = True
            envelope = self._records.persist(
                cursor,
                operation_id,
                request,
                data["created_at"],
                event_id,
                data["escrow_id"],
                result,
                legs,
            )
            db.commit()
            return envelope
        except ServiceError:
            db.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if receipt_phase:
                raise
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation", "Foreign key constraint failed", 409, {}
                ) from exc
            raise ServiceError(
                "escrow_already_locked", "Escrow constraint violation", 409, {}
            ) from exc
        except Exception:
            db.rollback()
            raise

    def escrow_release(self, data: dict[str, Any]) -> dict[str, Any]:
        operation_id, request = _financial_request(
            data,
            "escrow_release",
            {
                "escrow_id": data["escrow_id"],
                "recipient_account_id": data["recipient_account_id"],
                "source_reference": data["source_reference"],
            },
        )
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            replay = self._records.replay(cursor, operation_id, request)
            if replay is not None:
                db.rollback()
                return replay
            self._legacy.admit_settlement(cursor, data["escrow_id"])
            escrow = self._load_escrow_for_settlement(cursor, data["escrow_id"])
            event = _settlement_event(data["event"], escrow["task_id"])
            self._credit_balance(cursor, data["recipient_account_id"], escrow["amount"])
            event_id = insert_event(cursor, event)
            cursor.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
                "VALUES (?, ?, 'escrow_release', ?, "
                "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?, ?)",
                (
                    data["tx_id"],
                    data["recipient_account_id"],
                    escrow["amount"],
                    data["recipient_account_id"],
                    data["escrow_id"],
                    data["resolved_at"],
                    event_id,
                ),
            )
            cursor.execute(
                "UPDATE bank_escrow SET status = 'released', resolved_at = ? "
                "WHERE escrow_id = ? AND status = 'locked'",
                (data["resolved_at"], data["escrow_id"]),
            )
            if cursor.rowcount != 1:
                raise ServiceError("escrow_state_conflict", "Escrow is no longer locked", 409, {})
            balance_after = self._balance(cursor, data["recipient_account_id"])
            envelope = self._records.persist(
                cursor,
                operation_id,
                request,
                data["resolved_at"],
                event_id,
                data["escrow_id"],
                {"escrow_status_at_commit": "released"},
                (
                    {
                        "role": "recipient",
                        "account_id": data["recipient_account_id"],
                        "direction": "credit",
                        "amount": escrow["amount"],
                        "transaction_id": data["tx_id"],
                        "balance_after": balance_after,
                    },
                ),
            )
            db.commit()
            return envelope
        except ServiceError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            raise

    def escrow_split(self, data: dict[str, Any]) -> dict[str, Any]:
        """Split by percentage, or settle an exact worker amount when worker_amount is given."""
        exact = "worker_amount" in data
        share_field = "worker_amount" if exact else "worker_pct"
        operation_id, request = _financial_request(
            data,
            "escrow_split",
            {
                "escrow_id": data["escrow_id"],
                "worker_account_id": data["worker_account_id"],
                "poster_account_id": data["poster_account_id"],
                share_field: data[share_field],
                "source_reference": data["source_reference"],
            },
        )
        share = int(data[share_field])
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            replay = self._records.replay(cursor, operation_id, request)
            if replay is not None:
                db.rollback()
                return replay
            self._legacy.admit_settlement(cursor, data["escrow_id"])
            escrow = self._load_escrow_for_settlement(cursor, data["escrow_id"])
            event = _settlement_event(data["event"], escrow["task_id"])
            if data["poster_account_id"] != escrow["payer_account_id"]:
                raise ServiceError("payload_mismatch", "Poster is not the escrow payer", 400, {})
            if exact and share > escrow["amount"]:
                raise ServiceError(
                    "invalid_amount", "Worker amount exceeds the escrow amount", 400, {}
                )
            worker_amount = share if exact else escrow["amount"] * share // 100
            poster_amount = escrow["amount"] - worker_amount
            event_id = insert_event(cursor, event)
            worker_balance = self._credit_split_leg(
                cursor,
                data["worker_account_id"],
                worker_amount,
                data["worker_tx_id"],
                data["escrow_id"],
                data["resolved_at"],
                event_id,
            )
            poster_balance = self._credit_split_leg(
                cursor,
                data["poster_account_id"],
                poster_amount,
                data["poster_tx_id"],
                data["escrow_id"],
                data["resolved_at"],
                event_id,
            )
            cursor.execute(
                "UPDATE bank_escrow SET status = 'split', resolved_at = ? "
                "WHERE escrow_id = ? AND status = 'locked'",
                (data["resolved_at"], data["escrow_id"]),
            )
            if cursor.rowcount != 1:
                raise ServiceError("escrow_state_conflict", "Escrow is no longer locked", 409, {})
            legs = (
                {
                    "role": "worker",
                    "account_id": data["worker_account_id"],
                    "direction": "credit",
                    "amount": worker_amount,
                    "transaction_id": None if worker_amount == 0 else data["worker_tx_id"],
                    "balance_after": worker_balance,
                },
                {
                    "role": "poster",
                    "account_id": data["poster_account_id"],
                    "direction": "credit",
                    "amount": poster_amount,
                    "transaction_id": None if poster_amount == 0 else data["poster_tx_id"],
                    "balance_after": poster_balance,
                },
            )
            envelope = self._records.persist(
                cursor,
                operation_id,
                request,
                data["resolved_at"],
                event_id,
                data["escrow_id"],
                {"escrow_status_at_commit": "split", share_field: share},
                legs,
            )
            db.commit()
            return envelope
        except ServiceError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            raise

    def _credit_split_leg(
        self,
        cursor: sqlite3.Cursor,
        account_id: str,
        amount: int,
        transaction_id: str,
        escrow_id: str,
        timestamp: str,
        event_id: int,
    ) -> int | None:
        if amount == 0:
            return None
        self._credit_balance(cursor, account_id, amount)
        cursor.execute(
            "INSERT INTO bank_transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
            "VALUES (?, ?, 'escrow_release', ?, "
            "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?, ?)",
            (transaction_id, account_id, amount, account_id, escrow_id, timestamp, event_id),
        )
        return self._balance(cursor, account_id)

    def _load_escrow_for_settlement(
        self,
        cursor: sqlite3.Cursor,
        escrow_id: str,
    ) -> dict[str, Any]:
        row = cursor.execute(
            "SELECT escrow_id, amount, status, payer_account_id, task_id FROM bank_escrow "
            "WHERE escrow_id = ?",
            (escrow_id,),
        ).fetchone()
        if row is None:
            raise ServiceError("escrow_not_found", "No escrow with this ID", 404, {})
        if row[2] != "locked":
            winner = self._records.settlement_winner(cursor, escrow_id)
            if winner is None:
                raise missing_ownership("escrow_settlement", escrow_id)
            raise ownership_conflict(winner)
        return {
            "escrow_id": str(row[0]),
            "amount": int(row[1]),
            "status": str(row[2]),
            "payer_account_id": str(row[3]),
            "task_id": str(row[4]),
        }

    def _credit_balance(self, cursor: sqlite3.Cursor, account_id: str, amount: int) -> None:
        cursor.execute(
            "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ? AND balance <= ?",
            (amount, account_id, SQLITE_MAX_INTEGER - amount),
        )
        if cursor.rowcount:
            return
        if self._lookup_account(account_id) is None:
            raise ServiceError("account_not_found", "No account with this account_id", 404, {})
        raise ServiceError("amount_out_of_range", "Credit would exceed account capacity", 409, {})

    def _balance(self, cursor: sqlite3.Cursor, account_id: str) -> int:
        row = cursor.execute(
            "SELECT balance FROM bank_accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row is None:
            raise ServiceError("account_not_found", "No account with this account_id", 404, {})
        return int(row[0])

    def _lookup_account(self, account_id: str) -> dict[str, Any] | None:
        row = self._writer.connection.execute(
            "SELECT account_id, balance, created_at FROM bank_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return {"account_id": row[0], "balance": row[1], "created_at": row[2]}

    def _lookup_credit_tx(self, account_id: str, reference: str) -> dict[str, Any] | None:
        row = self._writer.connection.execute(
            "SELECT tx_id, amount, balance_after, event_id FROM bank_transactions "
            "WHERE account_id = ? AND reference = ? AND type = 'credit'",
            (account_id, reference),
        ).fetchone()
        if row is None:
            return None
        return {"tx_id": row[0], "amount": row[1], "balance_after": row[2], "event_id": row[3]}

    def _lookup_escrow(self, payer_account_id: str, task_id: str) -> dict[str, Any] | None:
        row = self._writer.connection.execute(
            "SELECT escrow_id, amount, event_id, status FROM bank_escrow "
            "WHERE payer_account_id = ? AND task_id = ?",
            (payer_account_id, task_id),
        ).fetchone()
        if row is None:
            return None
        return {"escrow_id": row[0], "amount": row[1], "event_id": row[2], "status": row[3]}
