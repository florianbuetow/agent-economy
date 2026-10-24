"""Explicit verified migration from task-contract schema v3 to publication schema v4."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from service_auth.financial_identity import operation_id_for
from service_commons.config import ConfigurationError, parse_yaml_config_bytes
from service_commons.recovery_contract import BindingKind, KeypairRole, SourceFieldBindingV1

from db_gateway_service.financial_requests import canonical_request, parse_canonical_request
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
    _snapshot_value,
    _split_sql,
    _validate_database_health,
    _validate_timeout,
    _write_evidence,
    _write_evidence_with_note,
)
from db_gateway_service.money_audit import audit_money
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.recovery import RecoveryError, open_backup_bound_source
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_publication_financial import (
    PublicationEvidenceError,
    inspect_lock_facts,
)
from db_gateway_service.task_time import parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Never

    from service_commons.recovery_contract import RecoveryInventoryV1

    from db_gateway_service.recovery import BackupBoundSource


@dataclass(frozen=True, slots=True)
class _Observation:
    observation_id: str
    escrow_id: str
    task_id: str
    poster_id: str
    amount: int
    observed_fact_json: str
    observed_fact_sha256: str
    state: str
    refund_operation_id: str | None
    refund_request_json: str | None
    refund_request_sha256: str | None
    refund_receipt_event_id: int | None
    refunded_at_us: int | None
    result_json: str | None


@dataclass(frozen=True, slots=True)
class _Admission:
    snapshot: dict[str, object]
    metadata: tuple[object, ...]
    caller_kid: str
    source_snapshot_sha256: str
    legacy_task_ids: tuple[str, ...]
    observations: tuple[_Observation, ...]


@dataclass(frozen=True, slots=True)
class _Plan:
    sidecar_sql: str
    metadata_sql: str
    sha256: str


def migrate_v3_to_v4(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    busy_timeout_ms: int,
) -> MigrationResult:
    """Migrate one verified, quiescent v3 source without implicit chaining."""
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
            caller_kid = _posting_authority(inventory)
            source_snapshot_sha256 = hashlib.sha256(bound.database_path.read_bytes()).hexdigest()
            clone = _open_read_only(bound.database_path)
            clone_primary: BaseException | None = None
            try:
                admission = _admit_v3(
                    clone,
                    caller_kid=caller_kid,
                    source_snapshot_sha256=source_snapshot_sha256,
                )
            except BaseException as primary:
                clone_primary = primary
                raise
            finally:
                _close_read_only(clone, primary=clone_primary, label="v3 admission clone")
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


def _posting_authority(inventory: RecoveryInventoryV1) -> str:
    configs = {config.logical_id: config for config in inventory.configs}
    try:
        board = _read_config(configs["task-board"].captured_path)
        gateway = _read_config(configs["db-gateway"].captured_path)
        kid = board["db_gateway"]["signing"]["kid"]
        trusted = gateway["auth"]["trusted_callers"][kid]
        capabilities = trusted["capabilities"]
        public_path = trusted["public_key_path"]
        private_path = board["db_gateway"]["signing"]["private_key_path"]
    except (KeyError, TypeError, ValueError, ConfigurationError) as exc:
        raise MigrationError(
            "posting_authority_invalid",
            "Backed-up Task Board posting authority is incomplete",
            {},
        ) from exc
    if (
        not isinstance(kid, str)
        or not kid
        or not isinstance(capabilities, list)
        or any(not isinstance(value, str) for value in capabilities)
        or "gateway.board.write" not in capabilities
        or not isinstance(public_path, str)
        or not public_path
        or not isinstance(private_path, str)
        or not private_path
    ):
        _authority_invalid(kid)
    public_binding = _binding(
        inventory,
        config_id="db-gateway",
        field_path=("auth", "trusted_callers", kid, "public_key_path"),
    )
    private_binding = _binding(
        inventory,
        config_id="task-board",
        field_path=("db_gateway", "signing", "private_key_path"),
    )
    keypairs = [item for item in inventory.keypairs if item.handle == kid]
    if (
        public_binding is None
        or private_binding is None
        or public_binding.binding_kind is not BindingKind.FILESYSTEM
        or private_binding.binding_kind is not BindingKind.FILESYSTEM
        or public_binding.captured_value != public_path
        or private_binding.captured_value != private_path
        or len(keypairs) != 1
        or keypairs[0].role is not KeypairRole.GATEWAY
        or keypairs[0].public_resource_id != public_binding.resource_id
        or keypairs[0].private_resource_id != private_binding.resource_id
    ):
        _authority_invalid(kid)
    return kid


def _read_config(path: Path) -> dict[str, Any]:
    value = parse_yaml_config_bytes(path.read_bytes(), source=str(path))
    if any(not isinstance(key, str) for key in value):
        raise ValueError("configuration root must be an object")
    return value


def _binding(
    inventory: RecoveryInventoryV1,
    *,
    config_id: str,
    field_path: tuple[str, ...],
) -> SourceFieldBindingV1 | None:
    matches = [
        binding
        for binding in inventory.field_bindings
        if binding.config_id == config_id and binding.field_path == field_path
    ]
    return matches[0] if len(matches) == 1 else None


def _authority_invalid(kid: object) -> Never:
    details: dict[str, object] = {"configured_kid": kid} if isinstance(kid, str) and kid else {}
    raise MigrationError(
        "posting_authority_invalid",
        "Backed-up Task Board posting authority is not trusted for Board writes",
        details,
    )


def _admit_v3(
    connection: sqlite3.Connection,
    *,
    caller_kid: str,
    source_snapshot_sha256: str,
) -> _Admission:
    classification = classify_schema(connection)
    if classification.state is not SchemaState.CURRENT_V3:
        raise MigrationError(
            "source_not_v3",
            "Verified backup source is not the exact supported v3 schema",
            {"source_schema": classification.state.value},
        )
    _validate_database_health(connection)
    _validate_task_escrow_references(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Source money audit failed", {})
    task_audit = audit_task_contracts(connection)
    if not task_audit.contracts_clean:
        raise MigrationError(
            "task_contract_audit_failed",
            "Source task-contract audit failed",
            {"finding_codes": sorted({item.code for item in task_audit.findings})},
        )
    observations = _derive_observations(
        connection,
        source_snapshot_sha256=source_snapshot_sha256,
    )
    if not audit_receipt_ownership(connection).ownership_clean:
        raise MigrationError("receipt_audit_failed", "Source receipt audit failed", {})
    metadata = connection.execute("SELECT * FROM schema_metadata").fetchone()
    if metadata is None:
        raise MigrationError("source_not_v3", "Source metadata is missing", {})
    legacy_task_ids = tuple(
        sorted(
            (str(row[0]) for row in connection.execute("SELECT task_id FROM board_tasks")),
            key=lambda value: value.encode("utf-8"),
        )
    )
    return _Admission(
        snapshot=_database_snapshot(connection, tables=None),
        metadata=tuple(metadata),
        caller_kid=caller_kid,
        source_snapshot_sha256=source_snapshot_sha256,
        legacy_task_ids=legacy_task_ids,
        observations=observations,
    )


def _validate_task_escrow_references(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT t.task_id,t.poster_id,t.reward,t.escrow_id,e.task_id,e.payer_account_id,e.amount "
        "FROM board_tasks t LEFT JOIN bank_escrow e ON e.escrow_id=t.escrow_id "
        "ORDER BY t.task_id"
    ).fetchall()
    for row in rows:
        if row[4:] != (row[0], row[1], row[2]):
            raise MigrationError(
                "task_escrow_reference_conflict",
                "A published task cites an escrow with contradictory immutable terms",
                {"task_id": str(row[0]), "escrow_id": str(row[3])},
            )


def _derive_observations(
    connection: sqlite3.Connection,
    *,
    source_snapshot_sha256: str,
) -> tuple[_Observation, ...]:
    rows = connection.execute(
        "SELECT e.escrow_id,e.payer_account_id,e.amount,e.task_id,e.status,e.created_at,"
        "e.resolved_at,e.event_id FROM bank_escrow e "
        "LEFT JOIN board_tasks t ON t.escrow_id=e.escrow_id "
        "WHERE t.task_id IS NULL ORDER BY e.escrow_id"
    ).fetchall()
    observations = [
        _derive_observation(
            connection,
            tuple(row),
            source_snapshot_sha256=source_snapshot_sha256,
        )
        for row in rows
    ]
    observations.sort(key=lambda item: item.observation_id.encode("utf-8"))
    return tuple(observations)


def _derive_observation(
    connection: sqlite3.Connection,
    escrow: tuple[object, ...],
    *,
    source_snapshot_sha256: str,
) -> _Observation:
    escrow_id, poster_id, amount, task_id, status, _created_at, resolved_at, _event_id = escrow
    safe: dict[str, object] = {
        "escrow_id": str(escrow_id),
        "task_id": str(task_id),
    }
    if (
        not isinstance(escrow_id, str)
        or not isinstance(task_id, str)
        or not isinstance(poster_id, str)
        or type(amount) is not int
    ):
        raise MigrationError(
            "posting_orphan_lock_unproven", "Posting orphan identity is invalid", safe
        )
    lock_provenance = _lock_provenance(connection, escrow, safe=safe)
    refund_origin: str
    existing_refund: dict[str, object] | None
    if status == "locked" and resolved_at is None:
        refund_origin = "new_refund"
        existing_refund = None
    elif status == "split":
        raise MigrationError(
            "posting_orphan_split", "Posting orphan was split and cannot be refunded", safe
        )
    elif status == "released" and isinstance(resolved_at, str):
        refund_origin = "existing_refund"
        existing_refund = _refund_provenance(connection, escrow, safe=safe)
    else:
        raise MigrationError(
            "posting_orphan_lock_unproven", "Posting orphan state is not supported", safe
        )
    fact = {
        "observation_version": 1,
        "source_snapshot_sha256": source_snapshot_sha256,
        "escrow": {
            "escrow_id": escrow[0],
            "payer_account_id": escrow[1],
            "amount": escrow[2],
            "task_id": escrow[3],
            "status": escrow[4],
            "created_at": escrow[5],
            "resolved_at": escrow[6],
            "event_id": escrow[7],
        },
        "lock_provenance": lock_provenance,
        "refund_origin": refund_origin,
        "existing_refund_provenance": existing_refund,
    }
    fact_json = _canonical_json(fact)
    observation_identity = f"agent-economy:posting-orphan:{source_snapshot_sha256}:{escrow_id}"
    observation_id = f"obs-{uuid5(NAMESPACE_URL, observation_identity)}"
    if refund_origin == "new_refund":
        operation_id = operation_id_for("task-posting-refund", (escrow_id,))
        request = canonical_request(
            "escrow_release",
            {
                "escrow_id": escrow_id,
                "recipient_account_id": poster_id,
                "source_reference": f"task:{task_id}:posting_refund:{escrow_id}",
            },
        )
        return _Observation(
            observation_id,
            escrow_id,
            task_id,
            poster_id,
            amount,
            fact_json,
            _sha256(fact_json),
            "refund_pending",
            operation_id,
            request.canonical_json,
            request.request_sha256,
            None,
            None,
            None,
        )
    assert existing_refund is not None
    native = existing_refund["kind"] == "native"
    receipt = _receipt(connection, str(existing_refund["operation_id"])) if native else None
    result_json = (
        str(receipt[4])
        if receipt is not None
        else _canonical_json(
            {
                "refund_origin": "existing_refund",
                "settlement_provenance_kind": "legacy_observation",
                "escrow_status_at_cutover": "released",
            }
        )
    )
    return _Observation(
        observation_id,
        escrow_id,
        task_id,
        poster_id,
        amount,
        fact_json,
        _sha256(fact_json),
        "refunded",
        str(existing_refund["operation_id"]) if native else None,
        str(receipt[1]) if receipt is not None else None,
        str(receipt[2]) if receipt is not None else None,
        _strict_int(receipt[3]) if receipt is not None else None,
        parse_utc_text_to_epoch_us(resolved_at),
        result_json,
    )


def _lock_provenance(
    connection: sqlite3.Connection,
    escrow: tuple[object, ...],
    *,
    safe: dict[str, object],
) -> dict[str, object]:
    receipts = connection.execute(
        "SELECT operation_id,canonical_request_json,request_sha256,event_id "
        "FROM bank_operation_receipts WHERE escrow_id=? AND operation_kind='escrow_lock' "
        "ORDER BY operation_id",
        (escrow[0],),
    ).fetchall()
    if len(receipts) == 1:
        receipt = tuple(receipts[0])
        try:
            proof = inspect_lock_facts(
                connection.cursor(),
                operation_id=str(receipt[0]),
                payer_account_id=str(escrow[1]),
                task_id=str(escrow[3]),
                amount=_strict_int(escrow[2]),
                require_locked=escrow[4] == "locked",
            )
        except PublicationEvidenceError as exc:
            raise MigrationError(
                "posting_orphan_lock_unproven", "Posting orphan lock receipt is invalid", safe
            ) from exc
        if proof is None or proof.escrow_id != escrow[0]:
            raise MigrationError(
                "posting_orphan_lock_unproven", "Posting orphan lock receipt is invalid", safe
            )
        return {
            "kind": "native",
            "operation_id": receipt[0],
            "request_sha256": proof.request_sha256,
            "event_id": proof.receipt_event_id,
            "transaction_ids": _transaction_ids(connection, str(receipt[0])),
        }
    if receipts:
        raise MigrationError(
            "posting_orphan_lock_unproven", "Posting orphan has ambiguous lock receipts", safe
        )
    marker = connection.execute(
        "SELECT cutover_singleton,lock_fact_sha256 FROM bank_legacy_escrow_observations "
        "WHERE escrow_id=?",
        (escrow[0],),
    ).fetchone()
    transactions = _legacy_transactions(
        connection,
        transaction_type="escrow_lock",
        reference=str(escrow[3]),
        account_id=str(escrow[1]),
    )
    if marker is None or marker[0] != 1 or not isinstance(marker[1], str) or not transactions:
        raise MigrationError(
            "posting_orphan_lock_unproven", "Posting orphan lock provenance is incomplete", safe
        )
    return {
        "kind": "legacy",
        "cutover_singleton": 1,
        "lock_fact_sha256": marker[1],
        "transaction_observations": transactions,
    }


def _refund_provenance(
    connection: sqlite3.Connection,
    escrow: tuple[object, ...],
    *,
    safe: dict[str, object],
) -> dict[str, object]:
    rows = connection.execute(
        "SELECT operation_id,canonical_request_json,request_sha256,event_id,source_reference "
        "FROM bank_operation_receipts WHERE escrow_id=? "
        "AND operation_kind IN ('escrow_release','escrow_split') ORDER BY operation_id",
        (escrow[0],),
    ).fetchall()
    if len(rows) == 1:
        row = tuple(rows[0])
        try:
            request = parse_canonical_request(str(row[1]))
            payload = json.loads(request.canonical_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MigrationError(
                "posting_orphan_foreign_recipient",
                "Posting orphan refund receipt is invalid",
                safe,
            ) from exc
        if request.operation_kind == "escrow_split":
            raise MigrationError(
                "posting_orphan_split", "Posting orphan was split and cannot be refunded", safe
            )
        if (
            request.operation_kind != "escrow_release"
            or payload.get("escrow_id") != escrow[0]
            or payload.get("recipient_account_id") != escrow[1]
            or row[2] != request.request_sha256
        ):
            raise MigrationError(
                "posting_orphan_foreign_recipient",
                "Posting orphan was released to a foreign recipient",
                safe,
            )
        return {
            "kind": "native",
            "operation_id": row[0],
            "request_sha256": row[2],
            "event_id": row[3],
            "transaction_ids": _transaction_ids(connection, str(row[0])),
            "source_reference": row[4],
        }
    if rows:
        raise MigrationError(
            "posting_orphan_foreign_recipient", "Posting orphan settlement is ambiguous", safe
        )
    marker = connection.execute(
        "SELECT cutover_singleton,resolution_fact_sha256 "
        "FROM bank_legacy_escrow_observations WHERE escrow_id=?",
        (escrow[0],),
    ).fetchone()
    transactions = _legacy_transactions(
        connection,
        transaction_type="escrow_release",
        reference=str(escrow[0]),
        account_id=str(escrow[1]),
    )
    if marker is None or marker[0] != 1 or not isinstance(marker[1], str) or not transactions:
        raise MigrationError(
            "posting_orphan_foreign_recipient",
            "Posting orphan refund recipient is not proven",
            safe,
        )
    return {
        "kind": "legacy",
        "cutover_singleton": 1,
        "resolution_fact_sha256": marker[1],
        "transaction_observations": transactions,
    }


def _receipt(connection: sqlite3.Connection, operation_id: str) -> tuple[object, ...]:
    row = connection.execute(
        "SELECT operation_id,canonical_request_json,request_sha256,event_id,result_json "
        "FROM bank_operation_receipts WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is None:
        raise MigrationError("receipt_audit_failed", "Receipt disappeared during admission", {})
    return tuple(row)


def _transaction_ids(connection: sqlite3.Connection, operation_id: str) -> list[str]:
    return sorted(
        (
            str(row[0])
            for row in connection.execute(
                "SELECT transaction_id FROM bank_operation_legs "
                "WHERE operation_id=? AND transaction_id IS NOT NULL",
                (operation_id,),
            )
        ),
        key=lambda value: value.encode("utf-8"),
    )


def _legacy_transactions(
    connection: sqlite3.Connection,
    *,
    transaction_type: str,
    reference: str,
    account_id: str,
) -> list[dict[str, object]]:
    return [
        {"tx_id": row[0], "observed_kind": row[1], "observed_fact_sha256": row[2]}
        for row in connection.execute(
            "SELECT o.tx_id,o.observed_kind,o.observed_fact_sha256 "
            "FROM bank_legacy_transaction_observations o "
            "JOIN bank_transactions t ON t.tx_id=o.tx_id "
            "WHERE t.type=? AND t.reference=? AND t.account_id=? ORDER BY o.tx_id",
            (transaction_type, reference, account_id),
        ).fetchall()
    ]


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
        _validate_live_v3(writer, admission)
        bound.verify_during_write()
        _apply_v4(
            writer,
            admission=admission,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
        _validate_v4_transaction(
            writer,
            admission=admission,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
            plan=plan,
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
        observation = _observe(bound.source_database_path, admission.snapshot)
        reason = "commit_outcome_uncertain" if commit_started else "migration_failed"
        message = (
            "Migration commit outcome is uncertain"
            if commit_started
            else "Migration failed before commit"
        )
        _raise_failure(
            reason,
            message,
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


def _apply_v4(
    connection: sqlite3.Connection,
    *,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    for statement in _split_sql(plan.sidecar_sql):
        connection.execute(statement)
    connection.executemany(
        "INSERT INTO board_legacy_posting_observations VALUES "
        "(?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
        tuple(
            (
                item.observation_id,
                item.escrow_id,
                item.task_id,
                item.poster_id,
                item.amount,
                item.observed_fact_json,
                item.observed_fact_sha256,
                admission.caller_kid,
                item.state,
                item.refund_operation_id,
                item.refund_request_json,
                item.refund_request_sha256,
                item.refund_receipt_event_id,
                item.refunded_at_us,
                item.result_json,
            )
            for item in admission.observations
        ),
    )
    task_hash = _sha256(_canonical_json(list(admission.legacy_task_ids)))
    observation_attestation = [
        [item.observation_id, item.escrow_id, item.observed_fact_sha256]
        for item in admission.observations
    ]
    connection.execute("DELETE FROM board_publication_cutover WHERE singleton=1")
    connection.execute(
        "INSERT INTO board_publication_cutover VALUES (1,1,'migrated',3,?,?,?,?,?,?,?,?,?)",
        (
            admission.source_snapshot_sha256,
            plan.sha256,
            backup_id,
            manifest_sha256,
            admission.caller_kid,
            task_hash,
            len(admission.legacy_task_ids),
            _sha256(_canonical_json(observation_attestation)),
            len(admission.observations),
        ),
    )
    connection.execute("DROP TABLE schema_metadata")
    connection.execute(plan.metadata_sql)
    connection.execute(
        "INSERT INTO schema_metadata VALUES (1,4,3,?,?)",
        (backup_id, manifest_sha256),
    )


def _validate_live_v3(connection: sqlite3.Connection, admission: _Admission) -> None:
    if classify_schema(connection).state is not SchemaState.CURRENT_V3:
        raise MigrationError("live_source_changed", "Live source is no longer exact v3", {})
    if _database_snapshot(connection, tables=None) != admission.snapshot:
        raise MigrationError("live_source_changed", "Live source data changed after backup", {})
    metadata = connection.execute("SELECT * FROM schema_metadata").fetchone()
    if metadata is None or tuple(metadata) != admission.metadata:
        raise MigrationError("live_source_changed", "Live source metadata changed after backup", {})
    repeated = _admit_v3(
        connection,
        caller_kid=admission.caller_kid,
        source_snapshot_sha256=admission.source_snapshot_sha256,
    )
    if repeated != admission:
        raise MigrationError("live_source_changed", "Live posting history changed after backup", {})


def _validate_v4_transaction(
    connection: sqlite3.Connection,
    *,
    admission: _Admission,
    backup_id: str,
    manifest_sha256: str,
    plan: _Plan,
) -> None:
    if classify_schema(connection).state is not SchemaState.CURRENT_V4:
        raise MigrationError("v4_validation_failed", "Migrated schema is not exact v4", {})
    _validate_database_health(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Migrated money audit failed", {})
    if not audit_receipt_ownership(connection).ownership_clean:
        raise MigrationError("receipt_audit_failed", "Migrated receipt audit failed", {})
    if not audit_task_contracts(connection).contracts_clean:
        raise MigrationError("task_contract_audit_failed", "Migrated task audit failed", {})
    publication_audit = audit_publications(connection)
    if not publication_audit.publications_clean:
        raise MigrationError(
            "publication_audit_failed",
            "Migrated publication audit failed",
            {"finding_codes": sorted({finding.code for finding in publication_audit.findings})},
        )
    preserved = _database_snapshot(connection, tables=tuple(admission.snapshot))
    if preserved != admission.snapshot:
        raise MigrationError("data_preservation_failed", "Existing domain data changed", {})
    if connection.execute("SELECT COUNT(*) FROM board_task_publications").fetchone() != (0,):
        raise MigrationError("native_history_invented", "Migration created native postings", {})
    if connection.execute("SELECT * FROM schema_metadata").fetchone() != (
        1,
        4,
        3,
        backup_id,
        manifest_sha256,
    ):
        raise MigrationError("provenance_failed", "Migrated metadata is incorrect", {})
    cutover = connection.execute("SELECT * FROM board_publication_cutover").fetchone()
    expected = (
        1,
        1,
        "migrated",
        3,
        admission.source_snapshot_sha256,
        plan.sha256,
        backup_id,
        manifest_sha256,
        admission.caller_kid,
        _sha256(_canonical_json(list(admission.legacy_task_ids))),
        len(admission.legacy_task_ids),
        _sha256(
            _canonical_json(
                [
                    [item.observation_id, item.escrow_id, item.observed_fact_sha256]
                    for item in admission.observations
                ]
            )
        ),
        len(admission.observations),
    )
    if cutover is None or tuple(cutover) != expected:
        raise MigrationError("provenance_failed", "Publication cutover is incorrect", {})


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
            _validate_v4_transaction(
                connection,
                admission=admission,
                backup_id=backup_id,
                manifest_sha256=manifest_sha256,
                plan=plan,
            )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            _close_read_only(connection, primary=primary, label="fresh v4 validation")
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
        cleanup_primary = MigrationError(
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
            cleanup_primary,
        )
        if evidence is not None:
            cleanup_primary.safe_details["evidence_path"] = str(evidence)
        raise cleanup_primary from cleanup_error
    try:
        evidence = _write_evidence(
            bound,
            {
                "status": "committed_v4_verified",
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
            },
        )
    except BaseException as primary:
        raise MigrationError(
            "post_commit_evidence_failed",
            "Migration committed but evidence publication failed",
            {"observation": "committed_v4_observed"},
        ) from primary
    return MigrationResult(
        applied=True,
        schema_version=4,
        backup_id=backup_id,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan.sha256,
        observation="committed_v4_verified",
        evidence_path=evidence,
    )


def _database_snapshot(
    connection: sqlite3.Connection,
    *,
    tables: tuple[str, ...] | None,
) -> dict[str, object]:
    selected = (
        list(tables)
        if tables is not None
        else [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'schema_metadata' ORDER BY name"
            ).fetchall()
        ]
    )
    if (
        tables is None
        and connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone()
    ):
        selected.append("sqlite_sequence")
    snapshot: dict[str, object] = {}
    for table in selected:
        columns = [
            (int(row[0]), str(row[1]), int(row[5]))
            for row in connection.execute(f'PRAGMA table_xinfo("{table}")').fetchall()  # nosec B608
            if int(row[6]) == 0
        ]
        projection = ", ".join(f'"{name}"' for _ordinal, name, _pk in columns)
        table_info = connection.execute("PRAGMA table_list").fetchall()
        without_rowid = next(int(row[4]) for row in table_info if str(row[1]) == table)
        if without_rowid:
            keys = [name for _ordinal, name, pk in sorted(columns, key=lambda item: item[2]) if pk]
            order = ", ".join(f'"{name}"' for name in keys)
            query = f'SELECT {projection} FROM "{table}" ORDER BY {order}'  # nosec B608
        else:
            query = f'SELECT rowid, {projection} FROM "{table}" ORDER BY rowid'  # nosec B608
        rows = connection.execute(query).fetchall()
        snapshot[table] = [[_snapshot_value(value) for value in tuple(row)] for row in rows]
    return snapshot


def _plan() -> _Plan:
    from pathlib import Path

    sidecar = (
        Path(__file__).with_name("schema_resources").joinpath("publication_v4.sql").read_text()
    )
    temporary = sqlite3.connect(":memory:")
    try:
        temporary.executescript(load_schema_sql(SchemaState.CURRENT_V4))
        row = temporary.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
        ).fetchone()
    finally:
        temporary.close()
    if row is None or not isinstance(row[0], str):
        raise MigrationError("plan_invalid", "Current v4 metadata SQL is unavailable", {})
    digest = hashlib.sha256((sidecar + "\n" + row[0]).encode()).hexdigest()
    return _Plan(sidecar, row[0], digest)


def _observe(source_path: Path, original_snapshot: dict[str, object]) -> str:
    try:
        connection = _open_read_only(source_path)
    except sqlite3.Error:
        return "unreadable_state"
    try:
        state = classify_schema(connection).state
        if state is SchemaState.CURRENT_V4:
            return "committed_v4_observed"
        snapshot = _database_snapshot(connection, tables=None)
        if state is SchemaState.CURRENT_V3 and snapshot == original_snapshot:
            return "not_applied_v3_observed"
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


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strict_int(value: object) -> int:
    if type(value) is not int:
        raise MigrationError("receipt_audit_failed", "Receipt integer is invalid", {})
    return value


__all__ = ["migrate_v3_to_v4"]
