"""Database writer — SQLite transaction executor for the Database Gateway."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from service_commons.exceptions import ServiceError

# Allowed columns for task status updates (whitelist)
TASK_UPDATE_COLUMNS: frozenset[str] = frozenset(
    {
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
        "dispute_id",
        "rebuttal_deadline",
        "rebuttal_submitted_at",
        "disputed_at",
        "ruling_id",
        "worker_pct",
        "ruling_summary",
        "ruled_at",
        "expired_at",
        "escrow_pending",
    }
)


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
        self._db.row_factory = sqlite3.Row
        self._db.execute(f"PRAGMA journal_mode={journal_mode}")
        self._db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._db.execute("PRAGMA foreign_keys=ON")

        if schema_sql is not None:
            self._init_schema(schema_sql)

    def _init_schema(self, schema_sql: str) -> None:
        """Initialize database schema from SQL file.

        The schema script is not re-runnable — its CREATE statements carry no
        IF NOT EXISTS — so it runs only against a database with no tables yet.
        Errors are never suppressed: a broken or missing schema kills startup.
        """
        if self._schema_is_absent():
            self._db.executescript(schema_sql)
        self._run_migrations()

    def _schema_is_absent(self) -> bool:
        """Report whether the database holds no application tables yet."""
        cursor = self._db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        row = cursor.fetchone()
        return int(row[0]) == 0

    def _run_migrations(self) -> None:
        """Add columns to databases created before those columns existed.

        The schema script is a no-op on an existing database, so every column added
        after a deployment must be introduced here. Failures are deliberately not
        suppressed: without these columns disputes and event provenance fail at runtime.
        """
        self._add_column_if_missing("board_tasks", "dispute_id", "TEXT")
        self._add_column_if_missing("board_tasks", "rebuttal_deadline", "TEXT")
        self._add_column_if_missing("board_tasks", "rebuttal_submitted_at", "TEXT")
        self._add_column_if_missing("bank_transactions", "event_id", "INTEGER")
        self._add_column_if_missing("bank_escrow", "event_id", "INTEGER")

    def _add_column_if_missing(self, table: str, column: str, decl: str) -> None:
        """Add one column to an existing table. No-op when the table or column is absent."""
        for identifier in (table, column, decl):
            if not identifier.isidentifier():
                raise ServiceError(
                    "invalid_migration",
                    "Migration identifiers must be valid identifiers",
                    500,
                    {"table": table, "column": column},
                )
        columns = self._db.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608
        if len(columns) == 0:
            return
        if any(row["name"] == column for row in columns):
            return
        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")  # nosec B608
        self._db.commit()

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

    def _lookup_registration_event_id(
        self,
        agent_id: str,
        event_source: str,
        event_type: str,
    ) -> int | None:
        """Find the event emitted when this agent first registered.

        An agent registers exactly once, so its earliest registration event is the
        original. The *existing* agent_id must be passed, never the replayed payload's:
        a replay mints a fresh agent_id that was never written to the events table.
        Returns None for a legacy row whose registration predates the events table.
        """
        cursor = self._db.execute(
            "SELECT MIN(event_id) FROM events "
            "WHERE agent_id = ? AND event_source = ? AND event_type = ?",
            (agent_id, event_source, event_type),
        )
        row = cursor.fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def _compile_constraints(
        self,
        table: str,
        pk_column: str,
        pk_value: str,
        constraints: dict[str, Any],
    ) -> tuple[str, list[Any]]:
        """Compile constraints into a WHERE clause for UPDATE statements."""
        where_parts = [f"{pk_column} = ?"]
        params: list[Any] = [pk_value]
        for column, expected in constraints.items():
            if not column.isidentifier():
                raise ServiceError(
                    "invalid_constraints",
                    "Constraint column names must be valid identifiers",
                    400,
                    {"table": table, "column": column},
                )
            where_parts.append(f"{column} = ?")
            params.append(expected)
        return " AND ".join(where_parts), params

    def _check_constraint_violation(
        self,
        cursor: sqlite3.Cursor,
        table: str,
        pk_column: str,
        pk_value: str,
        constraints: dict[str, Any],
    ) -> None:
        """Query actual values after a 0-rowcount update and raise a descriptive error."""
        row = cursor.execute(
            f"SELECT * FROM {table} WHERE {pk_column} = ?",  # nosec B608
            (pk_value,),
        ).fetchone()
        if row is None:
            raise ServiceError(
                error="not_found",
                message=f"No {table} row with {pk_column}={pk_value}",
                status_code=404,
                details={"table": table, pk_column: pk_value},
            )

        row_dict = dict(row)
        for column, expected in constraints.items():
            actual = row_dict.get(column)
            if str(actual) != str(expected):
                raise ServiceError(
                    error="constraint_violation",
                    message=f"Expected {column}='{expected}' but found {column}='{actual}'",
                    status_code=409,
                    details={
                        "table": table,
                        "constraint": column,
                        "expected": str(expected),
                        "actual": str(actual),
                    },
                )

    def _verify_cross_table_constraint(
        self,
        cursor: sqlite3.Cursor,
        table: str,
        conditions: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify cross-table preconditions with a SELECT lookup."""
        if len(conditions) == 0:
            raise ServiceError(
                "invalid_constraints",
                "Cross-table constraints cannot be empty",
                400,
                {"table": table},
            )
        where_parts: list[str] = []
        params: list[Any] = []
        for column, expected in conditions.items():
            if not column.isidentifier():
                raise ServiceError(
                    "invalid_constraints",
                    "Constraint column names must be valid identifiers",
                    400,
                    {"table": table, "column": column},
                )
            where_parts.append(f"{column} = ?")
            params.append(expected)
        where_clause = " AND ".join(where_parts)
        row = cursor.execute(
            f"SELECT * FROM {table} WHERE {where_clause}",  # nosec B608
            params,
        ).fetchone()
        if row is None:
            raise ServiceError(
                error="constraint_violation",
                message=f"Cross-table constraint failed on {table}",
                status_code=409,
                details={"table": table, "conditions": conditions},
            )
        return dict(row)

    @property
    def connection(self) -> sqlite3.Connection:
        """The single SQLite connection shared with DbReader.

        Single-writer invariant (GAP-C5, ADR pending in WP-12): the gateway keeps
        exactly one connection for the whole process and hands the same object to
        DbReader instead of opening a second one. That is safe only because every
        write method above is a plain (non-async) `def` that runs its
        `BEGIN IMMEDIATE` through `COMMIT`/`ROLLBACK` to completion without
        `await`ing anything in between — see
        tests/architecture/test_gap_c5_single_writer_invariant.py, which fails if
        that invariant is ever broken. Do not `await` between a write method's
        `BEGIN IMMEDIATE` and its `COMMIT`/`ROLLBACK`.
        """
        return self._db

    def get_database_size_bytes(self) -> int:
        """Get the on-disk size of the database, including WAL-mode sidecar files.

        In WAL journal mode, recently committed data lives in the `-wal` file (and
        the `-shm` index) until the next checkpoint — stat'ing only the main file
        undercounts the database's real footprint.
        """
        total = Path(self._db_path).stat().st_size
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self._db_path}{suffix}")
            if sidecar.exists():
                total += sidecar.stat().st_size
        return total

    @staticmethod
    def _is_foreign_key_violation(exc: sqlite3.IntegrityError) -> bool:
        """True when an IntegrityError is a FOREIGN KEY constraint failure.

        Classified via the driver's structured error code (GAP-C7), never by
        matching substrings in the exception's message text — that text embeds
        real table/column names straight from the schema (SEC-02).
        """
        return exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY

    @staticmethod
    def _is_unique_violation(exc: sqlite3.IntegrityError) -> bool:
        """True for a UNIQUE or PRIMARY KEY constraint failure (both report as
        SQLITE_CONSTRAINT_UNIQUE / SQLITE_CONSTRAINT_PRIMARYKEY; the driver's
        message text for both says "UNIQUE constraint failed", which is exactly
        the substring the old code matched on)."""
        return exc.sqlite_errorcode in (
            sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
        )

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
            # identity_agents has exactly one non-PK UNIQUE constraint (public_key),
            # so SQLITE_CONSTRAINT_UNIQUE unambiguously means a public_key collision.
            if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                # Check for idempotent replay
                existing = self._lookup_agent_by_public_key(data["public_key"])
                if existing is not None and self._agent_matches(existing, data):
                    return {
                        "agent_id": existing["agent_id"],
                        "event_id": self._lookup_registration_event_id(
                            existing["agent_id"],
                            data["event"]["event_source"],
                            data["event"]["event_type"],
                        ),
                    }
                raise ServiceError(
                    "public_key_exists",
                    "This public key is already registered",
                    409,
                    {},
                ) from exc
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "public_key_exists",
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
        name_match: bool = existing["name"] == data["name"]
        time_match: bool = existing["registered_at"] == data["registered_at"]
        return name_match and time_match

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
            event_id = self._insert_event(cursor, data["event"])
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
            self._db.commit()
            return {"account_id": data["account_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            if self._is_foreign_key_violation(exc):
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
                raise ServiceError("account_not_found", "No account with this account_id", 404, {})
            # The event is written first so the transaction row can name it.
            event_id = self._insert_event(cursor, data["event"])
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
            balance_cursor = self._db.execute(
                "SELECT balance FROM bank_accounts WHERE account_id = ?",
                (data["account_id"],),
            )
            balance_row = balance_cursor.fetchone()
            balance_after = int(balance_row[0]) if balance_row else 0
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
            if self._is_unique_violation(exc):
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
            self._db.rollback()
            raise

    def _lookup_credit_tx(self, account_id: str, reference: str) -> dict[str, Any] | None:
        """Look up an existing credit transaction by (account_id, reference).

        event_id is None for a legacy row written before the column existed.
        """
        cursor = self._db.execute(
            "SELECT tx_id, amount, balance_after, event_id FROM bank_transactions "
            "WHERE account_id = ? AND reference = ? AND type = 'credit'",
            (account_id, reference),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"tx_id": row[0], "amount": row[1], "balance_after": row[2], "event_id": row[3]}

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
                        "account_not_found", "No account for payer_account_id", 404, {}
                    )
                raise ServiceError(
                    "insufficient_funds",
                    "Account balance is less than the escrow amount",
                    402,
                    {},
                )
            # The event is written first so the escrow and transaction rows can name it.
            event_id = self._insert_event(cursor, data["event"])
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
            balance_cursor = self._db.execute(
                "SELECT balance FROM bank_accounts WHERE account_id = ?",
                (data["payer_account_id"],),
            )
            balance_row = balance_cursor.fetchone()
            balance_after = int(balance_row[0]) if balance_row else 0
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
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            if self._is_unique_violation(exc):
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
            self._db.rollback()
            raise

    def _lookup_active_escrow(self, payer_account_id: str, task_id: str) -> dict[str, Any] | None:
        """Look up an active (locked) escrow.

        event_id is None for a legacy row written before the column existed.
        """
        cursor = self._db.execute(
            "SELECT escrow_id, amount, event_id FROM bank_escrow "
            "WHERE payer_account_id = ? AND task_id = ? AND status = 'locked'",
            (payer_account_id, task_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"escrow_id": row[0], "amount": row[1], "event_id": row[2]}

    # ------------------------------------------------------------------
    # Bank — Escrow Release
    # ------------------------------------------------------------------

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
                raise ServiceError("account_not_found", "Recipient account not found", 404, {})
            # The event is written first so the transaction row can name it.
            event_id = self._insert_event(cursor, data["event"])
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
                where_clause, where_params = self._compile_constraints(
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
                        self._check_constraint_violation(
                            cursor,
                            "bank_escrow",
                            "escrow_id",
                            data["escrow_id"],
                            constraints,
                        )
                    except ServiceError:
                        self._db.rollback()
                        raise
                    self._db.rollback()
                    raise ServiceError("escrow_not_found", "No escrow with this ID", 404, {})
            else:
                cursor.execute(
                    "UPDATE bank_escrow SET status = 'released', resolved_at = ? "
                    "WHERE escrow_id = ?",
                    (data["resolved_at"], data["escrow_id"]),
                )
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
            raise ServiceError("escrow_not_found", "No escrow with this ID", 404, {})
        if row[2] != "locked":
            self._db.rollback()
            raise ServiceError(
                "escrow_already_resolved",
                "Escrow has already been released or split",
                409,
                {},
            )
        return {"escrow_id": row[0], "amount": row[1], "status": row[2]}

    # ------------------------------------------------------------------
    # Bank — Escrow Split
    # ------------------------------------------------------------------

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
                    "amount_mismatch",
                    "worker_amount + poster_amount does not equal escrow amount",
                    400,
                    {},
                )
            # The event is written first so the transaction rows can name it.
            event_id = self._insert_event(cursor, data["event"])
            # Credit worker (if amount > 0)
            if worker_amount > 0:
                cursor.execute(
                    "UPDATE bank_accounts SET balance = balance + ? WHERE account_id = ?",
                    (worker_amount, data["worker_account_id"]),
                )
                if cursor.rowcount == 0:
                    self._db.rollback()
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
                    self._db.rollback()
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
                where_clause, where_params = self._compile_constraints(
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
                        self._check_constraint_violation(
                            cursor,
                            "bank_escrow",
                            "escrow_id",
                            data["escrow_id"],
                            constraints,
                        )
                    except ServiceError:
                        self._db.rollback()
                        raise
                    self._db.rollback()
                    raise ServiceError("escrow_not_found", "No escrow with this ID", 404, {})
            else:
                cursor.execute(
                    "UPDATE bank_escrow SET status = 'split', resolved_at = ? WHERE escrow_id = ?",
                    (data["resolved_at"], data["escrow_id"]),
                )
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
                "bidding_deadline, bid_count, escrow_pending, escrow_id, created_at, "
                "dispute_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    data.get("bid_count", 0),
                    data.get("escrow_pending", 0),
                    data["escrow_id"],
                    data["created_at"],
                    data.get("dispute_id"),
                ),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"task_id": data["task_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "task_exists",
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

    def submit_bid(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Submit a bid on a task.

        INSERT INTO board_bids + increment board_tasks.bid_count + INSERT INTO events,
        all in the same transaction. bid_count is a materialized counter (GAP-E13,
        T-101) updated at write time, matching how every other board_tasks field in
        this schema works — not derived on read.
        Idempotency: idx_board_bids_one_per_agent on (task_id, bidder_id) — a
        rejected duplicate bid rolls the whole transaction back, so it never reaches
        the increment below.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            if constraints is not None:
                self._verify_cross_table_constraint(
                    cursor,
                    "board_tasks",
                    {"task_id": data["task_id"], **constraints},
                )
            cursor.execute(
                "INSERT INTO board_bids "
                "(bid_id, task_id, bidder_id, proposal, amount, submitted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    data["bid_id"],
                    data["task_id"],
                    data["bidder_id"],
                    data["proposal"],
                    data.get("amount", 0),
                    data["submitted_at"],
                ),
            )
            cursor.execute(
                "UPDATE board_tasks SET bid_count = bid_count + 1 WHERE task_id = ?",
                (data["task_id"],),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"bid_id": data["bid_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "bid_exists",
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
        self,
        task_id: str,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
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
                        "invalid_field",
                        f"Unknown column: {col}",
                        400,
                        {"field": col},
                    )
                set_parts.append(f"{col} = ?")
                values.append(val)

            set_clause = ", ".join(set_parts)
            if constraints is not None:
                where_clause, where_params = self._compile_constraints(
                    "board_tasks",
                    "task_id",
                    task_id,
                    constraints,
                )
                cursor.execute(
                    f"UPDATE board_tasks SET {set_clause} WHERE {where_clause}",  # nosec B608
                    [*values, *where_params],
                )
            else:
                values.append(task_id)
                cursor.execute(
                    f"UPDATE board_tasks SET {set_clause} WHERE task_id = ?",  # nosec B608
                    values,
                )
            if cursor.rowcount == 0:
                if constraints is not None:
                    try:
                        self._check_constraint_violation(
                            cursor,
                            "board_tasks",
                            "task_id",
                            task_id,
                            constraints,
                        )
                    except ServiceError:
                        self._db.rollback()
                        raise
                self._db.rollback()
                raise ServiceError("task_not_found", "No task with this task_id", 404, {})
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

    def record_asset(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Record an asset upload (metadata only).

        INSERT INTO board_assets + INSERT INTO events.
        Idempotency: PK on asset_id.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            if constraints is not None:
                self._verify_cross_table_constraint(
                    cursor,
                    "board_tasks",
                    {"task_id": data["task_id"], **constraints},
                )
            cursor.execute(
                "INSERT INTO board_assets "
                "(asset_id, task_id, uploader_id, filename, content_type, "
                "size_bytes, storage_path, content_hash, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["asset_id"],
                    data["task_id"],
                    data["uploader_id"],
                    data["filename"],
                    data["content_type"],
                    data["size_bytes"],
                    data["storage_path"],
                    data.get("content_hash"),
                    data["uploaded_at"],
                ),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"asset_id": data["asset_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "asset_exists",
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

    def _lookup_reverse_feedback_id(
        self,
        cursor: sqlite3.Cursor,
        task_id: str,
        from_agent_id: str,
        to_agent_id: str,
    ) -> str | None:
        """Find the counter-feedback: the same task, with the two agents swapped."""
        cursor.execute(
            "SELECT feedback_id FROM reputation_feedback "
            "WHERE task_id = ? AND from_agent_id = ? AND to_agent_id = ?",
            (task_id, to_agent_id, from_agent_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return str(row["feedback_id"])

    def _lookup_agent_names(
        self,
        cursor: sqlite3.Cursor,
        agent_ids: tuple[str, str],
    ) -> dict[str, str]:
        """Resolve agent display names. Both ids are FK-guaranteed to exist."""
        cursor.execute(
            "SELECT agent_id, name FROM identity_agents WHERE agent_id IN (?, ?)",
            agent_ids,
        )
        return {str(row["agent_id"]): str(row["name"]) for row in cursor.fetchall()}

    def _build_reveal_event(
        self,
        cursor: sqlite3.Cursor,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the feedback.revealed event for a pair that just became visible."""
        from_agent_id = str(data["from_agent_id"])
        to_agent_id = str(data["to_agent_id"])
        names = self._lookup_agent_names(cursor, (from_agent_id, to_agent_id))
        from_name = names[from_agent_id]
        to_name = names[to_agent_id]
        return {
            "event_source": "reputation",
            "event_type": "feedback.revealed",
            "timestamp": data["submitted_at"],
            "task_id": data["task_id"],
            "agent_id": from_agent_id,
            "summary": f"Sealed feedback revealed between {from_name} and {to_name}",
            "payload": json.dumps(
                {
                    "task_id": data["task_id"],
                    "from_name": from_name,
                    "to_name": to_name,
                    "category": data["category"],
                }
            ),
        }

    def submit_feedback(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Submit feedback, revealing the sealed pair atomically.

        INSERT INTO reputation_feedback + reverse-pair lookup + UPDATE of both rows
        + INSERT INTO events, all inside one BEGIN IMMEDIATE transaction.

        The reveal policy lives here rather than in the caller. A caller that reads
        the reverse pair before writing races with the counter-feedback: both readers
        see "no reverse yet" and both rows stay sealed forever. Deciding inside the
        write transaction makes the both-sealed outcome unreachable.

        ``force_visible`` bypasses sealing for platform-submitted ruling feedback.
        """
        force_visible = bool(data.get("force_visible", False))
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
                    1 if force_visible else 0,
                ),
            )
            # The reverse lookup is a fact about the database, not a caller policy:
            # it runs even under force_visible, so a sealed counterpart is still revealed.
            reverse_id = self._lookup_reverse_feedback_id(
                cursor,
                str(data["task_id"]),
                str(data["from_agent_id"]),
                str(data["to_agent_id"]),
            )
            revealed = reverse_id is not None
            if revealed:
                cursor.execute(
                    "UPDATE reputation_feedback SET visible = 1 WHERE feedback_id IN (?, ?)",
                    (data["feedback_id"], reverse_id),
                )
            event_id = self._insert_event(cursor, data["event"])
            if revealed:
                self._insert_event(cursor, self._build_reveal_event(cursor, data))
            self._db.commit()
            return {
                "feedback_id": data["feedback_id"],
                "visible": force_visible or revealed,
                "event_id": event_id,
            }
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "feedback_exists",
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
                "(claim_id, task_id, claimant_id, respondent_id, reason, status, "
                "rebuttal_deadline, filed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["claim_id"],
                    data["task_id"],
                    data["claimant_id"],
                    data["respondent_id"],
                    data["reason"],
                    data["status"],
                    data.get("rebuttal_deadline"),
                    data["filed_at"],
                ),
            )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"claim_id": data["claim_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            # court_claims has two UNIQUE-family constraints: claim_id (PK) and
            # task_id (one claim per task). A genuine claim_id collision keeps
            # claim_exists; a task_id collision under a fresh claim_id is a
            # different fact — the task already has a claim filed against it —
            # and reporting it as claim_exists would be misleading (GAP-C7).
            if self._lookup_claim_exists(data["claim_id"]):
                raise ServiceError(
                    "claim_exists",
                    "Claim with this claim_id already exists",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "task_already_disputed",
                "This task already has a claim filed against it",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def _lookup_claim_exists(self, claim_id: str) -> bool:
        """True if a claim row with this claim_id already exists."""
        cursor = self._db.execute(
            "SELECT 1 FROM court_claims WHERE claim_id = ?",
            (claim_id,),
        )
        return cursor.fetchone() is not None

    def update_claim_status(
        self,
        claim_id: str,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Update a claim status with optional constraints.

        UPDATE court_claims + INSERT INTO events, in one transaction.
        """
        event = data.get("event")
        if event is None:
            raise ServiceError(
                "missing_field",
                "Missing required field: event",
                400,
                {"field": "event"},
            )
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            if constraints is not None:
                where_clause, where_params = self._compile_constraints(
                    "court_claims",
                    "claim_id",
                    claim_id,
                    constraints,
                )
                cursor.execute(
                    f"UPDATE court_claims SET status = ? WHERE {where_clause}",  # nosec B608
                    [data["status"], *where_params],
                )
            else:
                cursor.execute(
                    "UPDATE court_claims SET status = ? WHERE claim_id = ?",
                    (data["status"], claim_id),
                )

            if cursor.rowcount == 0:
                if constraints is not None:
                    try:
                        self._check_constraint_violation(
                            cursor,
                            "court_claims",
                            "claim_id",
                            claim_id,
                            constraints,
                        )
                    except ServiceError:
                        self._db.rollback()
                        raise
                self._db.rollback()
                raise ServiceError("claim_not_found", "No claim with this claim_id", 404, {})

            event_id = self._insert_event(cursor, event)

            self._db.commit()
            return {
                "claim_id": claim_id,
                "status": data["status"],
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except Exception:
            self._db.rollback()
            raise

    # ------------------------------------------------------------------
    # Court — Rebuttals
    # ------------------------------------------------------------------

    def submit_rebuttal(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
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
                if constraints is not None:
                    where_clause, where_params = self._compile_constraints(
                        "court_claims",
                        "claim_id",
                        data["claim_id"],
                        constraints,
                    )
                    cursor.execute(
                        f"UPDATE court_claims SET status = ? WHERE {where_clause}",  # nosec B608
                        [claim_status, *where_params],
                    )
                    if cursor.rowcount == 0:
                        try:
                            self._check_constraint_violation(
                                cursor,
                                "court_claims",
                                "claim_id",
                                data["claim_id"],
                                constraints,
                            )
                        except ServiceError:
                            self._db.rollback()
                            raise
                        self._db.rollback()
                        raise ServiceError(
                            "claim_not_found",
                            "No claim with this claim_id",
                            404,
                            {},
                        )
                else:
                    cursor.execute(
                        "UPDATE court_claims SET status = ? WHERE claim_id = ?",
                        (claim_status, data["claim_id"]),
                    )
            elif constraints is not None:
                self._verify_cross_table_constraint(
                    cursor,
                    "court_claims",
                    {"claim_id": data["claim_id"], **constraints},
                )
            event_id = self._insert_event(cursor, data["event"])
            self._db.commit()
            return {"rebuttal_id": data["rebuttal_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "rebuttal_exists",
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
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "ruling_exists",
                "Ruling with this ruling_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def delete_ruling(self, claim_id: str, event: dict[str, Any]) -> dict[str, object]:
        """
        Delete a ruling record by claim_id.

        DELETE FROM court_rulings + INSERT INTO events, in one transaction.
        When no ruling matched, nothing was written and no event is logged.
        """
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "DELETE FROM court_rulings WHERE claim_id = ?",
                (claim_id,),
            )
            deleted = cursor.rowcount > 0
            event_id: int | None = None
            if deleted:
                event_id = self._insert_event(cursor, event)
            self._db.commit()
            return {"deleted": deleted, "claim_id": claim_id, "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            if self._is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise
        except Exception:
            self._db.rollback()
            raise
