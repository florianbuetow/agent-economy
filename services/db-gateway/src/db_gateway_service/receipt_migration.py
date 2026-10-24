"""Explicit verified migration from frozen schema v1 to receipt schema v2."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from db_gateway_service.migration import (
    _ALLOWED_ARTIFACT_FINDINGS,
    MigrationError,
    MigrationResult,
    _close_read_only,
    _configure_migration_connection,
    _database_snapshot,
    _dispose_writer,
    _open_live_writer,
    _open_read_only,
    _rollback_with_note,
    _snapshot_value,
    _split_sql,
    _validate_database_health,
    _validate_timeout,
    _write_evidence,
    _write_evidence_with_note,
)
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.recovery import RecoveryError, open_backup_bound_source
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Never

    from service_commons.recovery_contract import RecoveryInventoryV1

    from db_gateway_service.recovery import BackupBoundSource


@dataclass(frozen=True, slots=True)
class _Admission:
    snapshot: dict[str, object]
    metadata: tuple[object, ...]
    transaction_markers: tuple[dict[str, object], ...]
    escrow_markers: tuple[dict[str, object], ...]
    snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class _Plan:
    sidecar_sql: str
    metadata_sql: str
    sha256: str


def migrate_v1_to_v2(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    busy_timeout_ms: int,
) -> MigrationResult:
    """Migrate one verified, quiescent v1 source without implicit chaining."""
    _validate_timeout(busy_timeout_ms)
    plan = _plan()
    try:
        with open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
        ) as bound:
            verification = bound.artifact_verification_result
            if (
                not verification.artifact_verified
                or not verification.snapshot_preserved
                or not verification.identity_complete
            ):
                raise MigrationError(
                    "backup_not_admitted",
                    "Backup preservation or identity verification is incomplete",
                    {"finding_codes": list(verification.finding_codes)},
                )
            disallowed = sorted(set(verification.finding_codes) - _ALLOWED_ARTIFACT_FINDINGS)
            if disallowed:
                raise MigrationError(
                    "backup_not_admitted",
                    "Backup has unresolved migration findings",
                    {"finding_codes": disallowed},
                )
            clone = _open_read_only(bound.database_path)
            clone_primary: BaseException | None = None
            try:
                admission = _admit_v1(clone)
            except BaseException as primary:
                clone_primary = primary
                raise
            finally:
                _close_read_only(clone, primary=clone_primary, label="v1 admission clone")
            bound.verify_before_write()
            bound.begin_write_phase()
            return _migrate_bound(
                bound,
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


def _admit_v1(connection: sqlite3.Connection) -> _Admission:
    classification = classify_schema(connection)
    if classification.state is not SchemaState.CURRENT_V1:
        raise MigrationError(
            "source_not_v1",
            "Verified backup source is not the exact supported v1 schema",
            {"state": classification.state.value},
        )
    _validate_database_health(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Source money audit failed", {})
    snapshot = _database_snapshot(connection)
    metadata_row = connection.execute("SELECT * FROM schema_metadata").fetchone()
    if metadata_row is None:
        raise MigrationError("source_not_v1", "Source metadata is missing", {})
    transaction_markers = _transaction_markers(connection)
    escrow_markers = _escrow_markers(connection)
    return _Admission(
        snapshot=snapshot,
        metadata=tuple(metadata_row),
        transaction_markers=transaction_markers,
        escrow_markers=escrow_markers,
        snapshot_sha256=_hash_json(snapshot),
    )


def _migrate_bound(
    bound: BackupBoundSource,
    *,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
    busy_timeout_ms: int,
) -> MigrationResult:
    try:
        writer = _open_live_writer(bound.source_database_path, busy_timeout_ms)
    except BaseException as primary:
        observation = _observe(bound.source_database_path, admission.snapshot)
        evidence = _write_evidence_with_note(
            bound,
            {
                "status": "source_open_failed",
                "observation": observation,
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
                "error_type": type(primary).__name__,
            },
            primary,
        )
        details: dict[str, object] = {"observation": observation}
        if evidence is not None:
            details["evidence_path"] = str(evidence)
        raise MigrationError(
            "source_open_failed",
            "Live source could not be opened read-write",
            details,
        ) from primary
    transaction_started = False
    commit_started = False
    try:
        _configure_migration_connection(writer, busy_timeout_ms)
        bound.verify_during_write()
        writer.execute("BEGIN IMMEDIATE")
        transaction_started = True
        _validate_live_v1(writer, admission)
        bound.verify_during_write()
        _apply_v2(
            writer,
            admission=admission,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
        _fault("before_validation")
        _validate_v2_transaction(
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
        cleanup_codes: list[str] = []
        if transaction_started and not commit_started:
            cleanup_codes.extend(_rollback_with_note(writer, primary))
        cleanup, _close_error = _dispose_writer(writer, primary)
        cleanup_codes.extend(cleanup)
        observation = _observe(bound.source_database_path, admission.snapshot)
        if commit_started:
            _raise_failure(
                "commit_outcome_uncertain",
                "Migration commit outcome is uncertain",
                primary,
                bound=bound,
                plan=plan,
                backup_id=backup_id,
                manifest_sha256=manifest_sha256,
                observation=observation,
                cleanup_codes=cleanup_codes,
            )
        _raise_failure(
            "migration_failed",
            "Migration failed before commit",
            primary,
            bound=bound,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
            observation=observation,
            cleanup_codes=cleanup_codes,
        )
    return _finalize_committed(
        writer,
        bound=bound,
        admission=admission,
        plan=plan,
        backup_id=backup_id,
        manifest_sha256=manifest_sha256,
    )


def _finalize_committed(
    writer: sqlite3.Connection,
    *,
    bound: BackupBoundSource,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
) -> MigrationResult:
    cleanup_codes, cleanup_error = _dispose_writer(writer, None)
    try:
        bound.verify_during_write()
        _validate_fresh_v2(
            bound.source_database_path,
            admission=admission,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
    except BaseException as primary:
        if cleanup_error is not None:
            primary.add_note(f"Dynamic writer close also failed: {cleanup_error!r}")
        _raise_failure(
            "post_commit_validation_failed",
            "Migration committed but post-commit validation failed",
            primary,
            bound=bound,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
            observation=_observe(bound.source_database_path, admission.snapshot),
            cleanup_codes=cleanup_codes,
        )
    if cleanup_codes != ["native_close_completed"]:
        observation = _observe(bound.source_database_path, admission.snapshot)
        cleanup_primary = MigrationError(
            "post_commit_cleanup_failed",
            "Migration committed but writer cleanup failed",
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
                "status": "committed_v2_verified",
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
            },
        )
    except BaseException as primary:
        raise MigrationError(
            "post_commit_evidence_failed",
            "Migration committed but evidence publication failed",
            {"observation": "committed_v2_observed"},
        ) from primary
    return MigrationResult(
        applied=True,
        schema_version=2,
        backup_id=backup_id,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan.sha256,
        observation="committed_v2_verified",
        evidence_path=evidence,
    )


def _apply_v2(
    connection: sqlite3.Connection,
    *,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    for statement in _split_sql(plan.sidecar_sql):
        connection.execute(statement)
    transaction_set_hash = _hash_json(list(admission.transaction_markers))
    escrow_set_hash = _hash_json(list(admission.escrow_markers))
    marker_hash = _hash_json(
        {
            "attestation_version": 1,
            "legacy_escrow_count": len(admission.escrow_markers),
            "legacy_escrow_set_sha256": escrow_set_hash,
            "legacy_transaction_count": len(admission.transaction_markers),
            "legacy_transaction_set_sha256": transaction_set_hash,
        }
    )
    source_migrated, source_backup, source_manifest = admission.metadata[2:5]
    connection.execute(
        "INSERT INTO bank_receipt_cutover VALUES "
        "(1, 1, 'v1_to_v2', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_migrated,
            source_backup,
            source_manifest,
            backup_id,
            manifest_sha256,
            plan.sha256,
            admission.snapshot_sha256,
            len(admission.transaction_markers),
            len(admission.escrow_markers),
            transaction_set_hash,
            escrow_set_hash,
            marker_hash,
        ),
    )
    connection.executemany(
        "INSERT INTO bank_legacy_transaction_observations VALUES (?, 1, ?, ?)",
        tuple(
            (
                marker["tx_id"],
                marker["observed_kind"],
                marker["observed_fact_sha256"],
            )
            for marker in admission.transaction_markers
        ),
    )
    connection.executemany(
        "INSERT INTO bank_legacy_escrow_observations VALUES (?, 1, ?, ?, ?)",
        tuple(
            (
                marker["escrow_id"],
                marker["observed_status"],
                marker["lock_fact_sha256"],
                marker["resolution_fact_sha256"],
            )
            for marker in admission.escrow_markers
        ),
    )
    connection.execute("DROP TABLE schema_metadata")
    connection.execute(plan.metadata_sql)
    connection.execute(
        "INSERT INTO schema_metadata VALUES (1, 2, 1, ?, ?)",
        (backup_id, manifest_sha256),
    )


def _validate_live_v1(connection: sqlite3.Connection, admission: _Admission) -> None:
    if classify_schema(connection).state is not SchemaState.CURRENT_V1:
        raise MigrationError("live_source_changed", "Live source is no longer exact v1", {})
    if _database_snapshot(connection) != admission.snapshot:
        raise MigrationError("live_source_changed", "Live source data changed after backup", {})
    if (
        tuple(connection.execute("SELECT * FROM schema_metadata").fetchone() or ())
        != admission.metadata
    ):
        raise MigrationError("live_source_changed", "Live source metadata changed after backup", {})
    _validate_database_health(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Live source money audit failed", {})


def _validate_v2_transaction(
    connection: sqlite3.Connection,
    *,
    admission: _Admission,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    if classify_schema(connection).state is not SchemaState.CURRENT_V2:
        raise MigrationError("v2_validation_failed", "Migrated schema is not exact v2", {})
    _validate_database_health(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Migrated money audit failed", {})
    if not audit_receipt_ownership(connection).ownership_clean:
        raise MigrationError("receipt_audit_failed", "Migrated receipt audit failed", {})
    snapshot = _preserved_snapshot(connection, tuple(admission.snapshot))
    for table, rows in admission.snapshot.items():
        if snapshot.get(table) != rows:
            raise MigrationError(
                "data_preservation_failed", "Domain data changed", {"table": table}
            )
    if connection.execute("SELECT * FROM schema_metadata").fetchone() != (
        1,
        2,
        1,
        backup_id,
        manifest_sha256,
    ):
        raise MigrationError("provenance_failed", "Migrated metadata is incorrect", {})


def _validate_fresh_v2(
    source_path: Path,
    *,
    admission: _Admission,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    connection = _open_read_only(source_path)
    primary: BaseException | None = None
    try:
        _validate_v2_transaction(
            connection,
            admission=admission,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
    except BaseException as exc:
        primary = exc
        raise
    finally:
        _close_read_only(connection, primary=primary, label="fresh v2 validation")


def _preserved_snapshot(
    connection: sqlite3.Connection,
    tables: tuple[str, ...],
) -> dict[str, object]:
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


def _transaction_markers(connection: sqlite3.Connection) -> tuple[dict[str, object], ...]:
    rows = connection.execute(
        "SELECT tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id "
        "FROM bank_transactions ORDER BY tx_id"
    ).fetchall()
    kinds = {
        "credit": "observed_credit",
        "escrow_lock": "observed_escrow_lock",
        "escrow_release": "observed_escrow_release",
    }
    markers: list[dict[str, object]] = []
    for row in rows:
        fact = {
            "tx_id": row[0],
            "account_id": row[1],
            "type": row[2],
            "amount": row[3],
            "balance_after": row[4],
            "reference": row[5],
            "timestamp": row[6],
            "event_id": row[7],
        }
        markers.append(
            {
                "tx_id": row[0],
                "observed_kind": kinds[str(row[2])],
                "observed_fact_sha256": _hash_json(fact),
            }
        )
    return tuple(markers)


def _escrow_markers(connection: sqlite3.Connection) -> tuple[dict[str, object], ...]:
    rows = connection.execute(
        "SELECT escrow_id, payer_account_id, amount, task_id, status, created_at, "
        "resolved_at, event_id FROM bank_escrow ORDER BY escrow_id"
    ).fetchall()
    markers: list[dict[str, object]] = []
    for row in rows:
        escrow_id = str(row[0])
        lock_fact = {
            "escrow_id": row[0],
            "payer_account_id": row[1],
            "amount": row[2],
            "task_id": row[3],
            "created_at": row[5],
            "event_id": row[7],
        }
        resolution_hash = None
        if row[4] in {"released", "split"}:
            tx_ids = [
                str(item[0])
                for item in connection.execute(
                    "SELECT tx_id FROM bank_transactions "
                    "WHERE type='escrow_release' AND reference=? ORDER BY tx_id",
                    (escrow_id,),
                ).fetchall()
            ]
            resolution_hash = _hash_json(
                {
                    "escrow_id": escrow_id,
                    "observed_status": row[4],
                    "resolved_at": row[6],
                    "transaction_ids": tx_ids,
                }
            )
        markers.append(
            {
                "escrow_id": escrow_id,
                "observed_status": row[4],
                "lock_fact_sha256": _hash_json(lock_fact),
                "resolution_fact_sha256": resolution_hash,
            }
        )
    return tuple(markers)


def _plan() -> _Plan:
    from pathlib import Path

    root = Path(__file__).with_name("schema_resources")
    sidecar = (root / "receipt_v2.sql").read_text()
    temporary = sqlite3.connect(":memory:")
    try:
        temporary.executescript(load_schema_sql(SchemaState.CURRENT_V2))
        row = temporary.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
        ).fetchone()
    finally:
        temporary.close()
    if row is None or not isinstance(row[0], str):
        raise MigrationError("plan_invalid", "Current v2 metadata SQL is unavailable", {})
    plan_hash = hashlib.sha256((sidecar + "\n" + row[0]).encode()).hexdigest()
    return _Plan(sidecar, row[0], plan_hash)


def _observe(source_path: Path, original_snapshot: dict[str, object]) -> str:
    try:
        connection = _open_read_only(source_path)
    except sqlite3.Error:
        return "unreadable_state"
    try:
        state = classify_schema(connection).state
        if state is SchemaState.CURRENT_V2:
            return "committed_v2_observed"
        snapshot = _database_snapshot(connection)
        if state is SchemaState.CURRENT_V1 and snapshot == original_snapshot:
            return "not_applied_v1_observed"
        return "inconsistent_state_observed"
    finally:
        connection.close()


def _raise_failure(
    reason: str,
    message: str,
    primary: BaseException,
    *,
    bound: BackupBoundSource,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
    observation: str,
    cleanup_codes: list[str],
) -> Never:
    evidence = _write_evidence_with_note(
        bound,
        {
            "status": reason,
            "observation": observation,
            "backup_id": backup_id,
            "manifest_sha256": manifest_sha256,
            "plan_sha256": plan.sha256,
            "cleanup_codes": cleanup_codes,
            "error_type": type(primary).__name__,
        },
        primary,
    )
    details: dict[str, object] = {
        "observation": observation,
        "cleanup_codes": cleanup_codes,
        "native_close_completed": "native_close_completed" in cleanup_codes,
    }
    if evidence is not None:
        details["evidence_path"] = str(evidence)
    if isinstance(primary, MigrationError) and reason != "commit_outcome_uncertain":
        primary.safe_details.update(details)
        raise primary
    raise MigrationError(reason, message, details) from primary


def _commit(connection: sqlite3.Connection) -> None:
    connection.execute("COMMIT").close()
    if connection.in_transaction:
        raise sqlite3.OperationalError("explicit COMMIT did not end the transaction")


def _fault(_point: str) -> None:
    return


def _hash_json(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


__all__ = ["migrate_v1_to_v2"]
