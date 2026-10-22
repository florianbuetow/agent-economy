"""Fixed offline migration from the approved complete v0 schema to v1."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from db_gateway_service.money_audit import MoneyAuditResult, audit_money
from db_gateway_service.recovery import (
    BackupBoundSource,
    RecoveryError,
    open_backup_bound_source,
)
from db_gateway_service.schema import (
    SchemaState,
    _tokenize_sql,
    classify_schema,
    load_schema_sql,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Never

    from service_commons.recovery_contract import RecoveryInventoryV1

_DROP_ORDER = (
    "board_bids",
    "board_tasks",
    "bank_transactions",
    "bank_escrow",
    "bank_accounts",
)
_CREATE_ORDER = (
    "bank_accounts",
    "bank_transactions",
    "bank_escrow",
    "board_tasks",
    "board_bids",
)
_INDEX_ORDER = (
    "idx_bank_tx_idempotent",
    "idx_bank_tx_history",
    "idx_bank_escrow_lifetime",
    "idx_board_tasks_status",
    "idx_board_tasks_poster",
    "idx_board_tasks_worker",
    "idx_board_tasks_created",
    "idx_board_bids_one_per_agent",
)
_ALLOWED_ARTIFACT_FINDINGS = frozenset({"unknown_schema", "unclassified"})


class MigrationError(Exception):
    """Expected offline migration failure with redacted structured details."""

    def __init__(
        self,
        reason: str,
        message: str,
        safe_details: dict[str, object],
    ) -> None:
        self.reason = reason
        self.message = message
        self.safe_details = safe_details
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Verified migration result; diagnostic evidence is retained under work_root."""

    applied: bool
    schema_version: int
    backup_id: str
    manifest_sha256: str
    plan_sha256: str
    observation: str
    evidence_path: Path

    def model_dump(self, *, mode: str) -> dict[str, object]:
        if mode != "json":
            raise ValueError("MigrationResult supports only mode='json'")
        return {
            "applied": self.applied,
            "schema_version": self.schema_version,
            "backup_id": self.backup_id,
            "manifest_sha256": self.manifest_sha256,
            "plan_sha256": self.plan_sha256,
            "observation": self.observation,
            "evidence_path": str(self.evidence_path),
        }


@dataclass(frozen=True, slots=True)
class _Plan:
    tables: dict[str, str]
    indexes: dict[str, str]
    metadata: str
    sha256: str


@dataclass(frozen=True, slots=True)
class _Admission:
    snapshot: dict[str, object]
    money: MoneyAuditResult


def migrate_v0_to_v1(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    busy_timeout_ms: int,
) -> MigrationResult:
    """Migrate one verified, quiescent v0 source under a fresh authority context."""
    _validate_timeout(busy_timeout_ms)
    plan = _migration_plan()
    try:
        with open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
        ) as bound:
            result = bound.artifact_verification_result
            if (
                not result.artifact_verified
                or not result.snapshot_preserved
                or not result.identity_complete
            ):
                raise MigrationError(
                    "backup_not_admitted",
                    "Backup preservation or identity verification is incomplete",
                    {"finding_codes": list(result.finding_codes)},
                )
            disallowed = sorted(set(result.finding_codes) - _ALLOWED_ARTIFACT_FINDINGS)
            if disallowed:
                raise MigrationError(
                    "backup_not_admitted",
                    "Backup has unresolved migration findings",
                    {"finding_codes": disallowed},
                )

            try:
                clone = _open_read_only(bound.database_path)
            except sqlite3.Error as exc:
                raise MigrationError(
                    "backup_clone_open_failed",
                    "Verified backup clone could not be opened",
                    {},
                ) from exc
            clone_primary: BaseException | None = None
            try:
                admission = _admit_v0_clone(clone)
            except BaseException as primary:
                clone_primary = primary
                raise
            finally:
                _close_read_only(clone, primary=clone_primary, label="admission clone")
            bound.verify_before_write()
            bound.begin_write_phase()
            return _migrate_bound_source(
                bound=bound,
                admission=admission,
                plan=plan,
                backup_id=backup_id,
                manifest_sha256=bound.manifest_sha256,
                busy_timeout_ms=busy_timeout_ms,
            )
    except MigrationError:
        raise
    except RecoveryError as exc:
        raise MigrationError(exc.reason, str(exc), {}) from exc


