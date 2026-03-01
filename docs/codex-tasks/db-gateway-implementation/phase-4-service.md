# Phase 4 — Service Layer

## Working Directory

All paths relative to `services/db-gateway/`.

---

## File 1: `src/db_gateway_service/services/db_writer.py`

Create this file. This is the core of the service — the SQLite transaction executor.

```python
"""Database writer — SQLite transaction executor for the Database Gateway."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from service_commons.exceptions import ServiceError


# Allowed columns for task status updates (whitelist)
TASK_UPDATE_COLUMNS: frozenset[str] = frozenset({
    "status",
    "worker_id",
    "accepted_bid_id",
    "accepted_at",
    "execution_deadline",
    "submitted_at",
    "review_deadline",
    "approved_at",
    "cancelled_at",
    "dispute_reason",
    "disputed_at",
    "ruling_id",
    "worker_pct",
    "ruling_summary",
    "ruled_at",
    "expired_at",
})


class DbWriter:
    """
    SQLite transaction executor.

    Each public method maps to one API endpoint. Every method:
    1. Opens a BEGIN IMMEDIATE transaction
    2. Executes the domain write(s)
    3. Inserts an event row
    4. Commits (or rolls back on error)

    No business logic. Database constraints are the safety net.
    """

    def __init__(
        self,
        db_path: str,
        busy_timeout_ms: int,
        journal_mode: str,
        schema_sql: str | None,
    ) -> None:
        self._db_path = db_path
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(f"PRAGMA journal_mode={journal_mode}")
        self._db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._db.execute("PRAGMA foreign_keys=ON")

        if schema_sql is not None:
            self._init_schema(schema_sql)

    def _init_schema(self, schema_sql: str) -> None:
        """Initialize database schema from SQL file (idempotent)."""
        try:
            self._db.executescript(schema_sql)
        except sqlite3.OperationalError:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _insert_event(self, cursor: sqlite3.Cursor, event: dict[str, Any]) -> int:
        """Insert an event row and return the event_id."""
        cursor.execute(
            "INSERT INTO events "
            "(event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event["event_source"],
                event["event_type"],
                event["timestamp"],
                event.get("task_id"),
                event.get("agent_id"),
                event["summary"],
                event["payload"],
            ),
        )
        return cursor.lastrowid or 0

    def get_database_size_bytes(self) -> int:
        """Get the size of the database file in bytes."""
        try:
            return os.path.getsize(self._db_path)
        except OSError:
            return 0

    def get_total_events(self) -> int:
        """Count total rows in the events table."""
        cursor = self._db.execute("SELECT COUNT(*) FROM events")
        row = cursor.fetchone()
        if row is None:
            return 0
        return int(row[0])

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def register_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Register a new agent.

        INSERT INTO identity_agents + INSERT INTO events.
        Idempotency: UNIQUE on public_key.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
                "VALUES (?, ?, ?, ?)",
                (data["agent_id"], data["name"], data["public_key"], data["registered_at"]),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"agent_id": data["agent_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "unique" in error_msg and "public_key" in error_msg:
                # Check for idempotent replay
                existing = self._lookup_agent_by_public_key(data["public_key"])
                if existing is not None and self._agent_matches(existing, data):
                    return {"agent_id": existing["agent_id"], "event_id": 0}
                raise ServiceError(
                    "PUBLIC_KEY_EXISTS",
                    "This public key is already registered",
                    409,
                    {},
                ) from exc
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "PUBLIC_KEY_EXISTS",
                "This public key is already registered",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def _lookup_agent_by_public_key(self, public_key: str) -> dict[str, str] | None:
        """Look up an agent by public key."""
        cursor = self._db.execute(
            "SELECT agent_id, name, public_key, registered_at "
            "FROM identity_agents WHERE public_key = ?",
            (public_key,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "agent_id": row[0],
            "name": row[1],
            "public_key": row[2],
            "registered_at": row[3],
        }

    def _agent_matches(self, existing: dict[str, str], data: dict[str, Any]) -> bool:
        """Check if all agent fields match for idempotency."""
        return (
            existing["name"] == data["name"]
            and existing["registered_at"] == data["registered_at"]
        )

    # ------------------------------------------------------------------
    # Bank — Accounts
    # ------------------------------------------------------------------

    def create_account(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a bank account with optional initial credit.

        INSERT INTO bank_accounts + optional INSERT INTO bank_transactions + INSERT INTO events.
        Idempotency: PK on account_id.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
                (data["account_id"], data["balance"], data["created_at"]),
            )
            # Optional initial credit transaction
            initial_credit = data.get("initial_credit")
            if initial_credit is not None:
                cursor.execute(
                    "INSERT INTO bank_transactions "
                    "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                    "VALUES (?, ?, 'credit', ?, ?, ?, ?)",
                    (
                        initial_credit["tx_id"],
                        data["account_id"],
                        initial_credit["amount"],
                        data["balance"],
                        initial_credit["reference"],
                        initial_credit["timestamp"],
                    ),
                )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"account_id": data["account_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            # PK violation — check idempotency
            existing = self._lookup_account(data["account_id"])
            if existing is not None and existing["balance"] == data["balance"]:
                return {"account_id": data["account_id"], "event_id": 0}
            raise ServiceError(
                "ACCOUNT_EXISTS",
                "Account already exists for this agent",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def _lookup_account(self, account_id: str) -> dict[str, Any] | None:
        """Look up an account by ID."""
        cursor = self._db.execute(
            "SELECT account_id, balance, created_at FROM bank_accounts WHERE account_id = ?",
            (account_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"account_id": row[0], "balance": row[1], "created_at": row[2]}

    # ------------------------------------------------------------------
    # Bank — Credit
    # ------------------------------------------------------------------

    def credit_account(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Credit an account.

        UPDATE bank_accounts + INSERT INTO bank_transactions + INSERT INTO events.
        Idempotency: idx_bank_tx_idempotent on (account_id, reference) WHERE type='credit'.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ?",
                (data["amount"], data["account_id"]),
            )
            if cursor.rowcount == 0:
                self._db.rollback()
                raise ServiceError(
                    "ACCOUNT_NOT_FOUND", "No account with this account_id", 404, {}
                )
            cursor.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                "VALUES (?, ?, 'credit', ?, "
                "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?)",
                (
                    data["tx_id"],
                    data["account_id"],
                    data["amount"],
                    data["account_id"],
                    data["reference"],
                    data["timestamp"],
                ),
            )
            balance_cursor = self._db.execute(
                "SELECT balance FROM bank_accounts WHERE account_id = ?",
                (data["account_id"],),
            )
            balance_row = balance_cursor.fetchone()
            balance_after = int(balance_row[0]) if balance_row else 0
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {
                "tx_id": data["tx_id"],
                "balance_after": balance_after,
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "unique" in error_msg:
                # Idempotency check — same (account_id, reference) for credit
                existing = self._lookup_credit_tx(data["account_id"], data["reference"])
                if existing is not None and existing["amount"] == data["amount"]:
                    return {
                        "tx_id": existing["tx_id"],
                        "balance_after": existing["balance_after"],
                        "event_id": 0,
                    }
                raise ServiceError(
                    "REFERENCE_CONFLICT",
                    "Same (account_id, reference) exists with different amount",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "REFERENCE_CONFLICT",
                "Transaction constraint violation",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def _lookup_credit_tx(
        self, account_id: str, reference: str
    ) -> dict[str, Any] | None:
        """Look up an existing credit transaction by (account_id, reference)."""
        cursor = self._db.execute(
            "SELECT tx_id, amount, balance_after FROM bank_transactions "
            "WHERE account_id = ? AND reference = ? AND type = 'credit'",
            (account_id, reference),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"tx_id": row[0], "amount": row[1], "balance_after": row[2]}

    # ------------------------------------------------------------------
    # Bank — Escrow Lock
    # ------------------------------------------------------------------

    def escrow_lock(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Lock funds in escrow.

        UPDATE bank_accounts (debit) + INSERT INTO bank_escrow +
        INSERT INTO bank_transactions + INSERT INTO events.
        """
        cursor = self._db.cursor()
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
                self._db.rollback()
                if acct is None:
                    raise ServiceError(
                        "ACCOUNT_NOT_FOUND", "No account for payer_account_id", 404, {}
                    )
                raise ServiceError(
                    "INSUFFICIENT_FUNDS",
                    "Account balance is less than the escrow amount",
                    402,
                    {},
                )
            # Create escrow record
            cursor.execute(
                "INSERT INTO bank_escrow "
                "(escrow_id, payer_account_id, amount, task_id, status, created_at) "
                "VALUES (?, ?, ?, ?, 'locked', ?)",
                (
                    data["escrow_id"],
                    data["payer_account_id"],
                    data["amount"],
                    data["task_id"],
                    data["created_at"],
                ),
            )
            # Log escrow_lock transaction
            cursor.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                "VALUES (?, ?, 'escrow_lock', ?, "
                "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?)",
                (
                    data["tx_id"],
                    data["payer_account_id"],
                    data["amount"],
                    data["payer_account_id"],
                    data["task_id"],
                    data["created_at"],
                ),
            )
            # Get balance after debit
            balance_cursor = self._db.execute(
                "SELECT balance FROM bank_accounts WHERE account_id = ?",
                (data["payer_account_id"],),
            )
            balance_row = balance_cursor.fetchone()
            balance_after = int(balance_row[0]) if balance_row else 0
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {
                "escrow_id": data["escrow_id"],
                "balance_after": balance_after,
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            if "unique" in error_msg:
                # Idempotency: check existing escrow
                existing = self._lookup_active_escrow(
                    data["payer_account_id"], data["task_id"]
                )
                if existing is not None and existing["amount"] == data["amount"]:
                    return {
                        "escrow_id": existing["escrow_id"],
                        "balance_after": 0,
                        "event_id": 0,
                    }
                raise ServiceError(
                    "ESCROW_ALREADY_LOCKED",
                    "Escrow already locked for this (payer, task) pair",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "ESCROW_ALREADY_LOCKED",
                "Escrow constraint violation",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def _lookup_active_escrow(
        self, payer_account_id: str, task_id: str
    ) -> dict[str, Any] | None:
        """Look up an active (locked) escrow."""
        cursor = self._db.execute(
            "SELECT escrow_id, amount FROM bank_escrow "
            "WHERE payer_account_id = ? AND task_id = ? AND status = 'locked'",
            (payer_account_id, task_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"escrow_id": row[0], "amount": row[1]}

    # ------------------------------------------------------------------
    # Bank — Escrow Release
    # ------------------------------------------------------------------

    def escrow_release(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Release escrowed funds to a recipient.

        SELECT escrow + UPDATE bank_accounts (credit) +
        INSERT INTO bank_transactions + UPDATE bank_escrow + INSERT INTO events.
        """
        cursor = self._db.cursor()
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
                self._db.rollback()
                raise ServiceError(
                    "ACCOUNT_NOT_FOUND", "Recipient account not found", 404, {}
                )
            # Log escrow_release transaction
            cursor.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                "VALUES (?, ?, 'escrow_release', ?, "
                "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?)",
                (
                    data["tx_id"],
                    data["recipient_account_id"],
                    escrow["amount"],
                    data["recipient_account_id"],
                    data["escrow_id"],
                    data["resolved_at"],
                ),
            )
            # Resolve escrow
            cursor.execute(
                "UPDATE bank_escrow SET status = 'released', resolved_at = ? "
                "WHERE escrow_id = ?",
                (data["resolved_at"], data["escrow_id"]),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
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
            self._db.rollback()
            raise

    def _load_escrow(self, cursor: sqlite3.Cursor, escrow_id: str) -> dict[str, Any]:
        """Load an escrow and verify it is locked. Raises on not found or already resolved."""
        cursor.execute(
            "SELECT escrow_id, amount, status FROM bank_escrow WHERE escrow_id = ?",
            (escrow_id,),
        )
        row = cursor.fetchone()
        if row is None:
            self._db.rollback()
            raise ServiceError("ESCROW_NOT_FOUND", "No escrow with this ID", 404, {})
        if row[2] != "locked":
            self._db.rollback()
            raise ServiceError(
                "ESCROW_ALREADY_RESOLVED",
                "Escrow has already been released or split",
                409,
                {},
            )
        return {"escrow_id": row[0], "amount": row[1], "status": row[2]}

    # ------------------------------------------------------------------
    # Bank — Escrow Split
    # ------------------------------------------------------------------

    def escrow_split(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Split escrowed funds between worker and poster.

        Validates amounts sum to escrow amount. Credits both accounts
        (skipping zero-amount shares). Resolves escrow as 'split'.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            # Load and verify escrow
            escrow = self._load_escrow(cursor, data["escrow_id"])
            # Validate amounts sum
            worker_amount = data["worker_amount"]
            poster_amount = data["poster_amount"]
            if worker_amount + poster_amount != escrow["amount"]:
                self._db.rollback()
                raise ServiceError(
                    "AMOUNT_MISMATCH",
                    "worker_amount + poster_amount does not equal escrow amount",
                    400,
                    {},
                )
            # Credit worker (if amount > 0)
            if worker_amount > 0:
                cursor.execute(
                    "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ?",
                    (worker_amount, data["worker_account_id"]),
                )
                if cursor.rowcount == 0:
                    self._db.rollback()
                    raise ServiceError(
                        "ACCOUNT_NOT_FOUND", "Worker account not found", 404, {}
                    )
                cursor.execute(
                    "INSERT INTO bank_transactions "
                    "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                    "VALUES (?, ?, 'escrow_release', ?, "
                    "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?)",
                    (
                        data["worker_tx_id"],
                        data["worker_account_id"],
                        worker_amount,
                        data["worker_account_id"],
                        data["escrow_id"],
                        data["resolved_at"],
                    ),
                )
            # Credit poster (if amount > 0)
            if poster_amount > 0:
                cursor.execute(
                    "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ?",
                    (poster_amount, data["poster_account_id"]),
                )
                if cursor.rowcount == 0:
                    self._db.rollback()
                    raise ServiceError(
                        "ACCOUNT_NOT_FOUND", "Poster account not found", 404, {}
                    )
                cursor.execute(
                    "INSERT INTO bank_transactions "
                    "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                    "VALUES (?, ?, 'escrow_release', ?, "
                    "(SELECT balance FROM bank_accounts WHERE account_id = ?), ?, ?)",
                    (
                        data["poster_tx_id"],
                        data["poster_account_id"],
                        poster_amount,
                        data["poster_account_id"],
                        data["escrow_id"],
                        data["resolved_at"],
                    ),
                )
            # Resolve escrow
            cursor.execute(
                "UPDATE bank_escrow SET status = 'split', resolved_at = ? "
                "WHERE escrow_id = ?",
                (data["resolved_at"], data["escrow_id"]),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
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
            self._db.rollback()
            raise

    # ------------------------------------------------------------------
    # Board — Tasks
    # ------------------------------------------------------------------

    def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new task.

        INSERT INTO board_tasks + INSERT INTO events.
        Idempotency: PK on task_id.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO board_tasks "
                "(task_id, poster_id, title, spec, reward, status, "
                "bidding_deadline_seconds, deadline_seconds, review_deadline_seconds, "
                "bidding_deadline, escrow_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["task_id"],
                    data["poster_id"],
                    data["title"],
                    data["spec"],
                    data["reward"],
                    data["status"],
                    data["bidding_deadline_seconds"],
                    data["deadline_seconds"],
                    data["review_deadline_seconds"],
                    data["bidding_deadline"],
                    data["escrow_id"],
                    data["created_at"],
                ),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"task_id": data["task_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "TASK_EXISTS",
                "Task with this task_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    # ------------------------------------------------------------------
    # Board — Bids
    # ------------------------------------------------------------------

    def submit_bid(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Submit a bid on a task.

        INSERT INTO board_bids + INSERT INTO events.
        Idempotency: idx_board_bids_one_per_agent on (task_id, bidder_id).
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data["bid_id"],
                    data["task_id"],
                    data["bidder_id"],
                    data["proposal"],
                    data["submitted_at"],
                ),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"bid_id": data["bid_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "BID_EXISTS",
                "This agent already bid on this task",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    # ------------------------------------------------------------------
    # Board — Task Status Update
    # ------------------------------------------------------------------

    def update_task_status(
        self, task_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Update task status and associated fields.

        Dynamically builds SET clause from allowed columns.
        Does NOT validate status transitions — caller is responsible.
        """
        updates = data["updates"]
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            # Build dynamic SET clause from whitelist
            set_parts: list[str] = []
            values: list[Any] = []
            for col, val in updates.items():
                if col not in TASK_UPDATE_COLUMNS:
                    self._db.rollback()
                    raise ServiceError(
                        "INVALID_FIELD",
                        f"Unknown column: {col}",
                        400,
                        {"field": col},
                    )
                set_parts.append(f"{col} = ?")
                values.append(val)

            set_clause = ", ".join(set_parts)
            values.append(task_id)
            cursor.execute(
                f"UPDATE board_tasks SET {set_clause} WHERE task_id = ?",  # noqa: S608
                values,
            )
            if cursor.rowcount == 0:
                self._db.rollback()
                raise ServiceError(
                    "TASK_NOT_FOUND", "No task with this task_id", 404, {}
                )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            new_status = updates.get("status", "")
            return {
                "task_id": task_id,
                "status": new_status,
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except Exception:
            self._db.rollback()
            raise

    # ------------------------------------------------------------------
    # Board — Assets
    # ------------------------------------------------------------------

    def record_asset(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Record an asset upload (metadata only).

        INSERT INTO board_assets + INSERT INTO events.
        Idempotency: PK on asset_id.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO board_assets "
                "(asset_id, task_id, uploader_id, filename, content_type, "
                "size_bytes, storage_path, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["asset_id"],
                    data["task_id"],
                    data["uploader_id"],
                    data["filename"],
                    data["content_type"],
                    data["size_bytes"],
                    data["storage_path"],
                    data["uploaded_at"],
                ),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"asset_id": data["asset_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "ASSET_EXISTS",
                "Asset with this asset_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    # ------------------------------------------------------------------
    # Reputation — Feedback
    # ------------------------------------------------------------------

    def submit_feedback(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Submit feedback with optional mutual reveal.

        INSERT INTO reputation_feedback + optional UPDATE for reverse reveal +
        INSERT INTO events.
        """
        reveal = data.get("reveal_reverse", False)
        visible = 1 if reveal else 0
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO reputation_feedback "
                "(feedback_id, task_id, from_agent_id, to_agent_id, role, "
                "category, rating, comment, submitted_at, visible) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["feedback_id"],
                    data["task_id"],
                    data["from_agent_id"],
                    data["to_agent_id"],
                    data["role"],
                    data["category"],
                    data["rating"],
                    data.get("comment"),
                    data["submitted_at"],
                    visible,
                ),
            )
            # Reveal reverse feedback if requested
            if reveal:
                reverse_id = data.get("reverse_feedback_id")
                if reverse_id is not None:
                    cursor.execute(
                        "UPDATE reputation_feedback SET visible = 1 "
                        "WHERE feedback_id = ?",
                        (reverse_id,),
                    )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {
                "feedback_id": data["feedback_id"],
                "visible": reveal,
                "event_id": event_id,
            }
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "FEEDBACK_EXISTS",
                "Feedback already submitted for this (task, from, to) triple",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    # ------------------------------------------------------------------
    # Court — Claims
    # ------------------------------------------------------------------

    def file_claim(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        File a dispute claim.

        INSERT INTO court_claims + INSERT INTO events.
        Idempotency: PK on claim_id.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO court_claims "
                "(claim_id, task_id, claimant_id, respondent_id, reason, status, filed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    data["claim_id"],
                    data["task_id"],
                    data["claimant_id"],
                    data["respondent_id"],
                    data["reason"],
                    data["status"],
                    data["filed_at"],
                ),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"claim_id": data["claim_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "CLAIM_EXISTS",
                "Claim with this claim_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    # ------------------------------------------------------------------
    # Court — Rebuttals
    # ------------------------------------------------------------------

    def submit_rebuttal(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Submit a rebuttal with optional claim status update.

        INSERT INTO court_rebuttals + optional UPDATE court_claims + INSERT INTO events.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO court_rebuttals "
                "(rebuttal_id, claim_id, agent_id, content, submitted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data["rebuttal_id"],
                    data["claim_id"],
                    data["agent_id"],
                    data["content"],
                    data["submitted_at"],
                ),
            )
            # Optional claim status update
            claim_status = data.get("claim_status_update")
            if claim_status is not None:
                cursor.execute(
                    "UPDATE court_claims SET status = ? WHERE claim_id = ?",
                    (claim_status, data["claim_id"]),
                )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"rebuttal_id": data["rebuttal_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "REBUTTAL_EXISTS",
                "Rebuttal with this rebuttal_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    # ------------------------------------------------------------------
    # Court — Rulings
    # ------------------------------------------------------------------

    def record_ruling(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Record a court ruling with optional claim status update.

        INSERT INTO court_rulings + optional UPDATE court_claims + INSERT INTO events.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO court_rulings "
                "(ruling_id, claim_id, task_id, worker_pct, summary, judge_votes, ruled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    data["ruling_id"],
                    data["claim_id"],
                    data["task_id"],
                    data["worker_pct"],
                    data["summary"],
                    data["judge_votes"],
                    data["ruled_at"],
                ),
            )
            # Optional claim status update
            claim_status = data.get("claim_status_update")
            if claim_status is not None:
                cursor.execute(
                    "UPDATE court_claims SET status = ? WHERE claim_id = ?",
                    (claim_status, data["claim_id"]),
                )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"ruling_id": data["ruling_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            error_msg = str(exc).lower()
            if "foreign" in error_msg:
                raise ServiceError(
                    "FOREIGN_KEY_VIOLATION",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "RULING_EXISTS",
                "Ruling with this ruling_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise
```

---

## File 2: `src/db_gateway_service/services/__init__.py`

Overwrite the existing empty file with:

```python
"""Service layer components."""

from db_gateway_service.services.db_writer import DbWriter

__all__ = ["DbWriter"]
```

---

## Verification

```bash
cd services/db-gateway && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.
