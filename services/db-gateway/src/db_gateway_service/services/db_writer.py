"""Database writer facade (WP-11, B16 god-class decomposition).

DbWriter itself no longer holds the domain SQL — it owns the single shared
SQLite connection (schema init/migration, the connection lifecycle) and
constructs one writer per domain (identity, bank, board, reputation, court),
delegating every public write method to the matching domain writer. Public
method names/signatures stay byte-stable, so routers and
tests/architecture/test_gap_c5_single_writer_invariant.py's
``inspect.getmembers(DbWriter)`` coroutine-function scan both keep working
unchanged: every one of these delegating methods is itself a plain
(non-async) ``def``.

Domain writers read the shared connection through the public ``connection``
property below (never a private attribute) and stay plain (non-async) ``def``
methods that run BEGIN IMMEDIATE through COMMIT/ROLLBACK to completion
without awaiting anything in between — the same single-writer invariant this
facade upheld before the split. That per-file invariant is verified for the
new domain-writer modules by
tests/architecture/test_db_writer_domains_no_await_invariant.py (new test,
mirroring test_gap_c5's existing AST check).

GAP-C7: IntegrityError classification (``exc.sqlite_errorcode`` against the
driver's ``SQLITE_CONSTRAINT_FOREIGNKEY``/``SQLITE_CONSTRAINT_UNIQUE``
constants, never the raw message text) now lives in
``db_writer_helpers.is_foreign_key_violation``/``is_unique_violation`` — see
tests/unit/test_gap_c7_error_mapping_domains.py for the extended guard.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from service_commons.exceptions import ServiceError

from db_gateway_service.services.bank_writer import BankWriter
from db_gateway_service.services.board_writer import BoardWriter
from db_gateway_service.services.court_writer import CourtWriter
from db_gateway_service.services.identity_writer import IdentityWriter
from db_gateway_service.services.reputation_writer import ReputationWriter


class DbWriter:
    """
    SQLite transaction executor.

    Each public method maps to one API endpoint and delegates to the domain
    writer that owns it. Every domain writer method:
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

        self._identity = IdentityWriter(self)
        self._bank = BankWriter(self)
        self._board = BoardWriter(self)
        self._reputation = ReputationWriter(self)
        self._court = CourtWriter(self)

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

    @property
    def connection(self) -> sqlite3.Connection:
        """The single SQLite connection shared with DbReader and every domain writer.

        Single-writer invariant (GAP-C5, ADR pending in WP-12): the gateway keeps
        exactly one connection for the whole process and hands the same object to
        DbReader and each domain writer instead of opening a second one. That is
        safe only because every write method is a plain (non-async) `def` that runs
        its `BEGIN IMMEDIATE` through `COMMIT`/`ROLLBACK` to completion without
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
    # Public methods — called by routers. Each delegates to the domain
    # writer that owns the corresponding table(s).
    # ------------------------------------------------------------------

    def register_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        """Register a new agent. See IdentityWriter.register_agent."""
        return self._identity.register_agent(data)

    def create_account(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a bank account with optional initial credit."""
        return self._bank.create_account(data)

    def credit_account(self, data: dict[str, Any]) -> dict[str, Any]:
        """Credit an account."""
        return self._bank.credit_account(data)

    def escrow_lock(self, data: dict[str, Any]) -> dict[str, Any]:
        """Lock funds in escrow."""
        return self._bank.escrow_lock(data)

    def escrow_release(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Release escrowed funds to a recipient."""
        return self._bank.escrow_release(data, constraints)

    def escrow_split(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Split escrowed funds between worker and poster."""
        return self._bank.escrow_split(data, constraints)

    def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new task."""
        return self._board.create_task(data)

    def submit_bid(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Submit a bid on a task."""
        return self._board.submit_bid(data, constraints)

    def update_task_status(
        self,
        task_id: str,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Update task status and associated fields."""
        return self._board.update_task_status(task_id, data, constraints)

    def record_asset(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Record an asset upload (metadata only)."""
        return self._board.record_asset(data, constraints)

    def submit_feedback(self, data: dict[str, Any]) -> dict[str, Any]:
        """Submit feedback, revealing the sealed pair atomically."""
        return self._reputation.submit_feedback(data)

    def file_claim(self, data: dict[str, Any]) -> dict[str, Any]:
        """File a dispute claim."""
        return self._court.file_claim(data)

    def update_claim_status(
        self,
        claim_id: str,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Update a claim status with optional constraints."""
        return self._court.update_claim_status(claim_id, data, constraints)

    def submit_rebuttal(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Submit a rebuttal with optional claim status update."""
        return self._court.submit_rebuttal(data, constraints)

    def record_ruling(self, data: dict[str, Any]) -> dict[str, Any]:
        """Record a court ruling with optional claim status update."""
        return self._court.record_ruling(data)

    def delete_ruling(self, claim_id: str, event: dict[str, Any]) -> dict[str, object]:
        """Delete a ruling record by claim_id."""
        return self._court.delete_ruling(claim_id, event)