def migrate_v1_to_v2(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    busy_timeout_ms: int,
) -> MigrationResult:
    """Run the separate verified receipt-ownership migration from v1 to v2."""
    from db_gateway_service.receipt_migration import migrate_v1_to_v2 as implementation

    return implementation(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        busy_timeout_ms=busy_timeout_ms,
    )


def migrate_v2_to_v3(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    busy_timeout_ms: int,
) -> MigrationResult:
    """Run the separate verified task-contract migration from v2 to v3."""
    from db_gateway_service.task_migration import migrate_v2_to_v3 as implementation

    return implementation(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        busy_timeout_ms=busy_timeout_ms,
    )


def migrate_v3_to_v4(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    busy_timeout_ms: int,
) -> MigrationResult:
    """Run the explicit verified publication migration from v3 to v4."""
    from db_gateway_service.publication_migration import migrate_v3_to_v4 as implementation

    return implementation(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        busy_timeout_ms=busy_timeout_ms,
    )


def _migrate_bound_source(
    *,
    bound: BackupBoundSource,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
    busy_timeout_ms: int,
) -> MigrationResult:
    source_path = bound.source_database_path
    try:
        writer = _open_live_writer(source_path, busy_timeout_ms)
    except BaseException as primary:
        evidence = _write_evidence_with_note(
            bound,
            {
                "status": "source_open_failed",
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
                "error_type": type(primary).__name__,
            },
            primary,
        )
        details: dict[str, object] = {}
        if evidence is not None:
            details["evidence_path"] = str(evidence)
        raise MigrationError(
            "source_open_failed",
            "Live source could not be opened read-write",
            details,
        ) from primary
    if type(writer) is not sqlite3.Connection:
        raise MigrationError("source_open_failed", "Live writer is not a SQLite connection", {})
    transaction_started = False
    commit_started = False
    try:
        _configure_migration_connection(writer, busy_timeout_ms)
        bound.verify_during_write()
        writer.execute("BEGIN IMMEDIATE")
        transaction_started = True
        if not writer.in_transaction:
            raise MigrationError("configuration_failed", "Migration transaction did not begin", {})
        _validate_live_v0(writer, admission)
        bound.verify_during_write()
        _apply_plan(
            writer,
            clone_path=bound.database_path,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
        _fault("before_validation")
        _validate_migrated_transaction(
            writer,
            admission=admission,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
        bound.verify_during_write()
        _fault("before_commit")
        commit_started = True
        _commit(writer)
        transaction_started = False
    except BaseException as primary:
        if commit_started:
            _raise_commit_uncertain(
                primary,
                writer=writer,
                bound=bound,
                source_path=source_path,
                admission=admission,
                backup_id=backup_id,
                manifest_sha256=manifest_sha256,
                plan=plan,
            )
        cleanup_codes: list[str] = []
        if transaction_started:
            cleanup_codes.extend(_rollback_with_note(writer, primary))
        disposed_codes, _dispose_error = _dispose_writer(writer, primary)
        cleanup_codes.extend(disposed_codes)
        observation = _observe_source(source_path, admission.snapshot)
        _raise_precommit_failure(
            primary,
            bound=bound,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
            plan=plan,
            observation=observation,
            cleanup_codes=cleanup_codes,
        )
    return _finalize_committed(
        writer=writer,
        bound=bound,
        source_path=source_path,
        admission=admission,
        backup_id=backup_id,
        manifest_sha256=manifest_sha256,
        plan=plan,
    )


def _raise_commit_uncertain(
    primary: BaseException,
    *,
    writer: sqlite3.Connection,
    bound: BackupBoundSource,
    source_path: Path,
    admission: _Admission,
    backup_id: str,
    manifest_sha256: str,
    plan: _Plan,
) -> Never:
    cleanup_codes, _dispose_error = _dispose_writer(writer, primary)
    observation = _observe_source(source_path, admission.snapshot)
    evidence = _write_evidence_with_note(
        bound,
        {
            "status": "commit_outcome_uncertain",
            "observation": observation,
            "backup_id": backup_id,
            "manifest_sha256": manifest_sha256,
            "plan_sha256": plan.sha256,
            "cleanup_codes": cleanup_codes,
            "native_close_completed": True,
        },
        primary,
    )
    details: dict[str, object] = {
        "observation": observation,
        "cleanup_codes": cleanup_codes,
        "native_close_completed": True,
    }
    if evidence is not None:
        details["evidence_path"] = str(evidence)
    raise MigrationError(
        "commit_outcome_uncertain",
        "Migration commit outcome is uncertain",
        details,
    ) from primary


def _raise_precommit_failure(
    primary: BaseException,
    *,
    bound: BackupBoundSource,
    backup_id: str,
    manifest_sha256: str,
    plan: _Plan,
    observation: str,
    cleanup_codes: list[str],
) -> Never:
    evidence = _write_evidence_with_note(
        bound,
        {
            "status": "failed_before_commit",
            "backup_id": backup_id,
            "manifest_sha256": manifest_sha256,
            "plan_sha256": plan.sha256,
            "error_type": type(primary).__name__,
            "observation": observation,
            "cleanup_codes": cleanup_codes,
            "native_close_completed": True,
        },
        primary,
    )
    if isinstance(primary, MigrationError):
        primary.safe_details.setdefault("observation", observation)
        primary.safe_details.setdefault("cleanup_codes", cleanup_codes)
        primary.safe_details.setdefault("native_close_completed", True)
        if evidence is not None:
            primary.safe_details.setdefault("evidence_path", str(evidence))
        raise primary
    details: dict[str, object] = {
        "error_type": type(primary).__name__,
        "observation": observation,
        "cleanup_codes": cleanup_codes,
        "native_close_completed": True,
    }
    if evidence is not None:
        details["evidence_path"] = str(evidence)
    raise MigrationError(
        "migration_failed",
        "Migration failed before commit",
        details,
    ) from primary


def _finalize_committed(
    *,
    writer: sqlite3.Connection,
    bound: BackupBoundSource,
    source_path: Path,
    admission: _Admission,
    backup_id: str,
    manifest_sha256: str,
    plan: _Plan,
) -> MigrationResult:
    cleanup_codes, cleanup_error = _dispose_writer(writer, None)
    try:
        bound.verify_during_write()
        _validate_fresh_v1(
            source_path,
            admission=admission,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
    except BaseException as primary:
        if cleanup_error is not None:
            primary.add_note(f"Dynamic writer close also failed: {cleanup_error!r}")
        observation = _observe_source(source_path, admission.snapshot)
        evidence = _write_evidence_with_note(
            bound,
            {
                "status": "post_commit_validation_failed",
                "observation": observation,
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
                "cleanup_codes": cleanup_codes,
                "native_close_completed": True,
            },
            primary,
        )
        details: dict[str, object] = {
            "observation": observation,
            "cleanup_codes": cleanup_codes,
            "native_close_completed": True,
        }
        if evidence is not None:
            details["evidence_path"] = str(evidence)
        raise MigrationError(
            "post_commit_validation_failed",
            "Migration committed but post-commit validation failed",
            details,
        ) from primary
    if cleanup_codes != ["native_close_completed"]:
        observation = _observe_source(source_path, admission.snapshot)
        cleanup_primary = MigrationError(
            "post_commit_cleanup_failed",
            "Migration committed but dynamic writer cleanup failed",
            {
                "observation": observation,
                "cleanup_codes": cleanup_codes,
                "native_close_completed": True,
            },
        )
        evidence = _write_evidence_with_note(
            bound,
            {
                "status": "post_commit_cleanup_failed",
                "observation": observation,
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
                "cleanup_codes": cleanup_codes,
                "native_close_completed": True,
            },
            cleanup_primary,
        )
        if evidence is not None:
            cleanup_primary.safe_details["evidence_path"] = str(evidence)
        raise cleanup_primary from cleanup_error
    try:
        evidence = _write_evidence(
            bound,
            {
                "status": "committed_v1_verified",
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
            },
        )
    except BaseException as primary:
        raise MigrationError(
            "post_commit_evidence_failed",
            "Migration committed but evidence publication failed",
            {"observation": "committed_v1_observed"},
        ) from primary
    return MigrationResult(
        applied=True,
        schema_version=1,
        backup_id=backup_id,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan.sha256,
        observation="committed_v1_verified",
        evidence_path=evidence,
    )


def _admit_v0_clone(connection: sqlite3.Connection) -> _Admission:
    if classify_schema(connection).state is not SchemaState.SUPPORTED_V0:
        raise MigrationError("unsupported_schema", "Backup clone is not an exact supported v0", {})
    _validate_database_health(connection)
    money = audit_money(connection)
    if not money.financially_clean:
        diagnostics = [
            {
                "code": finding.code,
                "table": finding.table,
                "column": finding.column,
                "record_ids": list(finding.record_ids),
            }
            for finding in money.findings
        ]
        raise MigrationError(
            "financial_audit_failed",
            "Backup clone financial audit failed",
            {
                "finding_codes": sorted({finding.code for finding in money.findings}),
                "findings": diagnostics,
            },
        )
    return _Admission(_database_snapshot(connection), money)


def _validate_live_v0(connection: sqlite3.Connection, admission: _Admission) -> None:
    if classify_schema(connection).state is not SchemaState.SUPPORTED_V0:
        raise MigrationError("source_changed", "Live source is no longer exact v0", {})
    _validate_database_health(connection)
    money = audit_money(connection)
    if not money.financially_clean or money != admission.money:
        raise MigrationError(
            "source_changed", "Live financial state differs from verified clone", {}
        )
    if _database_snapshot(connection) != admission.snapshot:
        raise MigrationError("source_changed", "Live rows differ from verified clone", {})


def _apply_plan(
    connection: sqlite3.Connection,
    *,
    clone_path: Path,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    clone = _open_read_only(clone_path)
    clone_primary: BaseException | None = None
    try:
        for table in _DROP_ORDER:
            connection.execute(f'DROP TABLE "{table}"')  # nosec B608
            _fault(f"after_drop:{table}")
        for table in _CREATE_ORDER:
            connection.execute(plan.tables[table])
            _fault(f"after_create:{table}")
            _copy_table(clone, connection, table)
            _fault(f"after_copy:{table}")
        for index in _INDEX_ORDER:
            connection.execute(plan.indexes[index])
            _fault(f"after_index:{index}")
        connection.execute(plan.metadata)
        connection.execute(
            "INSERT INTO schema_metadata "
            "(singleton, version, migrated_from_version, migration_backup_id, "
            "migration_backup_manifest_sha256) VALUES (1, 1, 0, ?, ?)",
            (backup_id, manifest_sha256),
        )
        _fault("after_metadata")
    except BaseException as primary:
        clone_primary = primary
        raise
    finally:
        _close_read_only(clone, primary=clone_primary, label="copy clone")


def _copy_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
) -> None:
    columns = [
        str(row[1])
        for row in source.execute(f'PRAGMA table_xinfo("{table}")').fetchall()  # nosec B608
        if int(row[6]) == 0
    ]
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    rows = source.execute(
        f'SELECT rowid, {quoted_columns} FROM "{table}" ORDER BY rowid'  # nosec B608
    ).fetchall()
    placeholders = ", ".join("?" for _ in range(len(columns) + 1))
    target.executemany(
        f'INSERT INTO "{table}" (rowid, {quoted_columns}) VALUES ({placeholders})',  # nosec B608
        [tuple(row) for row in rows],
    )


def _validate_migrated_transaction(
    connection: sqlite3.Connection,
    *,
    admission: _Admission,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    if classify_schema(connection).state is not SchemaState.CURRENT_V1:
        raise MigrationError("validation_failed", "Migrated schema is not exact v1", {})
    _validate_database_health(connection)
    money = audit_money(connection)
    if not money.financially_clean:
        raise MigrationError("validation_failed", "Migrated financial audit failed", {})
    if _database_snapshot(connection) != admission.snapshot:
        raise MigrationError("validation_failed", "Migrated rows differ from v0 source", {})
    metadata = connection.execute(
        "SELECT singleton, version, migrated_from_version, migration_backup_id, "
        "migration_backup_manifest_sha256 FROM schema_metadata"
    ).fetchall()
    if metadata != [(1, 1, 0, backup_id, manifest_sha256)]:
        raise MigrationError("validation_failed", "Migration provenance differs", {})


def _validate_fresh_v1(
    source_path: Path,
    *,
    admission: _Admission,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    connection = _open_read_only(source_path)
    fresh_primary: BaseException | None = None
    try:
        _validate_migrated_transaction(
            connection,
            admission=admission,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
    except BaseException as primary:
        fresh_primary = primary
        raise
    finally:
        _close_read_only(
            connection,
            primary=fresh_primary,
            label="fresh validation connection",
        )


def _validate_database_health(connection: sqlite3.Connection) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise MigrationError("integrity_failed", "SQLite integrity check failed", {})
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise MigrationError("foreign_key_failed", "SQLite foreign key check failed", {})


def _database_snapshot(connection: sqlite3.Connection) -> dict[str, object]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'schema_metadata' ORDER BY name"
        ).fetchall()
    ]
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone():
        tables.append("sqlite_sequence")
    snapshot: dict[str, object] = {}
    for table in tables:
        columns = [
            str(row[1])
            for row in connection.execute(
                f'PRAGMA table_xinfo("{table}")'  # nosec B608
            ).fetchall()
            if int(row[6]) == 0
        ]
        columns.sort()
        quoted = ", ".join(f'"{column}"' for column in columns)
        rows = connection.execute(
            f'SELECT rowid, {quoted} FROM "{table}" ORDER BY rowid'  # nosec B608
        ).fetchall()
        snapshot[table] = [[_snapshot_value(value) for value in tuple(row)] for row in rows]
    return snapshot


def _snapshot_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"blob": value.hex()}
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise MigrationError("unsupported_storage", "SQLite returned unsupported storage", {})


def _configure_migration_connection(connection: sqlite3.Connection, busy_timeout_ms: int) -> None:
    if connection.autocommit is not True or connection.in_transaction:
        raise MigrationError(
            "configuration_failed", "Migration requires explicit SQL transactions", {}
        )
    connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")  # nosec B608
    connection.execute("PRAGMA foreign_keys=OFF")
    timeout = connection.execute("PRAGMA busy_timeout").fetchone()
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    if timeout != (busy_timeout_ms,) or foreign_keys != (0,):
        raise MigrationError("configuration_failed", "Migration connection PRAGMAs differ", {})


def _observe_source(source_path: Path, original_snapshot: dict[str, object]) -> str:
    try:
        connection = _open_read_only(source_path)
        try:
            state = classify_schema(connection).state
            snapshot = _database_snapshot(connection)
        finally:
            connection.close()
    except BaseException:
        return "unreadable_state"
    if state is SchemaState.CURRENT_V1:
        return "committed_v1_observed"
    if state is SchemaState.SUPPORTED_V0 and snapshot == original_snapshot:
        return "not_applied_v0_observed"
    return "inconsistent_state_observed"


def _open_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, check_same_thread=False)


def _open_live_writer(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    return sqlite3.connect(
        f"{path.as_uri()}?mode=rw",
        uri=True,
        check_same_thread=False,
        factory=sqlite3.Connection,
        cached_statements=0,
        autocommit=True,
        timeout=busy_timeout_ms / 1000,
    )


def _validate_timeout(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
        raise MigrationError(
            "invalid_argument",
            "busy_timeout_ms must be an integer from 0 through 2147483647",
            {},
        )


@lru_cache(maxsize=1)
def _migration_plan() -> _Plan:
    connection = sqlite3.connect(":memory:")
    plan_primary: BaseException | None = None
    try:
        for statement in _split_sql(load_schema_sql(SchemaState.CURRENT_V1)):
            connection.execute(statement)
        tables = {table: _object_sql(connection, "table", table) for table in _CREATE_ORDER}
        metadata = _object_sql(connection, "table", "schema_metadata")
        indexes = {index: _object_sql(connection, "index", index) for index in _INDEX_ORDER}
    except BaseException as primary:
        plan_primary = primary
        raise
    finally:
        _close_read_only(connection, primary=plan_primary, label="plan connection")
    encoded = json.dumps(
        {"tables": tables, "indexes": indexes, "metadata": metadata},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return _Plan(tables, indexes, metadata, hashlib.sha256(encoded).hexdigest())


def _object_sql(connection: sqlite3.Connection, kind: str, name: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
        (kind, name),
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError(f"canonical migration object is missing: {name}")
    return str(row[0])


def _split_sql(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for character in sql:
        buffer += character
        if sqlite3.complete_statement(buffer):
            if _tokenize_sql(buffer):
                statements.append(buffer.strip())
            buffer = ""
    if _tokenize_sql(buffer):
        raise RuntimeError("canonical schema has an incomplete statement")
    return tuple(statements)


def _write_evidence(bound: BackupBoundSource, value: dict[str, object]) -> Path:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    return bound.write_evidence("migration-result.json", data)


def _write_evidence_with_note(
    bound: BackupBoundSource,
    value: dict[str, object],
    primary: BaseException,
) -> Path | None:
    try:
        return _write_evidence(bound, value)
    except BaseException as secondary:
        primary.add_note(f"Migration evidence publication also failed: {secondary!r}")
        return None


def _rollback_with_note(connection: sqlite3.Connection, primary: BaseException) -> list[str]:
    codes: list[str] = []
    try:
        _rollback_connection(connection)
    except BaseException as secondary:
        codes.append("rollback_method_failed")
        primary.add_note(f"Secondary rollback failure: {secondary!r}")
    if connection.in_transaction:
        try:
            sqlite3.Connection.execute(connection, "ROLLBACK").close()
        except BaseException as secondary:
            primary.add_note(f"Native rollback fallback failed: {secondary!r}")
    return codes


def _dispose_writer(
    connection: sqlite3.Connection, primary: BaseException | None
) -> tuple[list[str], BaseException | None]:
    codes: list[str] = []
    close_error: BaseException | None = None
    try:
        _dynamic_close(connection)
    except BaseException as secondary:
        codes.append("close_method_failed")
        close_error = secondary
        if primary is not None:
            primary.add_note(f"Secondary close failure: {secondary!r}")
    sqlite3.Connection.close(connection)
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return [*codes, "native_close_completed"], close_error
    raise RuntimeError("Native SQLite close did not dispose the live writer")


def _close_read_only(
    connection: sqlite3.Connection,
    *,
    primary: BaseException | None,
    label: str,
) -> None:
    try:
        connection.close()
    except BaseException as secondary:
        if primary is not None:
            primary.add_note(f"Secondary {label} close failure: {secondary!r}")
        try:
            sqlite3.Connection.close(connection)
        except BaseException as fallback:
            if primary is not None:
                primary.add_note(f"Native {label} close fallback failed: {fallback!r}")
            else:
                raise MigrationError(
                    "close_failed", f"{label} could not be closed", {}
                ) from fallback
        if primary is None:
            raise MigrationError(
                "close_failed", f"{label} close reported failure", {}
            ) from secondary


def _rollback_connection(connection: sqlite3.Connection) -> None:
    connection.execute("ROLLBACK").close()


def _dynamic_close(connection: sqlite3.Connection) -> None:
    connection.close()


def _commit(connection: sqlite3.Connection) -> None:
    connection.execute("COMMIT").close()
    if connection.in_transaction:
        raise RuntimeError("Migration commit did not end the transaction")


def _fault(_point: str) -> None:
    """Fault-injection seam used only by migration transaction tests."""
