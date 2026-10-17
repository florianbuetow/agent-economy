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

from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import (
    SchemaClassification,
    SchemaState,
    _tokenize_sql,
    classify_schema,
)
from db_gateway_service.services.bank_writer import BankWriter
from db_gateway_service.services.board_writer import BoardWriter
from db_gateway_service.services.court_writer import CourtWriter
from db_gateway_service.services.identity_writer import IdentityWriter
from db_gateway_service.services.reputation_writer import ReputationWriter


def _schema_statements(schema_sql: str) -> tuple[str, ...]:
    """Split and allowlist complete bootstrap statements before any mutation."""
    statements: list[str] = []
    buffer = ""
    for character in schema_sql:
        buffer += character
        if not sqlite3.complete_statement(buffer):
            continue
        statement = buffer.strip()
        buffer = ""
        if _tokenize_sql(statement):
            _validate_bootstrap_statement(statement)
            statements.append(statement)
    if _tokenize_sql(buffer):
        raise sqlite3.OperationalError("incomplete schema statement")
    if not statements:
        raise sqlite3.OperationalError("schema contains no statements")
    return tuple(statements)


def _validate_bootstrap_statement(statement: str) -> None:
    tokens = _tokenize_sql(statement)
    body = tokens[:-1] if tokens and tokens[-1] == ";" else tokens
    allowed = (
        body[:2] == ("create", "table")
        or body[:3] == ("create", "unique", "index")
        or body[:2] == ("create", "index")
        or body[:3] == ("insert", "into", "schema_metadata")
        or body[:3] == ("insert", "into", "bank_receipt_cutover")
    )
    if not allowed or ";" in body:
        first_word = body[0] if body else "empty"
        raise sqlite3.OperationalError(
            f"schema statement is not allowed during transactional bootstrap: {first_word}"
        )


def _validate_database_configuration(busy_timeout_ms: int, journal_mode: str) -> None:
    valid_timeout = (
        not isinstance(busy_timeout_ms, bool)
        and isinstance(busy_timeout_ms, int)
        and 0 <= busy_timeout_ms <= 2_147_483_647
    )
    if valid_timeout and journal_mode == "wal":
        return
    raise ServiceError(
        "invalid_database_configuration",
        "Invalid database configuration",
        500,
        {
            "busy_timeout_ms": busy_timeout_ms,
            "journal_mode": journal_mode,
        },
    )


