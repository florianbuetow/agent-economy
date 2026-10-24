"""T-124 verified V4-to-V5 migration and immutable cutover evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

import pytest

from db_gateway_service import migration, recovery, task_time
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import classify_schema
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.fakes.gateway_mp08_effects import prepare_native_financial_v4
from tests.fakes.gateway_mp08_legacy_history import prepare_legacy_worker_settlement_v4
from tests.fakes.gateway_mp08_migration_helpers import (
    database_snapshot,
    storage_snapshot,
    tree_snapshot,
)
from tests.fakes.mp05_publication_migration_history import (
    prepare_v3_source,
    publish_verified_backup,
)

if TYPE_CHECKING:
    from service_commons.recovery_contract import RecoveryInventoryV1

pytestmark = pytest.mark.unit


class _MigrateV4ToV5(Protocol):
    def __call__(
        self,
        inventory: RecoveryInventoryV1,
        *,
        backup_root: Path,
        backup_id: str,
        work_root: Path,
        busy_timeout_ms: int,
        current_task_board_caller_kid: str,
    ) -> migration.MigrationResult: ...


def _entry_point() -> _MigrateV4ToV5:
    value = getattr(migration, "migrate_v4_to_v5", None)
    assert callable(value), "Missing explicit backup-bound V4-to-V5 migration"
    return cast("_MigrateV4ToV5", value)


def _prepared_v4(tmp_path: Path) -> tuple[object, Path, Path, Path]:
    source = prepare_v3_source(tmp_path)
    publish_verified_backup(source, backup_id="before-v4")
    result = migration.migrate_v3_to_v4(
        source.inventory,
        backup_root=source.backup_root,
        backup_id="before-v4",
        work_root=source.work_root,
        busy_timeout_ms=5000,
    )
    assert result.observation == "committed_v4_verified"
    return source.inventory, source.backup_root, source.work_root, source.database


def _sourceful_v4(
    tmp_path: Path,
    *,
    kind: Literal["pending", "succeeded"],
) -> tuple[object, Path, Path, Path]:
    source = prepare_legacy_worker_settlement_v4(
        tmp_path,
        kind=kind,
        caller_kid="gateway-task-board",
    )
    return source.inventory, source.backup_root, source.work_root, source.database


def test_v4_to_v5_records_verified_backup_and_exact_migration_plan(tmp_path: Path) -> None:
    inventory, backup_root, work_root, database = _prepared_v4(tmp_path)
    recovery.create_backup(
        inventory, backup_root=backup_root, backup_id="before-v5", work_root=work_root
    )
    before = database_snapshot(database)
    storage_before = storage_snapshot(inventory.source_root)
    backup_before = recovery.verify_backup(
        backup_root=backup_root,
        backup_id="before-v5",
        work_root=work_root,
    ).model_dump(mode="json")
    backup_tree_before = tree_snapshot(backup_root)
    result = _entry_point()(
        inventory,
        backup_root=backup_root,
        backup_id="before-v5",
        work_root=work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    assert result.applied and result.schema_version == 5
    assert result.observation == "committed_v5_verified"
    after = database_snapshot(database)
    before_rows = before["rows"]
    after_rows = after["rows"]
    assert isinstance(before_rows, dict) and isinstance(after_rows, dict)
    preserved_before = {
        name: rows for name, rows in before_rows.items() if name != "schema_metadata"
    }
    assert {name: after_rows[name] for name in preserved_before} == preserved_before
    assert after["sequence"] == before["sequence"]
    before_objects = before["objects"]
    after_objects = after["objects"]
    assert isinstance(before_objects, list) and isinstance(after_objects, list)
    preserved_objects = [item for item in before_objects if item[1] != "schema_metadata"]
    assert all(item in after_objects for item in preserved_objects)
    assert (
        recovery.verify_backup(
            backup_root=backup_root,
            backup_id="before-v5",
            work_root=work_root,
        ).model_dump(mode="json")
        == backup_before
    )
    assert tree_snapshot(backup_root) == backup_tree_before
    assert storage_snapshot(inventory.source_root) == storage_before
    connection = sqlite3.connect(database)
    try:
        assert classify_schema(connection).state.value == "current_v5"
        cutover = connection.execute(
            "SELECT cutover_kind, source_schema_version, backup_id, "
            "backup_manifest_sha256, caller_kid, migration_plan_sha256 "
            "FROM board_task_effect_delivery_cutover"
        ).fetchone()
        assert cutover is not None
        assert cutover[:5] == (
            "migrated",
            4,
            "before-v5",
            result.manifest_sha256,
            "gateway-task-board",
        )
        metadata_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
        ).fetchone()[0]
        migration_sql = (
            Path(migration.__file__).parent / "schema_resources" / "task_effect_recovery_v5.sql"
        ).read_text()
        expected = hashlib.sha256(f"{migration_sql}\n{metadata_sql}".encode()).hexdigest()
        assert cutover[5] == expected
    finally:
        connection.close()


@pytest.mark.parametrize("kind", ["pending", "succeeded"])
def test_v4_to_v5_uses_one_migration_time_for_delivery_bookkeeping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["pending", "succeeded"],
) -> None:
    inventory, backup_root, work_root, database = _sourceful_v4(tmp_path, kind=kind)
    migration_time = parse_utc_text_to_epoch_us("2100-01-01T00:00:00Z")
    monkeypatch.setattr(task_time, "utc_now_us", lambda: migration_time)

    recovery.create_backup(
        inventory, backup_root=backup_root, backup_id="before-v5-time", work_root=work_root
    )
    _entry_point()(
        inventory,
        backup_root=backup_root,
        backup_id="before-v5-time",
        work_root=work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT delivery_state, enqueued_at_us, next_attempt_at_us, completed_at_us "
            "FROM board_task_effect_deliveries"
        ).fetchall()
        for state, enqueued, next_attempt, completed in rows:
            assert enqueued == migration_time
            if state == "completed":
                assert completed == enqueued and next_attempt is None
            elif state == "pending":
                assert next_attempt == enqueued and completed is None
        initial = connection.execute(
            "SELECT d.source_kind,d.legacy_observation_id,d.owner_caller_kid,d.effect_kind,"
            "d.delivery_state,d.enqueued_at_us,d.total_attempt_count,d.cycle_attempt_count,"
            "d.lease_generation,d.next_attempt_at_us,d.cutover_singleton,o.observation_kind "
            "FROM board_task_effect_deliveries d JOIN board_legacy_effect_observations o "
            "ON o.observation_id=d.legacy_observation_id"
        ).fetchone()
        assert initial is not None
        assert initial[0] == "legacy_settlement_observation"
        assert initial[1] is not None
        assert initial[2:5] == ("gateway-task-board", "bank_release", "pending")
        assert initial[5] == migration_time
        assert initial[6:9] == (0, 0, 0)
        assert initial[9:11] == (migration_time, 1)
        assert initial[11] == f"worker_settlement_{kind}"
        if kind == "succeeded":
            legacy = connection.execute(
                "SELECT d.enqueued_at_us,r.committed_at FROM board_task_effect_deliveries d "
                "JOIN board_legacy_effect_observations o "
                "ON o.observation_id=d.legacy_observation_id "
                "JOIN bank_operation_receipts r ON r.operation_id=d.effect_operation_id "
                "WHERE o.observation_kind='worker_settlement_succeeded'"
            ).fetchone()
            assert legacy is not None
            assert parse_utc_text_to_epoch_us(str(legacy[1])) < int(legacy[0])
    finally:
        connection.close()


def test_v4_to_v5_membership_hashes_cover_only_immutable_source_identity(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root, database = _sourceful_v4(tmp_path, kind="pending")
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="before-v5-members",
        work_root=work_root,
    )
    _entry_point()(
        inventory,
        backup_root=backup_root,
        backup_id="before-v5-members",
        work_root=work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    connection = sqlite3.connect(database)
    try:
        native = [
            list(row)
            for row in connection.execute(
                "SELECT operation_id, task_id, caller_kid, effect_kind, "
                "effect_operation_id, request_sha256 FROM board_task_operations "
                "WHERE effect_kind IN ('bank_release','bank_split') ORDER BY operation_id"
            )
        ]
        publication_owner = connection.execute(
            "SELECT caller_kid FROM board_publication_cutover WHERE singleton=1"
        ).fetchone()[0]
        legacy = [
            [*row, publication_owner]
            for row in connection.execute(
                "SELECT observation_id, task_id, observation_kind, external_reference, "
                "observed_fact_sha256 FROM board_legacy_effect_observations "
                "WHERE unresolved=1 AND observation_kind IN "
                "('worker_settlement_pending','worker_settlement_succeeded') "
                "ORDER BY observation_id"
            )
        ]
        combined = [
            list(row)
            for row in connection.execute(
                "SELECT source_kind, "
                "CASE source_kind WHEN 'native_task_operation' THEN native_operation_id "
                "ELSE legacy_observation_id END, effect_operation_id, task_id, "
                "owner_caller_kid, effect_kind FROM board_task_effect_deliveries "
                "WHERE cutover_singleton=1 ORDER BY "
                "CASE source_kind WHEN 'native_task_operation' THEN native_operation_id "
                "ELSE legacy_observation_id END"
            )
        ]

        def digest(value: list[list[object]]) -> str:
            encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
            return hashlib.sha256(encoded).hexdigest()

        cutover = connection.execute(
            "SELECT native_member_count, native_members_sha256, legacy_member_count, "
            "legacy_members_sha256, combined_member_count, combined_members_sha256 "
            "FROM board_task_effect_delivery_cutover"
        ).fetchone()
        assert cutover == (
            len(native),
            digest(native),
            len(legacy),
            digest(legacy),
            len(combined),
            digest(combined),
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("source_state", "delivery_state"),
    [
        ("claimed", "pending"),
        ("completed", "completed"),
        ("blocked", "integrity_blocked"),
    ],
)
def test_v4_to_v5_classifies_real_native_financial_histories_at_one_migration_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_state: Literal["claimed", "completed", "blocked"],
    delivery_state: str,
) -> None:
    native = prepare_native_financial_v4(tmp_path, operation_state=source_state)
    control = sqlite3.connect(f"file:{native.source.database}?mode=ro", uri=True)
    try:
        assert audit_money(control).financially_clean
        assert audit_receipt_ownership(control).ownership_clean
        assert audit_task_contracts(control).contracts_clean
        assert control.execute("PRAGMA foreign_key_check").fetchall() == []
        assert control.execute(
            "SELECT operation_state FROM board_task_operations WHERE operation_id=?",
            (native.operation_id,),
        ).fetchone() == (source_state,)
    finally:
        control.close()
    migration_time = parse_utc_text_to_epoch_us("2100-01-01T00:00:00Z")
    monkeypatch.setattr(task_time, "utc_now_us", lambda: migration_time)
    recovery.create_backup(
        native.source.inventory,
        backup_root=native.source.backup_root,
        backup_id="before-v5-native",
        work_root=native.source.work_root,
    )
    _entry_point()(
        native.source.inventory,
        backup_root=native.source.backup_root,
        backup_id="before-v5-native",
        work_root=native.source.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    connection = sqlite3.connect(native.source.database)
    try:
        row = connection.execute(
            "SELECT source_kind,native_operation_id,owner_caller_kid,effect_kind,"
            "delivery_state,enqueued_at_us,total_attempt_count,cycle_attempt_count,"
            "lease_generation,next_attempt_at_us,last_failure_code,last_failure_at_us,"
            "completed_at_us,completion_event_id,cutover_singleton "
            "FROM board_task_effect_deliveries WHERE effect_operation_id=?",
            (native.operation_id,),
        ).fetchone()
        assert row is not None
        assert row[0:4] == (
            "native_task_operation",
            native.operation_id,
            "gateway-task-board",
            "bank_release",
        )
        assert row[4] == delivery_state
        assert row[5] == migration_time
        assert row[6:9] == (0, 0, 0)
        assert row[14] == 1
        if delivery_state == "pending":
            assert row[9:14] == (migration_time, None, None, None, None)
        elif delivery_state == "completed":
            receipt_event = connection.execute(
                "SELECT event_id FROM bank_operation_receipts WHERE operation_id=?",
                (native.operation_id,),
            ).fetchone()
            assert receipt_event is not None
            assert row[9:14] == (None, None, None, migration_time, receipt_event[0])
        else:
            assert row[9:14] == (
                None,
                "effect_evidence_conflict",
                migration_time,
                None,
                None,
            )
    finally:
        connection.close()
