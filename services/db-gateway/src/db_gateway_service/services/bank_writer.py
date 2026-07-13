"""Central Bank domain writes (WP-11, B16 god-class decomposition).

Extracted from db_writer.py. See identity_writer.py's module docstring for
the shared-connection/no-await invariant this preserves.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from db_gateway_service.services.db_writer_helpers import (
    check_constraint_violation,
    compile_constraints,
    insert_event,
    is_foreign_key_violation,
    is_unique_violation,
)

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter


class BankWriter:
    """Handles account creation, credit, and escrow lock/release/split writes."""

    def __init__(self, writer: DbWriter) -> None:
        self._writer = writer

    def create_account(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a bank account with optional initial credit.

        INSERT INTO bank_accounts + optional INSERT INTO bank_transactions + INSERT INTO events.
        Idempotency: PK on account_id.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
                (data["account_id"], data["balance"], data["created_at"]),
            )
            event_id = insert_event(cursor, data["event"])
            # Optional initial credit transaction
            initial_credit = data.get("initial_credit")
            if initial_credit is not None:
                cursor.execute(
                    "INSERT INTO bank_transactions "
                    "(tx_id, account_id, type, amount, balance_after, reference, timestamp, "
                    "event_id) "
                    "VALUES (?, ?, 'credit', ?, ?, ?, ?, ?)",
                    (
                        initial_credit["tx_id"],
                        data["account_id"],
                        initial_credit["amount"],
                        data["balance"],
                        initial_credit["reference"],
                        initial_credit["timestamp"],
                        event_id,
                    ),
                )
            db.commit()
            return {"account_id": data["account_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "account_exists",
                "Account already exists for this agent",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

    def _lookup_account(self, account_id: str) -> dict[str, Any] | None:
        """Look up an account by ID."""
        cursor = self._writer.connection.execute(
            "SELECT account_id, balance, created_at FROM bank_accounts WHERE account_id = ?",
            (account_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"account_id": row[0], "balance": row[1], "created_at": row[2]}

    def credit_account(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Credit an account.

        UPDATE bank_accounts + INSERT INTO bank_transactions + INSERT INTO events.
        Idempotency: idx_bank_tx_idempotent on (account_id, reference) WHERE type='credit'.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ?",
                (data["amount"], data["account_id"]),
            )
            if cursor.rowcount == 0:
                db.rollback()
                raise ServiceError("account_not_found", "No account with this account_id", 404, {})
            # The event is written first so the transaction row can name it.
            event_id = insert_event(cursor, data["event"])
            cursor.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
                "VALUES (?, ?, 'credit', ?, "
                "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?, ?)",
                (
                    data["tx_id"],
                    data["account_id"],
                    data["amount"],
                    data["account_id"],
                    data["reference"],
                    data["timestamp"],
                    event_id,
                ),
            )
            balance_cursor = db.execute(
                "SELECT balance FROM bank_accounts WHERE account_id = ?",
                (data["account_id"],),
            )
            balance_row = balance_cursor.fetchone()
            balance_after = int(balance_row[0]) if balance_row else 0
            db.commit()
            return {
                "tx_id": data["tx_id"],
                "balance_after": balance_after,
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_unique_violation(exc):
                # Idempotency check — same (account_id, reference) for credit
                existing = self._lookup_credit_tx(data["account_id"], data["reference"])
                if existing is not None and existing["amount"] == data["amount"]:
                    return {
                        "tx_id": existing["tx_id"],
                        "balance_after": existing["balance_after"],
                        "event_id": existing["event_id"],
                    }
                raise ServiceError(
                    "reference_conflict",
                    "Same (account_id, reference) exists with different amount",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "reference_conflict",
                "Transaction constraint violation",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

    def _lookup_credit_tx(self, account_id: str, reference: str) -> dict[str, Any] | None:
        """Look up an existing credit transaction by (account_id, reference).

        event_id is None for a legacy row written before the column existed.
        """
        cursor = self._writer.connection.execute(
            "SELECT tx_id, amount, balance_after, event_id FROM bank_transactions "
            "WHERE account_id = ? AND reference = ? AND type = 'credit'",
            (account_id, reference),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"tx_id": row[0], "amount": row[1], "balance_after": row[2], "event_id": row[3]}

    def escrow_lock(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Lock funds in escrow.

        UPDATE bank_accounts (debit) + INSERT INTO bank_escrow +
        INSERT INTO bank_transactions + INSERT INTO events.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            # Debit payer (balance check via WHERE clause)
            cursor.execute(
                "UPDATE bank_accounts SET balance = balance - ? "
                "WHERE account_id = ? AND balance >= ?",
                (data["amount"], data["payer_account_id"], data["amount"]),
            )
            if cursor.rowcount == 0:
                # Check if account exists at all
                acct = self._lookup_account(data["payer_account_id"])
                db.rollback()
                if acct is None:
                    raise ServiceError(
                        "account_not_found", "No account for payer_account_id", 404, {}
                    )
                raise ServiceError(
                    "insufficient_funds",
                    "Account balance is less than the escrow amount",
                    402,
                    {},
                )
            # The event is written first so the escrow and transaction rows can name it.
            event_id = insert_event(cursor, data["event"])
            # Create escrow record
            cursor.execute(
                "INSERT INTO bank_escrow "
                "(escrow_id, payer_account_id, amount, task_id, status, created_at, event_id) "
                "VALUES (?, ?, ?, ?, 'locked', ?, ?)",
                (
                    data["escrow_id"],
                    data["payer_account_id"],
                    data["amount"],
                    data["task_id"],
                    data["created_at"],
                    event_id,
                ),
            )
            # Log escrow_lock transaction
            cursor.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
                "VALUES (?, ?, 'escrow_lock', ?, "
                "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?, ?)",
                (
                    data["tx_id"],
                    data["payer_account_id"],
                    data["amount"],
                    data["payer_account_id"],
                    data["task_id"],
                    data["created_at"],
                    event_id,
                ),
            )
            # Get balance after debit
            balance_cursor = db.execute(
                "SELECT balance FROM bank_accounts WHERE account_id = ?",
                (data["payer_account_id"],),
            )
            balance_row = balance_cursor.fetchone()
            balance_after = int(balance_row[0]) if balance_row else 0
            db.commit()
            return {
                "escrow_id": data["escrow_id"],
                "balance_after": balance_after,
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            if is_unique_violation(exc):
                # Idempotency: check existing escrow
                existing = self._lookup_active_escrow(data["payer_account_id"], data["task_id"])
                if existing is not None and existing["amount"] == data["amount"]:
                    account = self._lookup_account(data["payer_account_id"])
                    if account is None:
                        raise ServiceError(
                            "account_not_found", "No account for payer_account_id", 404, {}
                        ) from exc
                    return {
                        "escrow_id": existing["escrow_id"],
                        "balance_after": int(account["balance"]),
                        "event_id": existing["event_id"],
                    }
                raise ServiceError(
                    "escrow_already_locked",
                    "Escrow already locked for this (payer, task) pair",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "escrow_already_locked",
                "Escrow constraint violation",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

    def _lookup_active_escrow(self, payer_account_id: str, task_id: str) -> dict[str, Any] | None:
        """Look up an active (locked) escrow.

        event_id is None for a legacy row written before the column existed.
        """
        cursor = self._writer.connection.execute(
            "SELECT escrow_id, amount, event_id FROM bank_escrow "
            "WHERE payer_account_id = ? AND task_id = ? AND status = 'locked'",
            (payer_account_id, task_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"escrow_id": row[0], "amount": row[1], "event_id": row[2]}

    def escrow_release(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Release escrowed funds to a recipient.

        SELECT escrow + UPDATE bank_accounts (credit) +
        INSERT INTO bank_transactions + UPDATE bank_escrow + INSERT INTO events.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            # Load and verify escrow
            escrow = self._load_escrow(cursor, data["escrow_id"])
            # Credit recipient
            cursor.execute(
                "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ?",
                (escrow["amount"], data["recipient_account_id"]),
            )
            if cursor.rowcount == 0:
                db.rollback()
                raise ServiceError("account_not_found", "Recipient account not found", 404, {})
            # The event is written first so the transaction row can name it.
            event_id = insert_event(cursor, data["event"])
            # Log escrow_release transaction
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
            # Resolve escrow
            if constraints is not None:
                where_clause, where_params = compile_constraints(
                    "bank_escrow",
                    "escrow_id",
                    data["escrow_id"],
                    constraints,
                )
                cursor.execute(
                    f"UPDATE bank_escrow SET status = 'released', resolved_at = ? "
                    f"WHERE {where_clause}",  # nosec B608
                    [data["resolved_at"], *where_params],
                )
                if cursor.rowcount == 0:
                    try:
                        check_constraint_violation(
                            cursor,
                            "bank_escrow",
                            "escrow_id",
                            data["escrow_id"],
                            constraints,
                        )
                    except ServiceError:
                        db.rollback()
                        raise
                    db.rollback()
                    raise ServiceError("escrow_not_found", "No escrow with this ID", 404, {})
            else:
                cursor.execute(
                    "UPDATE bank_escrow SET status = 'released', resolved_at = ? "
                    "WHERE escrow_id = ?",
                    (data["resolved_at"], data["escrow_id"]),
                )
            db.commit()
            return {
                "escrow_id": data["escrow_id"],
                "status": "released",
                "amount": escrow["amount"],
                "recipient_account_id": data["recipient_account_id"],
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except Exception:
            db.rollback()
            raise

    def _load_escrow(self, cursor: sqlite3.Cursor, escrow_id: str) -> dict[str, Any]:
        """Load an escrow and verify it is locked. Raises on not found or already resolved."""
        cursor.execute(
            "SELECT escrow_id, amount, status FROM bank_escrow WHERE escrow_id = ?",
            (escrow_id,),
        )
        row = cursor.fetchone()
        if row is None:
            self._writer.connection.rollback()
            raise ServiceError("escrow_not_found", "No escrow with this ID", 404, {})
        if row[2] != "locked":
            self._writer.connection.rollback()
            raise ServiceError(
                "escrow_already_resolved",
                "Escrow has already been released or split",
                409,
                {},
            )
        return {"escrow_id": row[0], "amount": row[1], "status": row[2]}

    def escrow_split(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Split escrowed funds between worker and poster.

        Validates amounts sum to escrow amount. Credits both accounts
        (skipping zero-amount shares). Resolves escrow as 'split'.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            # Load and verify escrow
            escrow = self._load_escrow(cursor, data["escrow_id"])
            # Validate amounts sum
            worker_amount = data["worker_amount"]
            poster_amount = data["poster_amount"]
            if worker_amount + poster_amount != escrow["amount"]:
                db.rollback()
                raise ServiceError(
                    "amount_mismatch",
                    "worker_amount + poster_amount does not equal escrow amount",
                    400,
                    {},
                )
            # The event is written first so the transaction rows can name it.
            event_id = insert_event(cursor, data["event"])
            # Credit worker (if amount > 0)
            if worker_amount > 0:
                cursor.execute(
                    "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ?",
                    (worker_amount, data["worker_account_id"]),
                )
                if cursor.rowcount == 0:
                    db.rollback()
                    raise ServiceError("account_not_found", "Worker account not found", 404, {})
                cursor.execute(
                    "INSERT INTO bank_transactions "
                    "(tx_id, account_id, type, amount, balance_after, reference, timestamp, "
                    "event_id) "
                    "VALUES (?, ?, 'escrow_release', ?, "
                    "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?, ?)",
                    (
                        data["worker_tx_id"],
                        data["worker_account_id"],
                        worker_amount,
                        data["worker_account_id"],
                        data["escrow_id"],
                        data["resolved_at"],
                        event_id,
                    ),
                )
            # Credit poster (if amount > 0)
            if poster_amount > 0:
                cursor.execute(
                    "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ?",
                    (poster_amount, data["poster_account_id"]),
                )
                if cursor.rowcount == 0:
                    db.rollback()
                    raise ServiceError("account_not_found", "Poster account not found", 404, {})
                cursor.execute(
                    "INSERT INTO bank_transactions "
                    "(tx_id, account_id, type, amount, balance_after, reference, timestamp, "
                    "event_id) "
                    "VALUES (?, ?, 'escrow_release', ?, "
                    "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?, ?)",
                    (
                        data["poster_tx_id"],
                        data["poster_account_id"],
                        poster_amount,
                        data["poster_account_id"],
                        data["escrow_id"],
                        data["resolved_at"],
                        event_id,
                    ),
                )
            # Resolve escrow
            if constraints is not None:
                where_clause, where_params = compile_constraints(
                    "bank_escrow",
                    "escrow_id",
                    data["escrow_id"],
                    constraints,
                )
                cursor.execute(
                    f"UPDATE bank_escrow SET status = 'split', resolved_at = ? "
                    f"WHERE {where_clause}",  # nosec B608
                    [data["resolved_at"], *where_params],
                )
                if cursor.rowcount == 0:
                    try:
                        check_constraint_violation(
                            cursor,
                            "bank_escrow",
                            "escrow_id",
                            data["escrow_id"],
                            constraints,
                        )
                    except ServiceError:
                        db.rollback()
                        raise
                    db.rollback()
                    raise ServiceError("escrow_not_found", "No escrow with this ID", 404, {})
            else:
                cursor.execute(
                    "UPDATE bank_escrow SET status = 'split', resolved_at = ? WHERE escrow_id = ?",
                    (data["resolved_at"], data["escrow_id"]),
                )
            db.commit()
            return {
                "escrow_id": data["escrow_id"],
                "status": "split",
                "worker_amount": worker_amount,
                "poster_amount": poster_amount,
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except Exception:
            db.rollback()
            raise
