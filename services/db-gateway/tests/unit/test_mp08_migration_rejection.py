"""T-124 V5 migration rejects ambiguous financial ownership without mutation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

import pytest

from db_gateway_service import migration, recovery
from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.task_contract_audit import audit_task_contracts
from tests.fakes.gateway_mp08_effects import prepare_native_financial_v4
from tests.fakes.gateway_mp08_legacy_history import (
    prepare_legacy_court_claim_v4,
    prepare_legacy_worker_settlement_v4,
)
from tests.fakes.gateway_mp08_migration_helpers import (
    database_snapshot,
    storage_snapshot,
    tree_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

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


def _case(tmp_path: Path) -> tuple[Any, Path, Path, Path, str]:
    source = prepare_legacy_worker_settlement_v4(
        tmp_path,
        kind="pending",
        caller_kid="gateway-task-board",
    )
    backup_id = "before-v5-reject"
    recovery.create_backup(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
    )
    return source.inventory, source.backup_root, source.work_root, source.database, backup_id


def _publish_backup(
    inventory: Any,
    backup_root: Path,
    work_root: Path,
    *,
    backup_id: str,
) -> None:
    publication = recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    assert publication.state is recovery.PublicationState.COMPLETE


def _assert_valid_v4_control(database: Path) -> None:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_task_contracts(connection).contracts_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT observation_kind, unresolved FROM board_legacy_effect_observations"
        ).fetchall() == [("worker_settlement_pending", 1)]
    finally:
        connection.close()


def _inject_native_overlap(database: Path, *, operation_id: str | None = None) -> str:
    connection = sqlite3.connect(database)
    try:
        task = connection.execute(
            "SELECT task_id,poster_id,worker_id,escrow_id,reward "
            "FROM board_tasks WHERE status='approved' AND escrow_pending=1"
        ).fetchone()
        assert task is not None
        task_id, poster_id, worker_id, escrow_id, reward = task
        resolved_operation_id = operation_id or f"op-{uuid4()}"
        source_reference = f"task:{task_id}:approved"
        financial = canonical_request(
            "escrow_release",
            {
                "escrow_id": escrow_id,
                "recipient_account_id": worker_id,
                "source_reference": source_reference,
            },
        )
        request = {
            "protocol_version": 1,
            "operation_id": resolved_operation_id,
            "task_id": task_id,
            "command": "approve_task",
            "expected_status": "submitted",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "arguments": {
                "poster_id": poster_id,
                "worker_id": worker_id,
                "escrow_id": escrow_id,
                "reward": reward,
                "source_reference": source_reference,
            },
        }
        request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
        intent = json.dumps(
            {
                "financial_request": json.loads(financial.canonical_json),
                "financial_request_sha256": financial.request_sha256,
                "expected_escrow": {
                    "escrow_id": escrow_id,
                    "task_id": task_id,
                    "payer_account_id": poster_id,
                    "amount": reward,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO board_task_operations (operation_id,operation_contract_version,"
            "task_id,command,canonical_request_json,request_sha256,caller_kid,actor_kind,"
            "actor_id,source_status,source_lifecycle_version,target_status,"
            "target_lifecycle_version,operation_state,effect_kind,effect_state,"
            "effect_operation_id,effect_intent_json,claimed_at_us) "
            "VALUES (?,1,?,'approve_task',?,?,'gateway-task-board','agent',?,"
            "'submitted',0,'approved',1,'claimed','bank_release','pending',?,?,?)",
            (
                resolved_operation_id,
                task_id,
                request_json,
                hashlib.sha256(request_json.encode()).hexdigest(),
                poster_id,
                resolved_operation_id,
                intent,
                4070908802000000,
            ),
        )
        connection.commit()
        return resolved_operation_id
    finally:
        connection.close()


def test_v5_migration_rejects_foreign_current_board_kid_without_mutation(tmp_path: Path) -> None:
    inventory, backup_root, work_root, database, backup_id = _case(tmp_path)
    _assert_valid_v4_control(database)
    before = database_snapshot(database)
    storage = storage_snapshot(inventory.source_root)
    backup_before = tree_snapshot(backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="foreign-board-key",
        )
    assert raised.value.reason in {
        "current_task_board_caller_untrusted",
        "effect_delivery_owner_mismatch",
    }
    assert database_snapshot(database) == before
    assert storage_snapshot(inventory.source_root) == storage
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_native_and_legacy_sources_for_same_task(tmp_path: Path) -> None:
    inventory, backup_root, work_root, database, _backup_id = _case(tmp_path)
    _assert_valid_v4_control(database)
    _inject_native_overlap(database)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        audit = audit_task_contracts(connection)
        assert audit.contracts_clean
        assert audit.findings == ()
        assert connection.execute(
            "SELECT COUNT(*) FROM board_task_operations WHERE task_id IN "
            "(SELECT task_id FROM board_legacy_effect_observations WHERE unresolved=1)"
        ).fetchone() == (1,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="duplicate-native",
        work_root=work_root,
    )
    before = database_snapshot(database)
    backup_before = tree_snapshot(backup_root)
    migrate = _entry_point()
    with pytest.raises(migration.MigrationError) as raised:
        migrate(
            inventory,
            backup_root=backup_root,
            backup_id="duplicate-native",
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "effect_delivery_source_conflict"
    assert database_snapshot(database) == before
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_duplicate_effect_identity_across_source_kinds(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root, database, _backup_id = _case(tmp_path)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        effect_operation_id = str(
            connection.execute(
                "SELECT external_reference FROM board_legacy_effect_observations"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    _inject_native_overlap(database, operation_id=effect_operation_id)
    _publish_backup(inventory, backup_root, work_root, backup_id="duplicate-effect-id")
    before = database_snapshot(database)
    backup_before = tree_snapshot(backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            inventory,
            backup_root=backup_root,
            backup_id="duplicate-effect-id",
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "effect_delivery_identity_conflict"
    assert database_snapshot(database) == before
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_multiple_native_financial_sources_for_one_task(
    tmp_path: Path,
) -> None:
    native = prepare_native_financial_v4(tmp_path, operation_state="completed")
    database = native.source.database
    connection = sqlite3.connect(database)
    try:
        source = connection.execute(
            "SELECT * FROM board_task_operations WHERE operation_id=?",
            (native.operation_id,),
        ).fetchone()
        assert source is not None
        columns = [row[1] for row in connection.execute("PRAGMA table_info(board_task_operations)")]
        duplicate = list(source)
        duplicate_id = f"op-{uuid4()}"
        duplicate[columns.index("operation_id")] = duplicate_id
        duplicate[columns.index("operation_state")] = "blocked"
        duplicate[columns.index("effect_state")] = "conflict"
        duplicate[columns.index("effect_operation_id")] = duplicate_id
        duplicate[columns.index("effect_result_reference")] = None
        duplicate[columns.index("completed_at_us")] = None
        duplicate[columns.index("event_id")] = None
        duplicate[columns.index("result_json")] = None
        duplicate[columns.index("error_json")] = json.dumps(
            {
                "error": "effect_evidence_conflict",
                "message": "Persisted effect evidence contradicts the claimed task operation",
                "details": {"operation_id": duplicate_id},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        placeholders = ",".join("?" for _ in duplicate)
        connection.execute(
            f"INSERT INTO board_task_operations VALUES ({placeholders})",  # nosec B608
            duplicate,
        )
        connection.commit()
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    _publish_backup(
        native.source.inventory,
        native.source.backup_root,
        native.source.work_root,
        backup_id="duplicate-native",
    )
    before = database_snapshot(database)
    backup_before = tree_snapshot(native.source.backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            native.source.inventory,
            backup_root=native.source.backup_root,
            backup_id="duplicate-native",
            work_root=native.source.work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "effect_delivery_source_conflict"
    assert database_snapshot(database) == before
    assert tree_snapshot(native.source.backup_root) == backup_before


def test_v5_migration_preserves_court_observation_without_financial_delivery(
    tmp_path: Path,
) -> None:
    source = prepare_legacy_court_claim_v4(
        tmp_path,
        caller_kid="gateway-task-board",
    )
    assert source.observation_id is not None
    recovery.create_backup(
        source.inventory,
        backup_root=source.backup_root,
        backup_id="before-v5-court",
        work_root=source.work_root,
    )
    result = _entry_point()(
        source.inventory,
        backup_root=source.backup_root,
        backup_id="before-v5-court",
        work_root=source.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    assert result.schema_version == 5
    connection = sqlite3.connect(f"file:{source.database}?mode=ro", uri=True)
    try:
        assert connection.execute(
            "SELECT observation_kind,external_reference,unresolved "
            "FROM board_legacy_effect_observations WHERE observation_id=?",
            (source.observation_id,),
        ).fetchone() == (
            "court_claim_succeeded",
            source.external_reference,
            1,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM board_task_effect_deliveries WHERE task_id=?",
            (source.task_id,),
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_v5_migration_rejects_noncanonical_legacy_effect_identity_without_mutation(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root, database, _backup_id = _case(tmp_path)
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT observation_id,observed_fact_json FROM board_legacy_effect_observations"
        ).fetchone()
        assert row is not None
        fact = json.loads(str(row[1]))
        assert isinstance(fact, dict)
        fact["external_reference"] = "not-a-canonical-operation"
        fact_json = json.dumps(fact, sort_keys=True, separators=(",", ":"))
        connection.execute(
            "UPDATE board_legacy_effect_observations SET external_reference=?,"
            "observed_fact_json=?,observed_fact_sha256=? WHERE observation_id=?",
            (
                "not-a-canonical-operation",
                fact_json,
                hashlib.sha256(fact_json.encode()).hexdigest(),
                row[0],
            ),
        )
        connection.commit()
    finally:
        connection.close()
    _publish_backup(
        inventory,
        backup_root,
        work_root,
        backup_id="noncanonical-effect",
    )
    before = database_snapshot(database)
    backup_before = tree_snapshot(backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            inventory,
            backup_root=backup_root,
            backup_id="noncanonical-effect",
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "effect_delivery_identity_invalid"
    assert database_snapshot(database) == before
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_noncanonical_legacy_source_identity_without_mutation(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root, database, _backup_id = _case(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE board_legacy_effect_observations SET observation_id='bad-observation-id'"
        )
        connection.commit()
    finally:
        connection.close()
    _publish_backup(inventory, backup_root, work_root, backup_id="noncanonical-source")
    before = database_snapshot(database)
    backup_before = tree_snapshot(backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            inventory,
            backup_root=backup_root,
            backup_id="noncanonical-source",
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "effect_delivery_identity_invalid"
    assert database_snapshot(database) == before
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_missing_legacy_cutover_owner_without_mutation(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root, database, _backup_id = _case(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE board_publication_cutover SET caller_kid=NULL")
        connection.commit()
    finally:
        connection.close()
    _publish_backup(inventory, backup_root, work_root, backup_id="missing-owner")
    before = database_snapshot(database)
    backup_before = tree_snapshot(backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            inventory,
            backup_root=backup_root,
            backup_id="missing-owner",
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "effect_delivery_owner_unproven"
    assert database_snapshot(database) == before
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_unsupported_financial_legacy_kind_without_mutation(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root, database, _backup_id = _case(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE board_legacy_effect_observations SET observation_kind='worker_refund_pending'"
        )
        connection.commit()
    finally:
        connection.close()
    _publish_backup(inventory, backup_root, work_root, backup_id="unsupported-legacy-kind")
    before = database_snapshot(database)
    backup_before = tree_snapshot(backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            inventory,
            backup_root=backup_root,
            backup_id="unsupported-legacy-kind",
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "effect_delivery_legacy_kind_unsupported"
    assert database_snapshot(database) == before
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_partial_v4_schema_without_mutation(tmp_path: Path) -> None:
    inventory, backup_root, work_root, database, _backup_id = _case(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.execute("DROP INDEX idx_board_task_operations_state_effect")
        connection.commit()
    finally:
        connection.close()
    _publish_backup(inventory, backup_root, work_root, backup_id="partial-v4")
    before = database_snapshot(database)
    backup_before = tree_snapshot(backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            inventory,
            backup_root=backup_root,
            backup_id="partial-v4",
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "source_not_v4"
    assert database_snapshot(database) == before
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_mutated_verified_backup_without_source_mutation(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root, database, backup_id = _case(tmp_path)
    manifest = backup_root / backup_id / "payload" / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    before = database_snapshot(database)
    backup_before = tree_snapshot(backup_root)
    with pytest.raises(recovery.RecoveryError):
        _entry_point()(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert database_snapshot(database) == before
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_current_board_kid_without_write_capability(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root, database, _backup_id = _case(tmp_path)
    configs = []
    for config in inventory.configs:
        if config.logical_id != "db-gateway":
            configs.append(config)
            continue
        payload = json.loads(config.captured_path.read_text())
        caller = payload["auth"]["trusted_callers"]["gateway-task-board"]
        caller["capabilities"] = ["gateway.board.read"]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        config.captured_path.write_bytes(encoded)
        configs.append(config.model_copy(update={"sha256": hashlib.sha256(encoded).hexdigest()}))
    restricted_inventory = inventory.model_copy(update={"configs": tuple(configs)})
    _publish_backup(
        restricted_inventory,
        backup_root,
        work_root,
        backup_id="caller-without-write",
    )
    before = database_snapshot(database)
    backup_before = tree_snapshot(backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            restricted_inventory,
            backup_root=backup_root,
            backup_id="caller-without-write",
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "current_task_board_caller_capability_missing"
    assert database_snapshot(database) == before
    assert tree_snapshot(backup_root) == backup_before


def test_v5_migration_rejects_native_source_owned_by_foreign_kid(
    tmp_path: Path,
) -> None:
    native = prepare_native_financial_v4(tmp_path, operation_state="claimed")
    connection = sqlite3.connect(native.source.database)
    try:
        connection.execute(
            "UPDATE board_task_operations SET caller_kid='foreign-native-owner' "
            "WHERE operation_id=?",
            (native.operation_id,),
        )
        connection.commit()
    finally:
        connection.close()
    _publish_backup(
        native.source.inventory,
        native.source.backup_root,
        native.source.work_root,
        backup_id="foreign-native-owner",
    )
    before = database_snapshot(native.source.database)
    backup_before = tree_snapshot(native.source.backup_root)
    with pytest.raises(migration.MigrationError) as raised:
        _entry_point()(
            native.source.inventory,
            backup_root=native.source.backup_root,
            backup_id="foreign-native-owner",
            work_root=native.source.work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.reason == "effect_delivery_owner_mismatch"
    assert database_snapshot(native.source.database) == before
    assert tree_snapshot(native.source.backup_root) == backup_before
