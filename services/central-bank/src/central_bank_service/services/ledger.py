"""Ledger business logic — accounts, transactions, and escrow."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from threading import RLock
from typing import cast

from service_commons.exceptions import ServiceError


class Ledger:
    """
    Manages accounts, transactions, and escrow.

    Uses SQLite for persistence. All balance mutations and their
    corresponding transaction log entries happen in a single
    database transaction for atomicity.
    """

    def __init__(self, db_path: str) -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    tx_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    type TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    balance_after INTEGER NOT NULL,
                    reference TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS escrow (
                    escrow_id TEXT PRIMARY KEY,
                    payer_account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'locked',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_credit_reference
                    ON transactions(account_id, reference)
                    WHERE type = 'credit';

                CREATE UNIQUE INDEX IF NOT EXISTS ux_locked_escrow_task
                    ON escrow(payer_account_id, task_id)
                    WHERE status = 'locked';

                CREATE INDEX IF NOT EXISTS ix_transactions_account_timestamp_tx_id
                    ON transactions(account_id, timestamp, tx_id);
                """
            )
            self._db.commit()

    def _now(self) -> str:
        """Current UTC timestamp in ISO 8601 format."""
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _new_tx_id(self) -> str:
        """Generate a new transaction ID."""
        return f"tx-{uuid.uuid4()}"

    def _new_escrow_id(self) -> str:
        """Generate a new escrow ID."""
        return f"esc-{uuid.uuid4()}"

    def create_account(
        self,
        account_id: str,
        initial_balance: int,
    ) -> dict[str, object]:
        """
        Create a new account.

        Args:
            account_id: The agent_id from Identity service.
            initial_balance: Starting balance (>= 0).

        Returns:
            Account record dict.

        Raises:
            ServiceError: ACCOUNT_EXISTS if account already exists.
            ServiceError: INVALID_AMOUNT if initial_balance < 0.
        """
        if initial_balance < 0:
            raise ServiceError(
                "INVALID_AMOUNT",
                "Initial balance must be non-negative",
                400,
                {},
            )

        with self._lock:
            now = self._now()

            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    "INSERT INTO accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
                    (account_id, initial_balance, now),
                )
                if initial_balance > 0:
                    tx_id = self._new_tx_id()
                    self._db.execute(
                        "INSERT INTO transactions "
                        "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            tx_id,
                            account_id,
                            "credit",
                            initial_balance,
                            initial_balance,
                            "initial_balance",
                            now,
                        ),
                    )
                self._db.commit()
            except sqlite3.IntegrityError as exc:
                self._db.rollback()
                raise ServiceError(
                    "ACCOUNT_EXISTS",
                    "Account already exists for this agent",
                    409,
                    {},
                ) from exc
            except Exception:
                self._db.rollback()
                raise

            return {
                "account_id": account_id,
                "balance": initial_balance,
                "created_at": now,
            }

    def get_account(self, account_id: str) -> dict[str, object] | None:
        """Look up an account by ID. Returns None if not found."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT account_id, balance, created_at FROM accounts WHERE account_id = ?",
                (account_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {"account_id": row[0], "balance": row[1], "created_at": row[2]}

    def credit(
        self,
        account_id: str,
        amount: int,
        reference: str,
    ) -> dict[str, object]:
        """
        Add funds to an account.

        Args:
            account_id: Target account.
            amount: Positive integer.
            reference: Context string (e.g., "salary_round_3").

        Returns:
            {"tx_id": "...", "balance_after": N}

        Raises:
            ServiceError: ACCOUNT_NOT_FOUND, INVALID_AMOUNT.
        """
        if amount <= 0:
            raise ServiceError(
                "INVALID_AMOUNT",
                "Amount must be a positive integer",
                400,
                {},
            )

        with self._lock:
            now = self._now()
            tx_id = self._new_tx_id()

            try:
                self._db.execute("BEGIN IMMEDIATE")
                cursor = self._db.execute(
                    "UPDATE accounts SET balance = balance + ? WHERE account_id = ?",
                    (amount, account_id),
                )
                if cursor.rowcount == 0:
                    raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

                row = self._db.execute(
                    "SELECT balance FROM accounts WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                if row is None:
                    msg = "Account not found after update"
                    raise RuntimeError(msg)
                new_balance = cast("int", row[0])

                self._db.execute(
                    "INSERT INTO transactions "
                    "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (tx_id, account_id, "credit", amount, new_balance, reference, now),
                )
                self._db.commit()
            except sqlite3.IntegrityError as exc:
                self._db.rollback()
                existing = self._db.execute(
                    "SELECT tx_id, amount, balance_after FROM transactions "
                    "WHERE account_id = ? AND type = 'credit' AND reference = ?",
                    (account_id, reference),
                ).fetchone()
                if existing is None:
                    msg = "Duplicate credit detected but could not load existing transaction"
                    raise RuntimeError(msg) from exc

                existing_tx_id = cast("str", existing[0])
                existing_amount = cast("int", existing[1])
                existing_balance_after = cast("int", existing[2])

                if existing_amount != amount:
                    raise ServiceError(
                        "PAYLOAD_MISMATCH",
                        "Duplicate credit reference used with a different amount",
                        400,
                        {},
                    ) from exc

                return {"tx_id": existing_tx_id, "balance_after": existing_balance_after}
            except ServiceError:
                self._db.rollback()
                raise
            except Exception:
                self._db.rollback()
                raise

            return {"tx_id": tx_id, "balance_after": new_balance}

    def get_transactions(self, account_id: str) -> list[dict[str, object]]:
        """
        Get transaction history for an account.

        Raises:
            ServiceError: ACCOUNT_NOT_FOUND.
        """
        account = self.get_account(account_id)
        if account is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

        with self._lock:
            cursor = self._db.execute(
                "SELECT tx_id, type, amount, balance_after, reference, timestamp "
                "FROM transactions WHERE account_id = ? ORDER BY timestamp, tx_id",
                (account_id,),
            )
            return [
                {
                    "tx_id": row[0],
                    "type": row[1],
                    "amount": row[2],
                    "balance_after": row[3],
                    "reference": row[4],
                    "timestamp": row[5],
                }
                for row in cursor.fetchall()
            ]

    def escrow_lock(
        self,
        payer_account_id: str,
        amount: int,
        task_id: str,
    ) -> dict[str, object]:
        """
        Lock funds in escrow.

        Debits the payer account and creates an escrow record.
        All in a single transaction.

        Raises:
            ServiceError: ACCOUNT_NOT_FOUND, INVALID_AMOUNT, INSUFFICIENT_FUNDS.
        """
        if amount <= 0:
            raise ServiceError(
                "INVALID_AMOUNT",
                "Amount must be a positive integer",
                400,
                {},
            )

        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                existing = self._db.execute(
                    "SELECT escrow_id, amount, task_id FROM escrow "
                    "WHERE payer_account_id = ? AND task_id = ? AND status = 'locked'",
                    (payer_account_id, task_id),
                ).fetchone()
                if existing is not None:
                    existing_escrow_id = cast("str", existing[0])
                    existing_amount = cast("int", existing[1])
                    existing_task_id = cast("str", existing[2])

                    if existing_task_id != task_id or existing_amount != amount:
                        raise ServiceError(
                            "ESCROW_ALREADY_LOCKED",
                            "Escrow already locked for this task with a different amount",
                            409,
                            {},
                        )

                    self._db.rollback()
                    return {
                        "escrow_id": existing_escrow_id,
                        "amount": existing_amount,
                        "task_id": existing_task_id,
                        "status": "locked",
                    }

                now = self._now()
                tx_id = self._new_tx_id()
                escrow_id = self._new_escrow_id()

                cursor = self._db.execute(
                    "UPDATE accounts SET balance = balance - ? "
                    "WHERE account_id = ? AND balance >= ?",
                    (amount, payer_account_id, amount),
                )
                if cursor.rowcount == 0:
                    # Distinguish between not found and insufficient funds
                    account = self.get_account(payer_account_id)
                    if account is None:
                        raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})
                    raise ServiceError(
                        "INSUFFICIENT_FUNDS",
                        "Insufficient funds for escrow lock",
                        402,
                        {},
                    )

                row = self._db.execute(
                    "SELECT balance FROM accounts WHERE account_id = ?",
                    (payer_account_id,),
                ).fetchone()
                if row is None:
                    msg = "Account not found after update"
                    raise RuntimeError(msg)
                new_balance = cast("int", row[0])

                self._db.execute(
                    "INSERT INTO transactions "
                    "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (tx_id, payer_account_id, "escrow_lock", amount, new_balance, task_id, now),
                )
                self._db.execute(
                    "INSERT INTO escrow "
                    "(escrow_id, payer_account_id, amount, task_id, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (escrow_id, payer_account_id, amount, task_id, "locked", now),
                )
                self._db.commit()
            except sqlite3.IntegrityError as exc:
                self._db.rollback()
                existing = self._db.execute(
                    "SELECT escrow_id, amount, task_id FROM escrow "
                    "WHERE payer_account_id = ? AND task_id = ? AND status = 'locked'",
                    (payer_account_id, task_id),
                ).fetchone()
                if existing is None:
                    msg = "Duplicate escrow detected but could not load existing escrow"
                    raise RuntimeError(msg) from exc

                existing_escrow_id = cast("str", existing[0])
                existing_amount = cast("int", existing[1])
                existing_task_id = cast("str", existing[2])

                if existing_task_id != task_id or existing_amount != amount:
                    raise ServiceError(
                        "ESCROW_ALREADY_LOCKED",
                        "Escrow already locked for this task with a different amount",
                        409,
                        {},
                    ) from exc

                return {
                    "escrow_id": existing_escrow_id,
                    "amount": existing_amount,
                    "task_id": existing_task_id,
                    "status": "locked",
                }
            except ServiceError:
                self._db.rollback()
                raise
            except Exception:
                self._db.rollback()
                raise

            return {
                "escrow_id": escrow_id,
                "amount": amount,
                "task_id": task_id,
                "status": "locked",
            }

    def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
    ) -> dict[str, object]:
        """
        Release escrowed funds to a recipient.

        Raises:
            ServiceError: ESCROW_NOT_FOUND, ESCROW_ALREADY_RESOLVED, ACCOUNT_NOT_FOUND.
        """
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")

                escrow = self._get_escrow(escrow_id)
                if escrow is None:
                    raise ServiceError("ESCROW_NOT_FOUND", "Escrow not found", 404, {})
                if escrow["status"] != "locked":
                    raise ServiceError(
                        "ESCROW_ALREADY_RESOLVED",
                        "Escrow has already been resolved",
                        409,
                        {},
                    )

                amount = cast("int", escrow["amount"])
                now = self._now()
                tx_id = self._new_tx_id()

                cursor = self._db.execute(
                    "UPDATE accounts SET balance = balance + ? WHERE account_id = ?",
                    (amount, recipient_account_id),
                )
                if cursor.rowcount == 0:
                    raise ServiceError(
                        "ACCOUNT_NOT_FOUND",
                        "Recipient account not found",
                        404,
                        {},
                    )

                row = self._db.execute(
                    "SELECT balance FROM accounts WHERE account_id = ?",
                    (recipient_account_id,),
                ).fetchone()
                if row is None:
                    msg = "Recipient account not found after update"
                    raise RuntimeError(msg)
                new_balance = cast("int", row[0])

                self._db.execute(
                    "INSERT INTO transactions "
                    "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        tx_id,
                        recipient_account_id,
                        "escrow_release",
                        amount,
                        new_balance,
                        escrow_id,
                        now,
                    ),
                )
                escrow_cursor = self._db.execute(
                    "UPDATE escrow SET status = 'released', resolved_at = ? "
                    "WHERE escrow_id = ? AND status = 'locked'",
                    (now, escrow_id),
                )
                if escrow_cursor.rowcount != 1:
                    raise ServiceError(
                        "ESCROW_ALREADY_RESOLVED",
                        "Escrow has already been resolved",
                        409,
                        {},
                    )
                self._db.commit()
            except ServiceError:
                self._db.rollback()
                raise
            except Exception:
                self._db.rollback()
                raise

            return {
                "escrow_id": escrow_id,
                "status": "released",
                "recipient": recipient_account_id,
                "amount": amount,
            }

    def _credit_escrow_share(
        self,
        account_id: str,
        amount: int,
        escrow_id: str,
        now: str,
        role: str,
    ) -> None:
        """
        Credit an account with an escrow share and record a transaction.

        Expects to be called inside an open DB transaction.
        """
        if amount <= 0:
            return

        tx_id = self._new_tx_id()
        cursor = self._db.execute(
            "UPDATE accounts SET balance = balance + ? WHERE account_id = ?",
            (amount, account_id),
        )
        if cursor.rowcount == 0:
            raise ServiceError(
                "ACCOUNT_NOT_FOUND",
                f"{role} account not found",
                404,
                {},
            )

        row = self._db.execute(
            "SELECT balance FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            msg = f"{role} account not found after update"
            raise RuntimeError(msg)
        new_balance = cast("int", row[0])

        self._db.execute(
            "INSERT INTO transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                tx_id,
                account_id,
                "escrow_release",
                amount,
                new_balance,
                escrow_id,
                now,
            ),
        )

    def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
    ) -> dict[str, object]:
        """
        Split escrowed funds between worker and poster.

        Worker gets floor(amount * worker_pct / 100), poster gets remainder.

        Raises:
            ServiceError: ESCROW_NOT_FOUND, ESCROW_ALREADY_RESOLVED, ACCOUNT_NOT_FOUND,
                INVALID_AMOUNT if worker_pct not in 0..100.
        """
        if not (0 <= worker_pct <= 100):
            raise ServiceError(
                "INVALID_AMOUNT",
                "worker_pct must be between 0 and 100",
                400,
                {},
            )

        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")

                escrow = self._get_escrow(escrow_id)
                if escrow is None:
                    raise ServiceError("ESCROW_NOT_FOUND", "Escrow not found", 404, {})
                if escrow["status"] != "locked":
                    raise ServiceError(
                        "ESCROW_ALREADY_RESOLVED",
                        "Escrow has already been resolved",
                        409,
                        {},
                    )

                payer_account_id = cast("str", escrow["payer_account_id"])
                if poster_account_id != payer_account_id:
                    raise ServiceError(
                        "PAYLOAD_MISMATCH",
                        "poster_account_id must match the escrow payer_account_id",
                        400,
                        {},
                    )

                total_amount = cast("int", escrow["amount"])
                worker_amount = total_amount * worker_pct // 100
                poster_amount = total_amount - worker_amount

                now = self._now()

                self._credit_escrow_share(
                    worker_account_id,
                    worker_amount,
                    escrow_id,
                    now,
                    "Worker",
                )
                self._credit_escrow_share(
                    poster_account_id,
                    poster_amount,
                    escrow_id,
                    now,
                    "Poster",
                )

                escrow_cursor = self._db.execute(
                    "UPDATE escrow SET status = 'split', resolved_at = ? "
                    "WHERE escrow_id = ? AND status = 'locked'",
                    (now, escrow_id),
                )
                if escrow_cursor.rowcount != 1:
                    raise ServiceError(
                        "ESCROW_ALREADY_RESOLVED",
                        "Escrow has already been resolved",
                        409,
                        {},
                    )
                self._db.commit()
            except ServiceError:
                self._db.rollback()
                raise
            except Exception:
                self._db.rollback()
                raise

            return {
                "escrow_id": escrow_id,
                "status": "split",
                "worker_amount": worker_amount,
                "poster_amount": poster_amount,
            }

    def count_accounts(self) -> int:
        """Count total accounts."""
        with self._lock:
            cursor = self._db.execute("SELECT COUNT(*) FROM accounts")
            result = cursor.fetchone()
            if result is None:
                return 0
            return int(result[0])

    def total_escrowed(self) -> int:
        """Sum of all locked (unresolved) escrow amounts."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM escrow WHERE status = 'locked'"
            )
            result = cursor.fetchone()
            if result is None:
                return 0
            return int(result[0])

    def _get_escrow(self, escrow_id: str) -> dict[str, object] | None:
        """Look up an escrow record."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT escrow_id, payer_account_id, amount, task_id, status, created_at, "
                "resolved_at FROM escrow WHERE escrow_id = ?",
                (escrow_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "escrow_id": row[0],
                "payer_account_id": row[1],
                "amount": row[2],
                "task_id": row[3],
                "status": row[4],
                "created_at": row[5],
                "resolved_at": row[6],
            }

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._db.close()
