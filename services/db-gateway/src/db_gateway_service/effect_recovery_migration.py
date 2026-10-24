"""Explicit verified migration from publication schema v4 to recovery schema v5."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from service_commons.config import ConfigurationError

from db_gateway_service import task_time
from db_gateway_service.effect_recovery_audit import audit_effect_recovery
from db_gateway_service.financial_effect_projection import (
    build_legacy_financial_intent,
    evaluate_financial_receipt,
    evaluate_native_financial_evidence,
)
from db_gateway_service.migration import (
    _ALLOWED_ARTIFACT_FINDINGS,
    MigrationError,
    MigrationResult,
    _close_read_only,
    _configure_migration_connection,
    _dispose_writer,
    _open_live_writer,
    _open_read_only,
    _rollback_with_note,
    _split_sql,
    _validate_database_health,
    _validate_timeout,
    _write_evidence,
    _write_evidence_with_note,
)
from db_gateway_service.money_audit import audit_money
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.publication_migration import (
    _database_snapshot,
    _posting_authority,
    _read_config,
)
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.recovery import open_backup_bound_source
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from db_gateway_service.task_contract_audit import audit_task_contracts

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Never

    from service_commons.recovery_contract import RecoveryInventoryV1

    from db_gateway_service.recovery import BackupBoundSource

_OPERATION_ID = re.compile(r"op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_OBSERVATION_ID = re.compile(r"obs-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SUPPORTED_LEGACY = frozenset({"worker_settlement_pending", "worker_settlement_succeeded"})
_MAX_TIME_US = 253402300799999999


@dataclass(frozen=True, slots=True)
class _NativeSource:
    operation_id: str
    task_id: str
    caller_kid: str
    effect_kind: str
    effect_operation_id: str
    request_sha256: str
    operation_state: str
    effect_state: str
    completion_event_id: int | None


@dataclass(frozen=True, slots=True)
class _LegacySource:
    observation_id: str
    task_id: str
    observation_kind: str
    effect_operation_id: str
    observed_fact_sha256: str
    owner_caller_kid: str


@dataclass(frozen=True, slots=True)
class _Admission:
    snapshot: dict[str, object]
    metadata: tuple[object, ...]
    source_snapshot_sha256: str
    caller_kid: str
    migration_time_us: int
    native_sources: tuple[_NativeSource, ...]
    legacy_sources: tuple[_LegacySource, ...]
    native_members: tuple[tuple[object, ...], ...]
    legacy_members: tuple[tuple[object, ...], ...]
    combined_members: tuple[tuple[object, ...], ...]


@dataclass(frozen=True, slots=True)
class _Plan:
    sidecar_sql: str
    metadata_sql: str
    sha256: str


def migrate_v4_to_v5(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    busy_timeout_ms: int,
    current_task_board_caller_kid: str,
) -> MigrationResult:
    """Migrate one verified, quiescent v4 source without implicit chaining."""
    _validate_timeout(busy_timeout_ms)
    plan = _plan()
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
        caller_kid = _current_board_authority(inventory, current_task_board_caller_kid)
        source_snapshot_sha256 = hashlib.sha256(bound.database_path.read_bytes()).hexdigest()
        clone = _open_read_only(bound.database_path)
        primary: BaseException | None = None
        try:
            admission = _admit_v4(
                clone,
                caller_kid=caller_kid,
                source_snapshot_sha256=source_snapshot_sha256,
                migration_time_us=_migration_time(),
            )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            _close_read_only(clone, primary=primary, label="v4 recovery admission clone")
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


def _current_board_authority(
    inventory: RecoveryInventoryV1,
    requested_kid: str,
) -> str:
    if not isinstance(requested_kid, str) or not requested_kid:
        raise MigrationError(
            "current_task_board_caller_untrusted",
            "Current Task Board caller KID is not trusted",
            {},
        )
    configs = {config.logical_id: config for config in inventory.configs}
    try:
        gateway = _read_config(configs["db-gateway"].captured_path)
        board = _read_config(configs["task-board"].captured_path)
        trusted = gateway["auth"]["trusted_callers"][requested_kid]
        capabilities = trusted["capabilities"]
        configured_kid = board["db_gateway"]["signing"]["kid"]
    except (KeyError, TypeError, ValueError, ConfigurationError) as exc:
        raise MigrationError(
            "current_task_board_caller_untrusted",
            "Current Task Board caller KID is not trusted",
            {"configured_kid": requested_kid},
        ) from exc
    if not isinstance(capabilities, list) or any(
        not isinstance(value, str) for value in capabilities
    ):
        raise MigrationError(
            "current_task_board_caller_untrusted",
            "Current Task Board caller capabilities are invalid",
            {"configured_kid": requested_kid},
        )
    if "gateway.board.write" not in capabilities:
        raise MigrationError(
            "current_task_board_caller_capability_missing",
            "Current Task Board caller lacks Gateway Board write capability",
            {"configured_kid": requested_kid},
        )
    if configured_kid != requested_kid:
        raise MigrationError(
            "current_task_board_caller_untrusted",
            "Current Task Board caller KID does not match backed-up authority",
            {"configured_kid": requested_kid},
        )
    try:
        proven_kid = _posting_authority(inventory)
    except MigrationError as exc:
        raise MigrationError(
            "current_task_board_caller_untrusted",
            "Current Task Board caller authority is not fully proven",
            {"configured_kid": requested_kid},
        ) from exc
    if proven_kid != requested_kid:
        raise MigrationError(
            "current_task_board_caller_untrusted",
            "Current Task Board caller authority does not match",
            {"configured_kid": requested_kid},
        )
    return requested_kid


def _migration_time() -> int:
    value = task_time.utc_now_us()
    if type(value) is not int or not 0 <= value <= _MAX_TIME_US:
        raise MigrationError(
            "migration_clock_invalid",
            "Gateway migration clock is outside the supported epoch range",
            {},
        )
    return value


def _admit_v4(
    connection: sqlite3.Connection,
    *,
    caller_kid: str,
    source_snapshot_sha256: str,
    migration_time_us: int,
) -> _Admission:
    classification = classify_schema(connection)
    if classification.state is not SchemaState.CURRENT_V4:
        raise MigrationError(
            "source_not_v4",
            "Verified backup source is not the exact supported v4 schema",
            {"source_schema": classification.state.value},
        )
    metadata = connection.execute("SELECT * FROM schema_metadata").fetchone()
    if metadata is None:
        raise MigrationError("source_not_v4", "Source metadata is missing", {})
    _validate_raw_source_uniqueness(connection)
    native_sources = _native_sources(connection, caller_kid)
    legacy_sources = _legacy_sources(connection, caller_kid)
    _validate_unique_sources(native_sources, legacy_sources)
    _validate_database_health(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Source money audit failed", {})
    if not audit_receipt_ownership(connection).ownership_clean:
        raise MigrationError("receipt_audit_failed", "Source receipt audit failed", {})
    task_audit = audit_task_contracts(connection)
    if not task_audit.contracts_clean:
        raise MigrationError(
            "task_contract_audit_failed",
            "Source task-contract audit failed",
            {"finding_codes": sorted({item.code for item in task_audit.findings})},
        )
    publication_audit = audit_publications(connection)
    if not publication_audit.publications_clean:
        raise MigrationError(
            "publication_audit_failed",
            "Source publication audit failed",
            {"finding_codes": sorted({item.code for item in publication_audit.findings})},
        )
    native_members = tuple(
        (
            item.operation_id,
            item.task_id,
            item.caller_kid,
            item.effect_kind,
            item.effect_operation_id,
            item.request_sha256,
        )
        for item in native_sources
    )
    legacy_members = tuple(
        (
            item.observation_id,
            item.task_id,
            item.observation_kind,
            item.effect_operation_id,
            item.observed_fact_sha256,
            item.owner_caller_kid,
        )
        for item in legacy_sources
    )
    combined = [
        (
            "native_task_operation",
            item.operation_id,
            item.effect_operation_id,
            item.task_id,
            item.caller_kid,
            item.effect_kind,
        )
        for item in native_sources
    ]
    combined.extend(
        (
            "legacy_settlement_observation",
            item.observation_id,
            item.effect_operation_id,
            item.task_id,
            item.owner_caller_kid,
            "bank_release",
        )
        for item in legacy_sources
    )
    combined.sort(key=lambda item: str(item[1]).encode("utf-8"))
    return _Admission(
        snapshot=_database_snapshot(connection, tables=None),
        metadata=tuple(metadata),
        source_snapshot_sha256=source_snapshot_sha256,
        caller_kid=caller_kid,
        migration_time_us=migration_time_us,
        native_sources=native_sources,
        legacy_sources=legacy_sources,
        native_members=native_members,
        legacy_members=legacy_members,
        combined_members=tuple(combined),
    )


def _native_sources(
    connection: sqlite3.Connection,
    caller_kid: str,
) -> tuple[_NativeSource, ...]:
    rows = connection.execute(
        "SELECT operation_id,task_id,caller_kid,effect_kind,effect_operation_id,"
        "request_sha256,operation_state,effect_state,effect_intent_json "
        "FROM board_task_operations "
        "WHERE effect_kind IN ('bank_release','bank_split') ORDER BY operation_id"
    ).fetchall()
    result: list[_NativeSource] = []
    for row in rows:
        operation_id = str(row[0])
        effect_operation_id = str(row[4])
        if not _canonical_operation_id(operation_id) or not _canonical_operation_id(
            effect_operation_id
        ):
            _identity_invalid(operation_id)
        if effect_operation_id != operation_id:
            raise MigrationError(
                "effect_delivery_identity_conflict",
                "Native effect identity differs from its immutable operation identity",
                {"operation_id": operation_id},
            )
        owner = str(row[2])
        if owner != caller_kid:
            raise MigrationError(
                "effect_delivery_owner_mismatch",
                "Native effect source is not owned by the current Task Board caller",
                {"operation_id": operation_id},
            )
        operation_state, effect_state = str(row[6]), str(row[7])
        allowed = {
            ("claimed", "pending"),
            ("completed", "succeeded"),
            ("blocked", "conflict"),
        }
        if (operation_state, effect_state) not in allowed:
            raise MigrationError(
                "effect_delivery_source_state_invalid",
                "Native effect source state is not recoverable",
                {"operation_id": operation_id},
            )
        native = evaluate_native_financial_evidence(connection, operation_id)
        if native.receipt.state == "conflict" and operation_state != "blocked":
            raise MigrationError(
                "effect_delivery_receipt_conflict",
                "Native financial effect has contradictory receipt evidence",
                {"operation_id": operation_id},
            )
        expected_projection = {
            "claimed": "source_current",
            "completed": "target_current",
        }.get(operation_state)
        if expected_projection is not None and native.projection_state != expected_projection:
            raise MigrationError(
                "effect_delivery_source_state_invalid",
                "Native financial source and business projection are incoherent",
                {"operation_id": operation_id},
            )
        if operation_state == "blocked" and not native.block_reason_valid:
            raise MigrationError(
                "effect_delivery_source_state_invalid",
                "Blocked native source lacks contradictory financial evidence",
                {"operation_id": operation_id},
            )
        completion_event_id: int | None = None
        if operation_state == "completed":
            if native.receipt.state != "confirmed" or native.receipt.event_id is None:
                raise MigrationError(
                    "effect_delivery_receipt_missing",
                    "Completed native effect has no exact authoritative receipt",
                    {"operation_id": operation_id},
                )
            completion_event_id = native.receipt.event_id
        result.append(
            _NativeSource(
                operation_id,
                str(row[1]),
                owner,
                str(row[3]),
                effect_operation_id,
                str(row[5]),
                operation_state,
                effect_state,
                completion_event_id,
            )
        )
    return tuple(result)


def _legacy_sources(
    connection: sqlite3.Connection,
    caller_kid: str,
) -> tuple[_LegacySource, ...]:
    cutover = connection.execute(
        "SELECT caller_kid FROM board_publication_cutover WHERE singleton=1"
    ).fetchone()
    owner = None if cutover is None or cutover[0] is None else str(cutover[0])
    rows = connection.execute(
        "SELECT observation_id,task_id,observation_kind,external_reference,"
        "observed_fact_sha256,observed_fact_json,unresolved "
        "FROM board_legacy_effect_observations "
        "WHERE unresolved=1 ORDER BY observation_id"
    ).fetchall()
    candidates: list[_LegacySource] = []
    for row in rows:
        kind = str(row[2])
        if kind == "court_claim_succeeded":
            continue
        if kind not in _SUPPORTED_LEGACY:
            raise MigrationError(
                "effect_delivery_legacy_kind_unsupported",
                "Unresolved legacy financial history is not supported",
                {"observation_id": str(row[0])},
            )
        observation_id = str(row[0])
        effect_operation_id = str(row[3])
        if not _canonical_observation_id(observation_id) or not _canonical_operation_id(
            effect_operation_id
        ):
            _identity_invalid(observation_id)
        if owner is None:
            raise MigrationError(
                "effect_delivery_owner_unproven",
                "Legacy effect source has no proven publication-cutover owner",
                {"observation_id": observation_id},
            )
        if owner != caller_kid:
            raise MigrationError(
                "effect_delivery_owner_mismatch",
                "Legacy effect source is not owned by the current Task Board caller",
                {"observation_id": observation_id},
            )
        task = _task_mapping(connection, str(row[1]))
        intent = build_legacy_financial_intent(
            (kind, row[5], row[4], effect_operation_id, row[6]),
            task,
        )
        if intent is None:
            raise MigrationError(
                "effect_delivery_intent_invalid",
                "Legacy financial effect intent is not bound to immutable task terms",
                {"observation_id": observation_id},
            )
        evidence = evaluate_financial_receipt(connection, effect_operation_id, intent)
        if evidence.state == "conflict":
            raise MigrationError(
                "effect_delivery_receipt_conflict",
                "Legacy financial effect has contradictory receipt evidence",
                {"observation_id": observation_id},
            )
        if kind == "worker_settlement_succeeded" and evidence.state != "confirmed":
            raise MigrationError(
                "effect_delivery_receipt_missing",
                "Succeeded legacy settlement has no exact authoritative receipt",
                {"observation_id": observation_id},
            )
        candidates.append(
            _LegacySource(
                observation_id,
                str(row[1]),
                kind,
                effect_operation_id,
                str(row[4]),
                owner,
            )
        )
    return tuple(candidates)


def _task_mapping(
    connection: sqlite3.Connection,
    task_id: str,
) -> dict[str, object]:
    row = connection.execute(
        "SELECT task_id,poster_id,worker_id,escrow_id,reward,status "
        "FROM board_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise MigrationError(
            "effect_delivery_source_invalid",
            "Financial effect source has no task",
            {"task_id": task_id},
        )
    return {
        "task_id": row[0],
        "poster_id": row[1],
        "worker_id": row[2],
        "escrow_id": row[3],
        "reward": row[4],
        "status": row[5],
    }


def _validate_unique_sources(
    native: tuple[_NativeSource, ...],
    legacy: tuple[_LegacySource, ...],
) -> None:
    effect_ids = [item.effect_operation_id for item in native] + [
        item.effect_operation_id for item in legacy
    ]
    duplicate_effects = sorted(
        (value for value, count in Counter(effect_ids).items() if count > 1),
        key=lambda value: value.encode("utf-8"),
    )
    if duplicate_effects:
        raise MigrationError(
            "effect_delivery_identity_conflict",
            "Multiple recovery sources claim one financial effect identity",
            {"effect_operation_ids": duplicate_effects},
        )
    task_ids = [item.task_id for item in native] + [item.task_id for item in legacy]
    duplicate_tasks = sorted(
        (value for value, count in Counter(task_ids).items() if count > 1),
        key=lambda value: value.encode("utf-8"),
    )
    if duplicate_tasks:
        raise MigrationError(
            "effect_delivery_source_conflict",
            "Multiple financial recovery sources claim one task",
            {"task_ids": duplicate_tasks},
        )


def _validate_raw_source_uniqueness(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT task_id,effect_operation_id FROM board_task_operations "
        "WHERE effect_kind IN ('bank_release','bank_split') UNION ALL "
        "SELECT task_id,external_reference FROM board_legacy_effect_observations "
        "WHERE unresolved=1 AND observation_kind IN "
        "('worker_settlement_pending','worker_settlement_succeeded')"
    ).fetchall()
    effect_counts = Counter(str(row[1]) for row in rows)
    duplicate_effects = sorted(
        (value for value, count in effect_counts.items() if count > 1),
        key=lambda value: value.encode("utf-8"),
    )
    if duplicate_effects:
        raise MigrationError(
            "effect_delivery_identity_conflict",
            "Multiple recovery sources claim one financial effect identity",
            {"effect_operation_ids": duplicate_effects},
        )
    task_counts = Counter(str(row[0]) for row in rows)
    duplicate_tasks = sorted(
        (value for value, count in task_counts.items() if count > 1),
        key=lambda value: value.encode("utf-8"),
    )
    if duplicate_tasks:
        raise MigrationError(
            "effect_delivery_source_conflict",
            "Multiple financial recovery sources claim one task",
            {"task_ids": duplicate_tasks},
        )


def _identity_invalid(record_id: str) -> Never:
    raise MigrationError(
        "effect_delivery_identity_invalid",
        "Financial recovery source identity is not canonical",
        {"record_id": record_id},
    )


def _canonical_operation_id(value: str) -> bool:
    return _OPERATION_ID.fullmatch(value) is not None


def _canonical_observation_id(value: str) -> bool:
    return _OBSERVATION_ID.fullmatch(value) is not None


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
            "source_open_failed", "Live source could not be opened read-write", details
        ) from primary
    transaction_started = False
    commit_started = False
    try:
        _configure_migration_connection(writer, busy_timeout_ms)
        bound.verify_during_write()
        writer.execute("BEGIN IMMEDIATE")
        transaction_started = True
        _validate_live_v4(writer, admission)
        bound.verify_during_write()
        _apply_v5(
            writer,
            admission=admission,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
        _validate_v5_transaction(
            writer,
            admission=admission,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
        bound.verify_during_write()
        commit_started = True
        _commit(writer)
        transaction_started = False
    except BaseException as primary:
        cleanup_codes: list[str] = []
        if transaction_started and not commit_started:
            cleanup_codes.extend(_rollback_with_note(writer, primary))
        cleanup, _close_error = _dispose_writer(writer, primary)
        cleanup_codes.extend(cleanup)
        _raise_failure(
            "commit_outcome_uncertain" if commit_started else "migration_failed",
            "Migration commit outcome is uncertain"
            if commit_started
            else "Migration failed before commit",
            primary,
            bound=bound,
            admission=admission,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
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


def _validate_live_v4(connection: sqlite3.Connection, admission: _Admission) -> None:
    if classify_schema(connection).state is not SchemaState.CURRENT_V4:
        raise MigrationError("live_source_changed", "Live source is no longer exact v4", {})
    if _database_snapshot(connection, tables=None) != admission.snapshot:
        raise MigrationError("live_source_changed", "Live source data changed after backup", {})
    metadata = connection.execute("SELECT * FROM schema_metadata").fetchone()
    if metadata is None or tuple(metadata) != admission.metadata:
        raise MigrationError("live_source_changed", "Live source metadata changed after backup", {})
    repeated = _admit_v4(
        connection,
        caller_kid=admission.caller_kid,
        source_snapshot_sha256=admission.source_snapshot_sha256,
        migration_time_us=admission.migration_time_us,
    )
    if repeated != admission:
        raise MigrationError(
            "live_source_changed", "Live recovery history changed after backup", {}
        )


def _apply_v5(
    connection: sqlite3.Connection,
    *,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    for statement in _split_sql(plan.sidecar_sql):
        connection.execute(statement)
    connection.execute("DELETE FROM board_task_effect_delivery_cutover WHERE singleton=1")
    connection.execute(
        "INSERT INTO board_task_effect_delivery_cutover VALUES "
        "(1,1,'migrated',4,?,?,?,?,?,?,?,?,?,?,?)",
        (
            admission.source_snapshot_sha256,
            plan.sha256,
            backup_id,
            manifest_sha256,
            admission.caller_kid,
            len(admission.native_members),
            _digest(admission.native_members),
            len(admission.legacy_members),
            _digest(admission.legacy_members),
            len(admission.combined_members),
            _digest(admission.combined_members),
        ),
    )
    for native_source in admission.native_sources:
        if native_source.operation_state == "claimed":
            state = "pending"
            next_attempt = admission.migration_time_us
            failure = None
            failure_at = None
            completed_at = None
            event_id = None
        elif native_source.operation_state == "completed":
            state = "completed"
            next_attempt = None
            failure = None
            failure_at = None
            completed_at = admission.migration_time_us
            event_id = native_source.completion_event_id
        else:
            state = "integrity_blocked"
            next_attempt = None
            failure = "effect_evidence_conflict"
            failure_at = admission.migration_time_us
            completed_at = None
            event_id = None
        _insert_delivery(
            connection,
            effect_operation_id=native_source.effect_operation_id,
            task_id=native_source.task_id,
            source_kind="native_task_operation",
            native_operation_id=native_source.operation_id,
            legacy_observation_id=None,
            owner_caller_kid=native_source.caller_kid,
            effect_kind=native_source.effect_kind,
            delivery_state=state,
            enqueued_at_us=admission.migration_time_us,
            next_attempt_at_us=next_attempt,
            last_failure_code=failure,
            last_failure_at_us=failure_at,
            completed_at_us=completed_at,
            completion_event_id=event_id,
        )
    for legacy_source in admission.legacy_sources:
        _insert_delivery(
            connection,
            effect_operation_id=legacy_source.effect_operation_id,
            task_id=legacy_source.task_id,
            source_kind="legacy_settlement_observation",
            native_operation_id=None,
            legacy_observation_id=legacy_source.observation_id,
            owner_caller_kid=legacy_source.owner_caller_kid,
            effect_kind="bank_release",
            delivery_state="pending",
            enqueued_at_us=admission.migration_time_us,
            next_attempt_at_us=admission.migration_time_us,
            last_failure_code=None,
            last_failure_at_us=None,
            completed_at_us=None,
            completion_event_id=None,
        )
    connection.execute("DROP TABLE schema_metadata")
    connection.execute(plan.metadata_sql)
    connection.execute(
        "INSERT INTO schema_metadata VALUES (1,5,4,?,?)",
        (backup_id, manifest_sha256),
    )


def _insert_delivery(
    connection: sqlite3.Connection,
    *,
    effect_operation_id: str,
    task_id: str,
    source_kind: str,
    native_operation_id: str | None,
    legacy_observation_id: str | None,
    owner_caller_kid: str,
    effect_kind: str,
    delivery_state: str,
    enqueued_at_us: int,
    next_attempt_at_us: int | None,
    last_failure_code: str | None,
    last_failure_at_us: int | None,
    completed_at_us: int | None,
    completion_event_id: int | None,
) -> None:
    connection.execute(
        "INSERT INTO board_task_effect_deliveries (effect_operation_id,"
        "recovery_contract_version,task_id,source_kind,native_operation_id,"
        "legacy_observation_id,owner_caller_kid,effect_kind,delivery_state,"
        "enqueued_at_us,total_attempt_count,cycle_attempt_count,lease_generation,"
        "next_attempt_at_us,lease_owner_id,lease_acquired_at_us,lease_expires_at_us,"
        "last_failure_code,last_failure_at_us,operator_retry_count,"
        "last_operator_retry_at_us,last_operator_retry_caller_kid,"
        "next_evidence_check_at_us,completed_at_us,completion_event_id,cutover_singleton) "
        "VALUES (?,1,?,?,?,?,?,?,?, ?,0,0,0, ?,NULL,NULL,NULL, ?,?,0,NULL,NULL,NULL, ?,?,1)",
        (
            effect_operation_id,
            task_id,
            source_kind,
            native_operation_id,
            legacy_observation_id,
            owner_caller_kid,
            effect_kind,
            delivery_state,
            enqueued_at_us,
            next_attempt_at_us,
            last_failure_code,
            last_failure_at_us,
            completed_at_us,
            completion_event_id,
        ),
    )


def _validate_v5_transaction(
    connection: sqlite3.Connection,
    *,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    if classify_schema(connection).state is not SchemaState.CURRENT_V5:
        raise MigrationError("v5_validation_failed", "Migrated schema is not exact v5", {})
    _validate_database_health(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Migrated money audit failed", {})
    if not audit_receipt_ownership(connection).ownership_clean:
        raise MigrationError("receipt_audit_failed", "Migrated receipt audit failed", {})
    if not audit_task_contracts(connection).contracts_clean:
        raise MigrationError("task_contract_audit_failed", "Migrated task audit failed", {})
    if not audit_publications(connection).publications_clean:
        raise MigrationError("publication_audit_failed", "Migrated publication audit failed", {})
    recovery_audit = audit_effect_recovery(connection)
    if not recovery_audit.recovery_clean:
        raise MigrationError(
            "effect_recovery_audit_failed",
            "Migrated recovery audit failed",
            {"finding_codes": sorted({item.code for item in recovery_audit.findings})},
        )
    if _database_snapshot(connection, tables=tuple(admission.snapshot)) != admission.snapshot:
        raise MigrationError("data_preservation_failed", "Existing domain data changed", {})
    if connection.execute("SELECT * FROM schema_metadata").fetchone() != (
        1,
        5,
        4,
        backup_id,
        manifest_sha256,
    ):
        raise MigrationError("provenance_failed", "Migrated metadata is incorrect", {})
    expected_cutover = (
        1,
        1,
        "migrated",
        4,
        admission.source_snapshot_sha256,
        plan.sha256,
        backup_id,
        manifest_sha256,
        admission.caller_kid,
        len(admission.native_members),
        _digest(admission.native_members),
        len(admission.legacy_members),
        _digest(admission.legacy_members),
        len(admission.combined_members),
        _digest(admission.combined_members),
    )
    cutover = connection.execute("SELECT * FROM board_task_effect_delivery_cutover").fetchone()
    if cutover is None or tuple(cutover) != expected_cutover:
        raise MigrationError("provenance_failed", "Recovery cutover is incorrect", {})


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
        connection = _open_read_only(bound.source_database_path)
        primary: BaseException | None = None
        try:
            _validate_v5_transaction(
                connection,
                admission=admission,
                plan=plan,
                backup_id=backup_id,
                manifest_sha256=manifest_sha256,
            )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            _close_read_only(connection, primary=primary, label="fresh v5 validation")
    except BaseException as validation_error:
        if cleanup_error is not None:
            validation_error.add_note(f"Dynamic writer close also failed: {cleanup_error!r}")
        _raise_failure(
            "post_commit_validation_failed",
            "Migration committed but post-commit validation failed",
            validation_error,
            bound=bound,
            admission=admission,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
            cleanup_codes=cleanup_codes,
        )
    if cleanup_codes != ["native_close_completed"]:
        cleanup_failure = MigrationError(
            "post_commit_cleanup_failed",
            "Migration committed but writer cleanup failed",
            {"cleanup_codes": cleanup_codes, "native_close_completed": True},
        )
        evidence = _write_evidence_with_note(
            bound,
            {
                "status": "post_commit_cleanup_failed",
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
                "cleanup_codes": cleanup_codes,
            },
            cleanup_failure,
        )
        if evidence is not None:
            cleanup_failure.safe_details["evidence_path"] = str(evidence)
        raise cleanup_failure from cleanup_error
    try:
        evidence = _write_evidence(
            bound,
            {
                "status": "committed_v5_verified",
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
            },
        )
    except BaseException as primary:
        raise MigrationError(
            "post_commit_evidence_failed",
            "Migration committed but evidence publication failed",
            {"observation": "committed_v5_observed"},
        ) from primary
    return MigrationResult(
        applied=True,
        schema_version=5,
        backup_id=backup_id,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan.sha256,
        observation="committed_v5_verified",
        evidence_path=evidence,
    )


def _plan() -> _Plan:
    from pathlib import Path

    sidecar = (
        Path(__file__)
        .with_name("schema_resources")
        .joinpath("task_effect_recovery_v5.sql")
        .read_text()
    )
    temporary = sqlite3.connect(":memory:")
    try:
        temporary.executescript(load_schema_sql(SchemaState.CURRENT_V5))
        row = temporary.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
        ).fetchone()
    finally:
        temporary.close()
    if row is None or not isinstance(row[0], str):
        raise MigrationError("plan_invalid", "Current v5 metadata SQL is unavailable", {})
    digest = hashlib.sha256((sidecar + "\n" + row[0]).encode()).hexdigest()
    return _Plan(sidecar, row[0], digest)


def _observe(source_path: Path, original_snapshot: dict[str, object]) -> str:
    try:
        connection = _open_read_only(source_path)
    except sqlite3.Error:
        return "unreadable_state"
    try:
        state = classify_schema(connection).state
        if state is SchemaState.CURRENT_V5:
            return "committed_v5_observed"
        if (
            state is SchemaState.CURRENT_V4
            and _database_snapshot(connection, tables=None) == original_snapshot
        ):
            return "not_applied_v4_observed"
        return "inconsistent_state_observed"
    finally:
        connection.close()


def _raise_failure(
    reason: str,
    message: str,
    primary: BaseException,
    *,
    bound: BackupBoundSource,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
    cleanup_codes: list[str],
) -> Never:
    observation = _observe(bound.source_database_path, admission.snapshot)
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


def _digest(value: tuple[tuple[object, ...], ...]) -> str:
    payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["migrate_v4_to_v5"]
