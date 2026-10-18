"""T-122 fail-closed rejection proofs for the explicit v2-to-v3 migration.

The producer does not exist at the current baseline. One required test records
that capability red before running its prepared rejection scenarios in sequence.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from db_gateway_service import migration, recovery
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, classify_schema
from tests.unit import test_mp06_migration as old_migration
from tests.unit import test_mp07_migration_contract as parent_contract

if TYPE_CHECKING:
    from pathlib import Path

    from service_commons.recovery_contract import RecoveryInventoryV1

pytestmark = pytest.mark.unit

_UNSUPPORTED_SOURCE_REASON = "source_not_v2"
_INVALID_CUTOFF_REASON = "task_cutoff_inconsistent"
_INVALID_ACCEPTED_LINK_REASON = "accepted_bid_linkage_invalid"
_INVALID_DISPUTE_LINK_REASON = "dispute_claim_linkage_invalid"

type _DatabaseSnapshot = dict[str, object]
type _StorageSnapshot = dict[str, bytes]


class _MigrateV2ToV3(Protocol):
    def __call__(
        self,
        inventory: RecoveryInventoryV1,
        *,
        backup_root: Path,
        backup_id: str,
        work_root: Path,
        busy_timeout_ms: int,
    ) -> migration.MigrationResult: ...


@dataclass(frozen=True)
class _PreservationEvidence:
    database: _DatabaseSnapshot
    metadata: tuple[object, ...]
    storage: _StorageSnapshot
    backup_verification: dict[str, object]
    backup_digest: str


@dataclass(frozen=True)
class _CorruptionCase:
    name: str
    sql: str
    expected_reason: str


_CORRUPTIONS = (
    _CorruptionCase(
        name="inconsistent_execution_cutoff",
        sql="UPDATE board_tasks SET execution_deadline="
        "'2026-10-17T12:00:01.500001Z' WHERE task_id='t-1'",
        expected_reason=_INVALID_CUTOFF_REASON,
    ),
    _CorruptionCase(
        name="accepted_worker_does_not_match_bid",
        sql="UPDATE board_tasks SET worker_id='a-external' WHERE task_id='t-1'",
        expected_reason=_INVALID_ACCEPTED_LINK_REASON,
    ),
    _CorruptionCase(
        name="court_respondent_does_not_match_worker",
        sql="UPDATE court_claims SET respondent_id='a-external' WHERE task_id='t-1'",
        expected_reason=_INVALID_DISPUTE_LINK_REASON,
    ),
)


def _migration_entry_point() -> _MigrateV2ToV3 | None:
    value = getattr(migration, "migrate_v2_to_v3", None)
    return cast("_MigrateV2ToV3", value) if callable(value) else None


def _assert_valid_v2_control(database: Path) -> None:
    """Prove the parent fixture is valid before this test corrupts one linkage."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V2
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def _fresh_verified_backup(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
) -> dict[str, object]:
    publication = recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    assert publication.state is recovery.PublicationState.COMPLETE
    verification = recovery.verify_backup(
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    result = verification.model_dump(mode="json")
    assert result["artifact_verified"] is True
    assert result["snapshot_preserved"] is True
    assert result["identity_complete"] is True
    return result


def _metadata(database: Path) -> tuple[object, ...]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT * FROM schema_metadata").fetchone()
        assert row is not None
        return tuple(row)
    finally:
        connection.close()


def _preservation_evidence(
    *,
    inventory: RecoveryInventoryV1,
    backup_root: Path,
    backup_id: str,
    database: Path,
    backup_verification: dict[str, object],
) -> _PreservationEvidence:
    return _PreservationEvidence(
        database=parent_contract._snapshot(database),
        metadata=_metadata(database),
        storage=parent_contract._storage_snapshot(inventory.source_root),
        backup_verification=backup_verification,
        backup_digest=old_migration._tree_digest(backup_root / backup_id),
    )


def _assert_preserved(
    before: _PreservationEvidence,
    *,
    inventory: RecoveryInventoryV1,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    database: Path,
) -> None:
    assert parent_contract._snapshot(database) == before.database
    assert _metadata(database) == before.metadata
    assert parent_contract._storage_snapshot(inventory.source_root) == before.storage
    assert old_migration._tree_digest(backup_root / backup_id) == before.backup_digest
    assert (
        recovery.verify_backup(
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
        ).model_dump(mode="json")
        == before.backup_verification
    )


def _assert_v1_source_rejected(migrate: _MigrateV2ToV3, tmp_path: Path) -> None:
    tmp_path.mkdir()
    inventory, backup_root, work_root, database = old_migration._prepared_v0(tmp_path)
    old_migration._migrate(inventory, backup_root, work_root)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V1
        assert audit_money(connection).financially_clean
    finally:
        connection.close()

    backup_id = "reject-v1-no-chain"
    verification = _fresh_verified_backup(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    before = _preservation_evidence(
        inventory=inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        database=database,
        backup_verification=verification,
    )

    with pytest.raises(migration.MigrationError) as raised:
        migrate(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert raised.value.reason == _UNSUPPORTED_SOURCE_REASON
    assert raised.value.safe_details.get("source_schema") == SchemaState.CURRENT_V1.value
    _assert_preserved(
        before,
        inventory=inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        database=database,
    )


def _assert_invalid_v2_history_rejected(
    migrate: _MigrateV2ToV3,
    case: _CorruptionCase,
    tmp_path: Path,
) -> None:
    tmp_path.mkdir()
    inventory, backup_root, work_root, database = parent_contract._prepared_v2(tmp_path)
    _assert_valid_v2_control(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute(case.sql)
        connection.commit()
    finally:
        connection.close()

    # The corruption is domain-invalid while remaining structurally v2 with
    # clean money, receipt ownership and foreign keys. The fresh backup below
    # therefore matches the exact source the migration must reject.
    _assert_valid_v2_control(database)
    backup_id = f"reject-{case.name}"
    verification = _fresh_verified_backup(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    before = _preservation_evidence(
        inventory=inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        database=database,
        backup_verification=verification,
    )

    with pytest.raises(migration.MigrationError) as raised:
        migrate(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert raised.value.reason == case.expected_reason
    assert raised.value.safe_details.get("task_id") == "t-1"
    _assert_preserved(
        before,
        inventory=inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        database=database,
    )


def test_v2_to_v3_rejects_invalid_sources_without_mutation(tmp_path: Path) -> None:
    migrate = _migration_entry_point()
    assert migrate is not None, (
        "Missing explicit migrate_v2_to_v3; all required rejection scenarios remain prepared"
    )

    _assert_v1_source_rejected(migrate, tmp_path / "v1-source")
    for case in _CORRUPTIONS:
        _assert_invalid_v2_history_rejected(
            migrate,
            case,
            tmp_path / case.name,
        )