def _add_cleanup_note(primary: BaseException, operation: str, secondary: BaseException) -> None:
    primary.add_note(f"Secondary {operation} failure: {secondary!r}")


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
        _validate_database_configuration(busy_timeout_ms, journal_mode)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        try:
            self._db.row_factory = sqlite3.Row
            classification = classify_schema(self._db)
            if classification.state is SchemaState.EMPTY:
                classification = self._bootstrap_schema(schema_sql)
            self._admit_existing_schema(classification)
            self._admit_database_health()
            self._admit_receipt_ownership()

            self._configure_connection(busy_timeout_ms, journal_mode)

            self._identity = IdentityWriter(self)
            self._bank = BankWriter(self)
            self._board = BoardWriter(self)
            self._reputation = ReputationWriter(self)
            self._court = CourtWriter(self)
        except BaseException as primary:
            try:
                self._db.close()
            except BaseException as secondary:
                _add_cleanup_note(primary, "connection close", secondary)
            raise

    def _admit_database_health(self) -> None:
        """Reject corrupt stored data before persistent connection configuration."""
        try:
            integrity_rows = self._db.execute("PRAGMA integrity_check").fetchall()
            integrity_issue_count = sum(
                1 for row in integrity_rows if len(row) != 1 or row[0] != "ok"
            )
            foreign_key_issue_count = sum(
                1 for _row in self._db.execute("PRAGMA foreign_key_check")
            )
        except sqlite3.DatabaseError as exc:
            raise ServiceError(
                "database_integrity_failed",
                "Database integrity admission could not be completed",
                500,
                {
                    "failed_checks": ["health_check_unavailable"],
                    "foreign_key_issue_count": None,
                    "integrity_issue_count": None,
                },
            ) from exc

        failed_checks: list[str] = []
        if integrity_issue_count:
            failed_checks.append("integrity_check")
        if foreign_key_issue_count:
            failed_checks.append("foreign_key_check")
        if not failed_checks:
            return
        raise ServiceError(
            "database_integrity_failed",
            "Database integrity admission failed",
            500,
            {
                "failed_checks": failed_checks,
                "foreign_key_issue_count": foreign_key_issue_count,
                "integrity_issue_count": integrity_issue_count,
            },
        )

    def _admit_receipt_ownership(self) -> None:
        """Reject incomplete or corrupt financial ownership before configuration."""
        result = audit_receipt_ownership(self._db)
        if result.ownership_clean:
            return
        raise ServiceError(
            "receipt_ownership_failed",
            "Financial receipt ownership admission failed",
            500,
            {
                "finding_codes": sorted({finding.code for finding in result.findings}),
                "financially_clean": result.financially_clean,
            },
        )

    def _bootstrap_schema(self, schema_sql: str | None) -> SchemaClassification:
        """Create the exact current v2 schema atomically without implicit commits."""
        if schema_sql is None:
            raise ServiceError(
                "schema_required",
                "An empty database requires the configured current schema",
                500,
                None,
            )
        statements = _schema_statements(schema_sql)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                self._db.execute(statement)
            classification = classify_schema(self._db)
            self._admit_existing_schema(classification)
            self._admit_database_health()
            self._admit_receipt_ownership()
            self._db.commit()
            return classification
        except BaseException as primary:
            try:
                self._db.rollback()
            except BaseException as secondary:
                _add_cleanup_note(primary, "bootstrap rollback", secondary)
            raise

    def _configure_connection(self, busy_timeout_ms: int, journal_mode: str) -> None:
        journal_row = self._db.execute(
            f"PRAGMA journal_mode={journal_mode}"  # nosec B608
        ).fetchone()
        self._db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")  # nosec B608
        self._db.execute("PRAGMA foreign_keys=ON")
        timeout_row = self._db.execute("PRAGMA busy_timeout").fetchone()
        foreign_keys_row = self._db.execute("PRAGMA foreign_keys").fetchone()
        effective_journal = None if journal_row is None else str(journal_row[0]).lower()
        effective_timeout = None if timeout_row is None else timeout_row[0]
        effective_foreign_keys = None if foreign_keys_row is None else foreign_keys_row[0]
        if (
            effective_journal == journal_mode
            and effective_timeout == busy_timeout_ms
            and effective_foreign_keys == 1
        ):
            return
        raise ServiceError(
            "invalid_database_configuration",
            "Effective database configuration postconditions were not applied",
            500,
            {
                "requested_journal_mode": journal_mode,
                "effective_journal_mode": effective_journal,
                "requested_busy_timeout_ms": busy_timeout_ms,
                "effective_busy_timeout_ms": effective_timeout,
                "effective_foreign_keys": effective_foreign_keys,
            },
        )

    @staticmethod
    def _admit_existing_schema(classification: SchemaClassification) -> None:
        if classification.state is SchemaState.CURRENT_V2:
            return
        details: dict[str, object] = {
            "state": classification.state.value,
            "signature_sha256": classification.signature_sha256,
            "findings": list(classification.findings),
        }
        if classification.version is not None:
            details["version"] = classification.version
        if classification.state in {SchemaState.SUPPORTED_V0, SchemaState.CURRENT_V1}:
            raise ServiceError(
                "migration_required",
                "The older schema requires an explicit verified migration",
                503,
                details,
            )
        if classification.state is SchemaState.FUTURE:
            raise ServiceError(
                "unsupported_schema_version",
                "The database schema is newer than this service supports",
                503,
                details,
            )
        raise ServiceError(
            "invalid_schema",
            "The database schema is incomplete or unknown",
            500,
            details,
        )

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

    def escrow_release(self, data: dict[str, Any]) -> dict[str, Any]:
        """Release escrowed funds to a recipient."""
        return self._bank.escrow_release(data)

    def escrow_split(self, data: dict[str, Any]) -> dict[str, Any]:
        """Split escrowed funds between worker and poster."""
        return self._bank.escrow_split(data)

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
