"""MP-05 verified v3-to-v4 publication cutover acceptance tests.

Until ``db_gateway_service.publication_migration`` exists, the behavior tests
report a named capability gap after constructing and verifying their real v3
histories. The archived MP-05 lost-compensation suite remains the genuine
baseline behavior red; module absence is not presented as that product failure.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID, uuid4

import pytest
from service_auth.financial_identity import operation_id_for

from db_gateway_service import migration
from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, classify_schema
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.fakes.mp05_publication_migration_history import (
    LEGACY_REFUNDED_ESCROW_ID,
    LockedEscrow,
    RichCutoverHistory,
    V3Source,
    add_rich_cutover_history,
    create_funded_payer,
    invalidate_posting_authority,
    lock_escrow,
    open_v3_writer,
    prepare_v3_source,
    prepare_v3_source_with_legacy_refund,
    publish_legacy_task,
    publish_verified_backup,
    release_escrow,
    split_escrow,
    verify_backup_without_mutating,
)
from tests.unit import test_mp06_migration as old_migration

if TYPE_CHECKING:
    from service_commons.recovery_contract import RecoveryInventoryV1

pytestmark = pytest.mark.unit

_CALLER_KID = "gateway-task-board"
_NONDEFAULT_CALLER_KID = "gateway-board-cutover-alt"
_CAPABILITY_GAP = "Missing explicit backup-bound v3-to-v4 publication migration"


class _MigrateV3ToV4(Protocol):
    def __call__(
        self,
        inventory: RecoveryInventoryV1,
        *,
        backup_root: Path,
        backup_id: str,
        work_root: Path,
        busy_timeout_ms: int,
    ) -> migration.MigrationResult: ...


@dataclass(frozen=True, slots=True)
class _PreservationEvidence:
    database: dict[str, object]
    source_files: dict[str, bytes]
    backup_verification: dict[str, object]
    backup_digest: str


@dataclass(frozen=True, slots=True)
class _RejectionCase:
    name: str
    expected_reason: str


_REJECTION_CASES = (
    _RejectionCase("split", "posting_orphan_split"),
    _RejectionCase("foreign_recipient", "posting_orphan_foreign_recipient"),
    _RejectionCase("unproven_lock", "posting_orphan_lock_unproven"),
    _RejectionCase("conflicting_task_reference", "task_escrow_reference_conflict"),
)


def _migration_entry_point() -> _MigrateV3ToV4 | None:
    try:
        module = importlib.import_module("db_gateway_service.publication_migration")
    except ModuleNotFoundError as exc:
        if exc.name != "db_gateway_service.publication_migration":
            raise
        return None
    candidate = getattr(module, "migrate_v3_to_v4", None)
    return cast("_MigrateV3ToV4", candidate) if callable(candidate) else None


def _require_migration_entry_point() -> _MigrateV3ToV4:
    migrate = _migration_entry_point()
    if migrate is None:
        pytest.fail(_CAPABILITY_GAP)
    return migrate


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _open_immutable(database: Path) -> sqlite3.Connection:
    """Read one closed standalone SQLite file without creating WAL sidecars."""
    assert database.is_file()
    wal = Path(f"{database}-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _complete_snapshot(database: Path) -> dict[str, object]:
    """Snapshot every old object, typed row and sequence without touching bytes."""
    connection = _open_immutable(database)
    try:
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "AND name != 'schema_metadata' ORDER BY type, name"
        ).fetchall()
        rows: dict[str, object] = {}
        for _schema, table, kind, _columns, without_rowid, _strict in connection.execute(
            "PRAGMA table_list"
        ).fetchall():
            if kind != "table" or table.startswith("sqlite_") or table == "schema_metadata":
                continue
            columns = connection.execute(f'PRAGMA table_xinfo("{table}")').fetchall()
            projection = ", ".join(f'typeof("{column[1]}"), "{column[1]}"' for column in columns)
            if without_rowid:
                keys = sorted((column[5], column[1]) for column in columns if column[5])
                order = ", ".join(f'"{name}"' for _position, name in keys)
            else:
                projection = f"rowid, {projection}"
                order = "rowid"
            rows[table] = connection.execute(
                f'SELECT {projection} FROM "{table}" ORDER BY {order}'
            ).fetchall()
        return {
            "objects": objects,
            "rows": rows,
            "sequence": connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall(),
        }
    finally:
        connection.close()


def _legacy_task_ids(database: Path) -> list[str]:
    connection = _open_immutable(database)
    try:
        return [
            str(row[0]) for row in connection.execute("SELECT task_id FROM board_tasks").fetchall()
        ]
    finally:
        connection.close()


def _escrow_fact(database: Path, escrow_id: str) -> dict[str, object]:
    connection = _open_immutable(database)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT escrow_id, payer_account_id, amount, task_id, status, created_at, "
            "resolved_at, event_id FROM bank_escrow WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone()
        assert row is not None
        return dict(row)
    finally:
        connection.close()


def _native_provenance(
    database: Path,
    operation_id: str,
    *,
    include_source_reference: bool,
) -> dict[str, object]:
    connection = _open_immutable(database)
    try:
        receipt = connection.execute(
            "SELECT request_sha256, event_id, source_reference "
            "FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        assert receipt is not None
        transaction_ids = sorted(
            str(row[0])
            for row in connection.execute(
                "SELECT transaction_id FROM bank_operation_legs "
                "WHERE operation_id=? AND transaction_id IS NOT NULL",
                (operation_id,),
            ).fetchall()
        )
        result: dict[str, object] = {
            "kind": "native",
            "operation_id": operation_id,
            "request_sha256": receipt[0],
            "event_id": receipt[1],
            "transaction_ids": transaction_ids,
        }
        if include_source_reference:
            result["source_reference"] = receipt[2]
        return result
    finally:
        connection.close()


def _native_receipt_record(database: Path, operation_id: str) -> dict[str, object]:
    connection = _open_immutable(database)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT canonical_request_json, request_sha256, event_id, result_json "
            "FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        assert row is not None
        return dict(row)
    finally:
        connection.close()


def _legacy_provenance(
    database: Path,
    escrow_id: str,
    *,
    settlement: bool,
) -> dict[str, object]:
    connection = _open_immutable(database)
    try:
        escrow = connection.execute(
            "SELECT task_id FROM bank_escrow WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone()
        marker = connection.execute(
            "SELECT cutover_singleton, lock_fact_sha256, resolution_fact_sha256 "
            "FROM bank_legacy_escrow_observations WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone()
        assert escrow is not None and marker is not None
        transaction_type = "escrow_release" if settlement else "escrow_lock"
        reference = escrow_id if settlement else escrow[0]
        transaction_observations = [
            {
                "tx_id": row[0],
                "observed_kind": row[1],
                "observed_fact_sha256": row[2],
            }
            for row in connection.execute(
                "SELECT o.tx_id, o.observed_kind, o.observed_fact_sha256 "
                "FROM bank_legacy_transaction_observations o "
                "JOIN bank_transactions t ON t.tx_id=o.tx_id "
                "WHERE t.type=? AND t.reference=? ORDER BY o.tx_id",
                (transaction_type, reference),
            ).fetchall()
        ]
        assert transaction_observations
        result: dict[str, object] = {
            "kind": "legacy",
            "cutover_singleton": marker[0],
            "transaction_observations": transaction_observations,
        }
        result["resolution_fact_sha256" if settlement else "lock_fact_sha256"] = (
            marker[2] if settlement else marker[1]
        )
        return result
    finally:
        connection.close()


def _expected_observed_fact(
    database: Path,
    lock: LockedEscrow,
    *,
    source_snapshot_sha256: str,
    refund_origin: str,
    refund_operation_id: str | None,
) -> dict[str, object]:
    return {
        "observation_version": 1,
        "source_snapshot_sha256": source_snapshot_sha256,
        "escrow": _escrow_fact(database, lock.escrow_id),
        "lock_provenance": _native_provenance(
            database,
            lock.operation_id,
            include_source_reference=False,
        ),
        "refund_origin": refund_origin,
        "existing_refund_provenance": (
            None
            if refund_operation_id is None
            else _native_provenance(
                database,
                refund_operation_id,
                include_source_reference=True,
            )
        ),
    }


def _expected_legacy_refund_fact(
    database: Path,
    *,
    source_snapshot_sha256: str,
) -> dict[str, object]:
    return {
        "observation_version": 1,
        "source_snapshot_sha256": source_snapshot_sha256,
        "escrow": _escrow_fact(database, LEGACY_REFUNDED_ESCROW_ID),
        "lock_provenance": _legacy_provenance(
            database,
            LEGACY_REFUNDED_ESCROW_ID,
            settlement=False,
        ),
        "refund_origin": "existing_refund",
        "existing_refund_provenance": _legacy_provenance(
            database,
            LEGACY_REFUNDED_ESCROW_ID,
            settlement=True,
        ),
    }


def _assert_observation_id(value: object) -> str:
    assert isinstance(value, str) and value.startswith("obs-")
    parsed = UUID(value[4:])
    assert value == f"obs-{parsed}"
    return value


def _source_file_snapshot(source: V3Source) -> dict[str, bytes]:
    return {
        path.relative_to(source.inventory.source_root).as_posix(): path.read_bytes()
        for path in sorted(source.inventory.source_root.rglob("*"))
        if path.is_file() and path != source.database
    }


def _tree_file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _exercise_observation_reads(
    source: V3Source,
    backup_database: Path,
    history: RichCutoverHistory,
) -> None:
    source_snapshot_sha256 = _sha256(backup_database.read_bytes())
    _complete_snapshot(source.database)
    _complete_snapshot(backup_database)
    _legacy_task_ids(source.database)
    _legacy_task_ids(backup_database)
    _expected_observed_fact(
        backup_database,
        history.pending_orphans[0],
        source_snapshot_sha256=source_snapshot_sha256,
        refund_origin="new_refund",
        refund_operation_id=None,
    )
    _expected_observed_fact(
        backup_database,
        history.refunded_orphan,
        source_snapshot_sha256=source_snapshot_sha256,
        refund_origin="existing_refund",
        refund_operation_id=history.existing_refund_operation_id,
    )
    _native_receipt_record(backup_database, history.existing_refund_operation_id)


def _preservation_evidence(
    source: V3Source,
    *,
    backup_id: str,
    verification: dict[str, object],
) -> _PreservationEvidence:
    return _PreservationEvidence(
        database=_complete_snapshot(source.database),
        source_files=_source_file_snapshot(source),
        backup_verification=verification,
        backup_digest=old_migration._tree_digest(source.backup_root / backup_id),
    )


def _assert_preserved(
    before: _PreservationEvidence,
    source: V3Source,
    *,
    backup_id: str,
) -> None:
    assert _complete_snapshot(source.database) == before.database
    assert _source_file_snapshot(source) == before.source_files
    assert old_migration._tree_digest(source.backup_root / backup_id) == before.backup_digest
    assert verify_backup_without_mutating(source, backup_id=backup_id) == before.backup_verification


def _assert_backup_preserved(
    source: V3Source,
    *,
    backup_id: str,
    verification: dict[str, object],
    digest: str,
) -> None:
    assert old_migration._tree_digest(source.backup_root / backup_id) == digest
    assert verify_backup_without_mutating(source, backup_id=backup_id) == verification


def _assert_all_v3_data_preserved(
    before: dict[str, object],
    after: dict[str, object],
) -> None:
    before_objects = before["objects"]
    after_objects = after["objects"]
    before_rows = before["rows"]
    after_rows = after["rows"]
    assert isinstance(before_objects, list) and isinstance(after_objects, list)
    assert isinstance(before_rows, dict) and isinstance(after_rows, dict)
    assert all(item in after_objects for item in before_objects)
    assert {name: after_rows[name] for name in before_rows} == before_rows
    assert after["sequence"] == before["sequence"]


def test_v3_to_v4_migration_interface_is_explicitly_available() -> None:
    assert _migration_entry_point() is not None, _CAPABILITY_GAP
    assert getattr(SchemaState, "CURRENT_V4", None) is not None, (
        "SchemaState.CURRENT_V4 is required for exact source/target admission"
    )


def test_positive_cutover_fixture_is_real_receipt_backed_and_quiescent(tmp_path: Path) -> None:
    source = prepare_v3_source(tmp_path)
    writer = open_v3_writer(source)
    try:
        history = add_rich_cutover_history(writer)
    finally:
        writer.close()
    verification = publish_verified_backup(source, backup_id="fixture-control")

    connection = _open_immutable(source.database)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V3
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_task_contracts(connection).contracts_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        receipt_rows = connection.execute(
            "SELECT operation_id, operation_kind, escrow_id FROM bank_operation_receipts "
            "WHERE escrow_id IN (?, ?, ?, ?) ORDER BY escrow_id, operation_kind",
            (
                history.winner.escrow_id,
                history.pending_orphans[0].escrow_id,
                history.pending_orphans[1].escrow_id,
                history.refunded_orphan.escrow_id,
            ),
        ).fetchall()
        assert len(receipt_rows) == 5
        assert {row[1] for row in receipt_rows} == {"escrow_lock", "escrow_release"}
        assert connection.execute(
            "SELECT escrow_id FROM board_tasks WHERE task_id=?",
            (history.task_id,),
        ).fetchone() == (history.winner.escrow_id,)
        assert connection.execute(
            "SELECT status FROM bank_escrow WHERE escrow_id=?",
            (history.refunded_orphan.escrow_id,),
        ).fetchone() == ("released",)
    finally:
        connection.close()
    assert verification["artifact_verified"] is True


@pytest.mark.parametrize("caller_kid", [_CALLER_KID, _NONDEFAULT_CALLER_KID])
def test_verified_v3_cutover_preserves_all_legacy_data_and_attests_exact_sets(
    tmp_path: Path,
    caller_kid: str,
) -> None:
    source = prepare_v3_source(tmp_path, caller_kid=caller_kid)
    writer = open_v3_writer(source)
    try:
        history = add_rich_cutover_history(writer)
    finally:
        writer.close()
    backup_id = "before-mp05-success"
    backup_verification = publish_verified_backup(source, backup_id=backup_id)
    backup_digest = old_migration._tree_digest(source.backup_root / backup_id)
    backup_database = source.backup_root / backup_id / "payload/database/economy.db"
    source_before = _complete_snapshot(backup_database)
    legacy_task_ids = sorted(_legacy_task_ids(backup_database), key=lambda value: value.encode())
    source_snapshot_sha256 = _sha256(backup_database.read_bytes())
    source_files_before_reads = _tree_file_snapshot(source.inventory.source_root)
    backup_files_before_reads = _tree_file_snapshot(source.backup_root / backup_id)
    _exercise_observation_reads(source, backup_database, history)
    assert _tree_file_snapshot(source.inventory.source_root) == source_files_before_reads
    assert _tree_file_snapshot(source.backup_root / backup_id) == backup_files_before_reads

    result = _require_migration_entry_point()(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5000,
    )

    assert result.applied is True
    assert result.schema_version == 4
    assert result.observation == "committed_v4_verified"
    _assert_all_v3_data_preserved(
        source_before,
        _complete_snapshot(source.database),
    )
    _assert_backup_preserved(
        source,
        backup_id=backup_id,
        verification=backup_verification,
        digest=backup_digest,
    )
    connection = _open_immutable(source.database)
    connection.row_factory = sqlite3.Row
    try:
        assert classify_schema(connection).state is SchemaState("current_v4")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        metadata = connection.execute("SELECT * FROM schema_metadata").fetchone()
        assert metadata is not None
        assert tuple(metadata) == (
            1,
            4,
            3,
            backup_id,
            result.manifest_sha256,
        )
        publication_count = connection.execute(
            "SELECT COUNT(*) FROM board_task_publications"
        ).fetchone()
        assert publication_count is not None and publication_count[0] == 0
        cutover = connection.execute("SELECT * FROM board_publication_cutover").fetchone()
        assert cutover is not None
        observations = connection.execute(
            "SELECT observation_id, observation_contract_version, escrow_id, task_id, "
            "poster_id, amount, observed_fact_json, observed_fact_sha256, caller_kid, "
            "state, refund_operation_id, refund_request_json, refund_request_sha256, "
            "refund_receipt_event_id, refunded_at_us, result_json, error_json "
            "FROM board_legacy_posting_observations ORDER BY observation_id"
        ).fetchall()
        observation_attestation = [
            [row["observation_id"], row["escrow_id"], row["observed_fact_sha256"]]
            for row in observations
        ]
        assert dict(cutover) == {
            "singleton": 1,
            "cutover_version": 1,
            "cutover_kind": "migrated",
            "source_schema_version": 3,
            "source_snapshot_sha256": source_snapshot_sha256,
            "migration_plan_sha256": result.plan_sha256,
            "backup_id": backup_id,
            "backup_manifest_sha256": result.manifest_sha256,
            "caller_kid": caller_kid,
            "legacy_task_ids_sha256": _sha256(_canonical_json(legacy_task_ids).encode()),
            "legacy_task_count": len(legacy_task_ids),
            "legacy_observations_sha256": _sha256(
                _canonical_json(observation_attestation).encode()
            ),
            "legacy_observation_count": 3,
        }
        _assert_migrated_observations(
            observations,
            backup_database=backup_database,
            history=history,
            source_snapshot_sha256=source_snapshot_sha256,
            caller_kid=caller_kid,
        )
    finally:
        connection.close()


def _assert_migrated_observations(
    observations: list[sqlite3.Row],
    *,
    backup_database: Path,
    history: RichCutoverHistory,
    source_snapshot_sha256: str,
    caller_kid: str,
) -> None:
    assert len(observations) == 3
    by_escrow = {str(row["escrow_id"]): row for row in observations}
    expected_escrows = {
        history.pending_orphans[0].escrow_id,
        history.pending_orphans[1].escrow_id,
        history.refunded_orphan.escrow_id,
    }
    assert set(by_escrow) == expected_escrows
    for lock in history.pending_orphans:
        row = by_escrow[lock.escrow_id]
        _assert_common_observation(
            row,
            lock=lock,
            expected_fact=_expected_observed_fact(
                backup_database,
                lock,
                source_snapshot_sha256=source_snapshot_sha256,
                refund_origin="new_refund",
                refund_operation_id=None,
            ),
        )
        assert row["caller_kid"] == caller_kid
        operation_id = operation_id_for("task-posting-refund", (lock.escrow_id,))
        source_reference = f"task:{lock.task_id}:posting_refund:{lock.escrow_id}"
        request = canonical_request(
            "escrow_release",
            {
                "escrow_id": lock.escrow_id,
                "recipient_account_id": lock.payer_id,
                "source_reference": source_reference,
            },
        )
        assert row["state"] == "refund_pending"
        assert row["refund_operation_id"] == operation_id
        assert row["refund_request_json"] == request.canonical_json
        assert row["refund_request_sha256"] == request.request_sha256
        assert row["refund_receipt_event_id"] is None
        assert row["refunded_at_us"] is None
        assert row["result_json"] is None
        assert row["error_json"] is None

    refunded = history.refunded_orphan
    row = by_escrow[refunded.escrow_id]
    _assert_common_observation(
        row,
        lock=refunded,
        expected_fact=_expected_observed_fact(
            backup_database,
            refunded,
            source_snapshot_sha256=source_snapshot_sha256,
            refund_origin="existing_refund",
            refund_operation_id=history.existing_refund_operation_id,
        ),
    )
    assert row["caller_kid"] == caller_kid
    refund_receipt = _native_receipt_record(
        backup_database,
        history.existing_refund_operation_id,
    )
    existing_request = canonical_request(
        "escrow_release",
        {
            "escrow_id": refunded.escrow_id,
            "recipient_account_id": refunded.payer_id,
            "source_reference": history.existing_refund_source_reference,
        },
    )
    assert row["state"] == "refunded"
    assert row["refund_operation_id"] == history.existing_refund_operation_id
    assert row["refund_request_json"] == refund_receipt["canonical_request_json"]
    assert row["refund_request_json"] == existing_request.canonical_json
    assert row["refund_request_sha256"] == refund_receipt["request_sha256"]
    assert row["refund_receipt_event_id"] == refund_receipt["event_id"]
    resolved_at = _escrow_fact(backup_database, refunded.escrow_id)["resolved_at"]
    assert isinstance(resolved_at, str)
    assert row["refunded_at_us"] == parse_utc_text_to_epoch_us(resolved_at)
    assert row["result_json"] == refund_receipt["result_json"]
    assert row["error_json"] is None


def _assert_common_observation(
    row: sqlite3.Row,
    *,
    lock: LockedEscrow,
    expected_fact: dict[str, object],
) -> None:
    _assert_observation_id(row["observation_id"])
    assert row["observation_contract_version"] == 1
    assert row["escrow_id"] == lock.escrow_id
    assert row["task_id"] == lock.task_id
    assert row["poster_id"] == lock.payer_id
    assert row["amount"] == lock.amount
    fact_json = _canonical_json(expected_fact)
    assert row["observed_fact_json"] == fact_json
    assert row["observed_fact_sha256"] == _sha256(fact_json.encode())
    assert isinstance(row["caller_kid"], str) and row["caller_kid"]


def test_legacy_marker_fixture_is_supported_receipt_cutover_history(tmp_path: Path) -> None:
    source = prepare_v3_source_with_legacy_refund(tmp_path)
    verification = publish_verified_backup(source, backup_id="legacy-fixture-control")
    connection = _open_immutable(source.database)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V3
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_task_contracts(connection).contracts_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT observed_status, lock_fact_sha256, resolution_fact_sha256 "
            "FROM bank_legacy_escrow_observations WHERE escrow_id=?",
            (LEGACY_REFUNDED_ESCROW_ID,),
        ).fetchone() == (
            "released",
            _legacy_provenance(
                source.database,
                LEGACY_REFUNDED_ESCROW_ID,
                settlement=False,
            )["lock_fact_sha256"],
            _legacy_provenance(
                source.database,
                LEGACY_REFUNDED_ESCROW_ID,
                settlement=True,
            )["resolution_fact_sha256"],
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE escrow_id=?",
            (LEGACY_REFUNDED_ESCROW_ID,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM board_tasks WHERE escrow_id=?",
            (LEGACY_REFUNDED_ESCROW_ID,),
        ).fetchone() == (0,)
    finally:
        connection.close()
    assert verification["artifact_verified"] is True


def test_verified_legacy_marker_refund_remains_an_honest_non_native_tombstone(
    tmp_path: Path,
) -> None:
    source = prepare_v3_source_with_legacy_refund(tmp_path)
    backup_id = "before-mp05-legacy-refund"
    backup_verification = publish_verified_backup(source, backup_id=backup_id)
    backup_digest = old_migration._tree_digest(source.backup_root / backup_id)
    backup_database = source.backup_root / backup_id / "payload/database/economy.db"
    source_before = _complete_snapshot(backup_database)
    source_snapshot_sha256 = _sha256(backup_database.read_bytes())
    expected_fact = _expected_legacy_refund_fact(
        backup_database,
        source_snapshot_sha256=source_snapshot_sha256,
    )

    result = _require_migration_entry_point()(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5000,
    )

    assert result.observation == "committed_v4_verified"
    _assert_all_v3_data_preserved(
        source_before,
        _complete_snapshot(source.database),
    )
    _assert_backup_preserved(
        source,
        backup_id=backup_id,
        verification=backup_verification,
        digest=backup_digest,
    )
    connection = _open_immutable(source.database)
    connection.row_factory = sqlite3.Row
    try:
        cutover = connection.execute("SELECT * FROM board_publication_cutover").fetchone()
        row = connection.execute(
            "SELECT observation_id, observation_contract_version, escrow_id, task_id, "
            "poster_id, amount, observed_fact_json, observed_fact_sha256, caller_kid, "
            "state, refund_operation_id, refund_request_json, refund_request_sha256, "
            "refund_receipt_event_id, refunded_at_us, result_json, error_json "
            "FROM board_legacy_posting_observations WHERE escrow_id=?",
            (LEGACY_REFUNDED_ESCROW_ID,),
        ).fetchone()
        assert cutover is not None and row is not None
        _assert_observation_id(row["observation_id"])
        assert row["observation_contract_version"] == 1
        escrow = expected_fact["escrow"]
        assert isinstance(escrow, dict)
        assert row["escrow_id"] == LEGACY_REFUNDED_ESCROW_ID
        assert row["task_id"] == escrow["task_id"] == "t-bank-only"
        assert row["poster_id"] == escrow["payer_account_id"] == "a-external"
        assert row["amount"] == escrow["amount"] == 50
        fact_json = _canonical_json(expected_fact)
        assert row["observed_fact_json"] == fact_json
        assert row["observed_fact_sha256"] == _sha256(fact_json.encode())
        assert row["caller_kid"] == source.caller_kid
        legacy_task_ids = sorted(
            _legacy_task_ids(backup_database),
            key=lambda value: value.encode(),
        )
        assert dict(cutover) == {
            "singleton": 1,
            "cutover_version": 1,
            "cutover_kind": "migrated",
            "source_schema_version": 3,
            "source_snapshot_sha256": source_snapshot_sha256,
            "migration_plan_sha256": result.plan_sha256,
            "backup_id": backup_id,
            "backup_manifest_sha256": result.manifest_sha256,
            "caller_kid": source.caller_kid,
            "legacy_task_ids_sha256": _sha256(_canonical_json(legacy_task_ids).encode()),
            "legacy_task_count": len(legacy_task_ids),
            "legacy_observations_sha256": _sha256(
                _canonical_json(
                    [
                        [
                            row["observation_id"],
                            row["escrow_id"],
                            row["observed_fact_sha256"],
                        ]
                    ]
                ).encode()
            ),
            "legacy_observation_count": 1,
        }
        assert row["state"] == "refunded"
        assert row["refund_operation_id"] is None
        assert row["refund_request_json"] is None
        assert row["refund_request_sha256"] is None
        assert row["refund_receipt_event_id"] is None
        assert isinstance(escrow["resolved_at"], str)
        assert row["refunded_at_us"] == parse_utc_text_to_epoch_us(escrow["resolved_at"])
        assert row["result_json"] == _canonical_json(
            {
                "refund_origin": "existing_refund",
                "settlement_provenance_kind": "legacy_observation",
                "escrow_status_at_cutover": "released",
            }
        )
        assert row["error_json"] is None
    finally:
        connection.close()


@pytest.mark.parametrize("defect", ["caller_mismatch", "missing_board_write"])
def test_backup_bound_posting_authority_defect_rejects_without_mutation(
    tmp_path: Path,
    defect: str,
) -> None:
    source = invalidate_posting_authority(prepare_v3_source(tmp_path), defect=defect)
    backup_id = f"reject-authority-{defect}"
    verification = publish_verified_backup(source, backup_id=backup_id)
    before = _preservation_evidence(
        source,
        backup_id=backup_id,
        verification=verification,
    )

    with pytest.raises(migration.MigrationError) as raised:
        _require_migration_entry_point()(
            source.inventory,
            backup_root=source.backup_root,
            backup_id=backup_id,
            work_root=source.work_root,
            busy_timeout_ms=5000,
        )

    assert raised.value.reason == "posting_authority_invalid"
    _assert_preserved(before, source, backup_id=backup_id)


@pytest.mark.parametrize("case", _REJECTION_CASES, ids=lambda case: case.name)
def test_unreliable_or_conflicting_orphan_history_rejects_without_mutation(
    tmp_path: Path,
    case: _RejectionCase,
) -> None:
    source = prepare_v3_source(tmp_path)
    affected = _add_rejected_history(source, case.name)
    backup_id = f"reject-{case.name}"
    verification = publish_verified_backup(source, backup_id=backup_id)
    before = _preservation_evidence(
        source,
        backup_id=backup_id,
        verification=verification,
    )

    with pytest.raises(migration.MigrationError) as raised:
        _require_migration_entry_point()(
            source.inventory,
            backup_root=source.backup_root,
            backup_id=backup_id,
            work_root=source.work_root,
            busy_timeout_ms=5000,
        )

    assert raised.value.reason == case.expected_reason
    assert raised.value.safe_details["escrow_id"] == affected.escrow_id
    assert raised.value.safe_details["task_id"] == affected.task_id
    _assert_preserved(before, source, backup_id=backup_id)


def _add_rejected_history(source: V3Source, case_name: str) -> LockedEscrow:
    writer = open_v3_writer(source)
    try:
        payer = create_funded_payer(writer, name=f"Rejected {case_name}")
        task_id = f"t-{uuid4()}"
        lock = lock_escrow(writer, payer_id=payer, task_id=task_id, amount=80)
        if case_name == "split":
            worker = create_funded_payer(writer, name="Split recipient", balance=1)
            split_escrow(writer, lock, worker_id=worker, worker_pct=50)
        elif case_name == "foreign_recipient":
            recipient = create_funded_payer(writer, name="Foreign refund recipient", balance=1)
            release_escrow(
                writer,
                lock,
                recipient_id=recipient,
                operation_id=f"op-{uuid4()}",
                source_reference=f"task:{task_id}:posting_compensation",
            )
        elif case_name == "conflicting_task_reference":
            winner = lock_escrow(
                writer,
                payer_id=create_funded_payer(writer, name="Published task owner"),
                task_id=task_id,
                amount=80,
            )
            publish_legacy_task(writer, winner)
        elif case_name != "unproven_lock":
            raise AssertionError(f"Unknown rejection case: {case_name}")
    finally:
        writer.close()

    if case_name == "unproven_lock":
        # Deliberate test-only corruption: remove the authoritative receipt and
        # legs while retaining its escrow/transaction/event economic effects.
        connection = sqlite3.connect(source.database)
        try:
            connection.execute(
                "DELETE FROM bank_operation_legs WHERE operation_id=?",
                (lock.operation_id,),
            )
            connection.execute(
                "DELETE FROM bank_operation_receipts WHERE operation_id=?",
                (lock.operation_id,),
            )
            connection.commit()
        finally:
            connection.close()
    elif case_name == "conflicting_task_reference":
        # Deliberate test-only corruption: the task now cites another payer's
        # escrow even though both genuine lock receipts remain unchanged.
        connection = sqlite3.connect(source.database)
        try:
            connection.execute(
                "UPDATE board_tasks SET escrow_id=? WHERE task_id=?",
                (lock.escrow_id, task_id),
            )
            connection.commit()
        finally:
            connection.close()
    return lock
