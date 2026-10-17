"""Failing-first coverage for the explicit MP-04 v1-to-v2 migration."""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from db_gateway_service import recovery
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, classify_schema
from tests.unit import test_mp06_migration as v0_migration_tests

if TYPE_CHECKING:
    from service_commons.recovery_contract import RecoveryInventoryV1

pytestmark = pytest.mark.unit

_SIDECAR_TABLES = {
    "bank_operation_receipts",
    "bank_operation_legs",
    "bank_legacy_transaction_observations",
    "bank_legacy_escrow_observations",
    "bank_receipt_cutover",
}


def test_v1_to_v2_migration_api_exists_without_collection_failure() -> None:
    module = importlib.import_module("db_gateway_service.migration")
    assert callable(getattr(module, "migrate_v1_to_v2", None)), (
        "explicit v1-to-v2 migration is not implemented"
    )


def _prepared_v1(tmp_path: Path) -> tuple[RecoveryInventoryV1, Path, Path, Path]:
    inventory, backup_root, work_root, database = v0_migration_tests._prepared_v0(tmp_path)
    v0_migration_tests._migrate(inventory, backup_root, work_root)
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="before-mp04",
        work_root=work_root,
    )
    return inventory, backup_root, work_root, database


def _independent_snapshot(database: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        tables: dict[str, object] = {}
        for _kind, table, _sql in objects:
            if _kind != "table" or table == "schema_metadata" or table in _SIDECAR_TABLES:
                continue
            columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_xinfo("{table}")')]
            projection = ", ".join(f'typeof("{column}"), "{column}"' for column in columns)
            values = connection.execute(
                f'SELECT rowid, {projection} FROM "{table}" ORDER BY rowid'
            ).fetchall()
            tables[str(table)] = values
        sequence = connection.execute(
            "SELECT name, seq FROM sqlite_sequence ORDER BY name"
        ).fetchall()
        return {"objects": objects, "tables": tables, "sqlite_sequence": sequence}
    finally:
        connection.close()


def test_complete_exact_v1_migrates_to_verified_v2(tmp_path: Path) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, database = _prepared_v1(tmp_path)
    before = _independent_snapshot(database)
    try:
        result = migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp04",
            work_root=work_root,
            busy_timeout_ms=5000,
        )
    except MigrationError as exc:
        pytest.fail(f"migration failed: {exc.reason} {exc.safe_details}")

    assert result.applied is True
    assert result.schema_version == 2
    assert result.observation == "committed_v2_verified"
    after = _independent_snapshot(database)
    assert after["tables"] == before["tables"]
    assert after["sqlite_sequence"] == before["sqlite_sequence"]
    before_objects = {(row[0], row[1]) for row in before["objects"]}
    after_objects = {(row[0], row[1]) for row in after["objects"]}
    added = after_objects - before_objects
    assert {name for kind, name in added if kind == "table"} == _SIDECAR_TABLES
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V2
        assert audit_receipt_ownership(connection).ownership_clean is True
        assert connection.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            2,
            1,
            "before-mp04",
            result.manifest_sha256,
        )
        cutover = connection.execute(
            "SELECT cutover_kind, source_schema_version, source_migrated_from_version, "
            "source_migration_backup_id, source_migration_backup_manifest_sha256, "
            "legacy_transaction_count, legacy_escrow_count FROM bank_receipt_cutover"
        ).fetchone()
        assert cutover is not None
        assert cutover[:4] == ("v1_to_v2", 1, 0, "before-mp06")
        assert isinstance(cutover[4], str) and len(cutover[4]) == 64
        assert cutover[5:] == (3, 2)
    finally:
        connection.close()


def test_v0_source_is_rejected_without_implicit_chain(tmp_path: Path) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, database = v0_migration_tests._prepared_v0(tmp_path)
    with pytest.raises(MigrationError) as captured:
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp06",
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert captured.value.reason == "source_not_v1"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.SUPPORTED_V0
    finally:
        connection.close()


def test_precommit_fault_rolls_back_to_exact_v1(tmp_path: Path) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, database = _prepared_v1(tmp_path)

    def fail_before_commit(point: str) -> None:
        if point == "before_commit":
            raise RuntimeError("injected precommit failure")

    with (
        patch("db_gateway_service.receipt_migration._fault", side_effect=fail_before_commit),
        pytest.raises(MigrationError) as captured,
    ):
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp04",
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert captured.value.reason == "migration_failed"
    assert captured.value.safe_details["observation"] == "not_applied_v1_observed"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V1
    finally:
        connection.close()


def test_commit_exception_is_uncertain_and_never_retried(tmp_path: Path) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, database = _prepared_v1(tmp_path)
    with (
        patch(
            "db_gateway_service.receipt_migration._commit",
            side_effect=sqlite3.OperationalError("injected commit uncertainty"),
        ) as commit,
        pytest.raises(MigrationError) as captured,
    ):
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp04",
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert commit.call_count == 1
    assert captured.value.reason == "commit_outcome_uncertain"
    assert captured.value.safe_details["observation"] in {
        "not_applied_v1_observed",
        "committed_v2_observed",
        "inconsistent_state_observed",
        "unreadable_state",
    }
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V1
    finally:
        connection.close()


def test_invalid_timeouts_fail_before_backup_or_writer_access() -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory = cast("RecoveryInventoryV1", object())
    invalid_values: tuple[object, ...] = (True, -1, 2_147_483_648, "5000", None)
    for value in invalid_values:
        with (
            patch("db_gateway_service.receipt_migration.open_backup_bound_source") as backup,
            pytest.raises(MigrationError),
        ):
            migrate_v1_to_v2(
                inventory,
                backup_root=Path("/not-used"),
                backup_id="not-used",
                work_root=Path("/not-used"),
                busy_timeout_ms=cast("int", value),
            )
        backup.assert_not_called()


def test_maximum_busy_timeout_is_accepted(tmp_path: Path) -> None:
    from db_gateway_service.migration import migrate_v1_to_v2

    inventory, backup_root, work_root, _database = _prepared_v1(tmp_path)
    result = migrate_v1_to_v2(
        inventory,
        backup_root=backup_root,
        backup_id="before-mp04",
        work_root=work_root,
        busy_timeout_ms=2_147_483_647,
    )
    assert result.schema_version == 2


def test_source_change_after_backup_is_rejected_and_backup_is_unchanged(tmp_path: Path) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, database = _prepared_v1(tmp_path)
    backup_digest = v0_migration_tests._tree_digest(backup_root / "before-mp04")
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO events (event_source, event_type, timestamp, summary, payload) "
        "VALUES ('bank', 'source.changed', 'now', 'changed', '{}')"
    )
    connection.commit()
    connection.close()

    with (
        patch("db_gateway_service.receipt_migration._open_live_writer") as writer,
        pytest.raises(MigrationError),
    ):
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp04",
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    writer.assert_not_called()
    assert v0_migration_tests._tree_digest(backup_root / "before-mp04") == backup_digest


@pytest.mark.parametrize("corruption", ["money", "partial-schema"])
def test_corrupt_backup_source_is_rejected_before_live_writer(
    tmp_path: Path,
    corruption: str,
) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, database = _prepared_v1(tmp_path)
    connection = sqlite3.connect(database)
    if corruption == "money":
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE bank_accounts SET balance='bad' WHERE account_id='a-external'")
    else:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE reputation_feedback")
    connection.commit()
    connection.close()
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id=f"corrupt-{corruption}",
        work_root=work_root,
    )

    with (
        patch("db_gateway_service.receipt_migration._open_live_writer") as writer,
        pytest.raises(MigrationError),
    ):
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id=f"corrupt-{corruption}",
            work_root=work_root,
            busy_timeout_ms=5000,
        )
    writer.assert_not_called()


def test_resolved_legacy_escrow_gets_exact_resolution_marker(tmp_path: Path) -> None:
    from db_gateway_service.migration import migrate_v1_to_v2

    inventory, backup_root, work_root, database = _prepared_v1(tmp_path)
    connection = sqlite3.connect(database)
    event = connection.execute(
        "INSERT INTO events (event_source, event_type, timestamp, agent_id, summary, payload) "
        "VALUES ('bank', 'escrow.released', 'now', 'a-worker', 'released', '{}')"
    )
    event_id = int(event.lastrowid)
    connection.execute("UPDATE bank_accounts SET balance=50 WHERE account_id='a-worker'")
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-release-legacy', 'a-worker', 'escrow_release', 50, 50, "
        "'esc-1', 'now', ?)",
        (event_id,),
    )
    connection.execute(
        "UPDATE bank_escrow SET status='released', resolved_at='now' WHERE escrow_id='esc-1'"
    )
    connection.commit()
    connection.close()
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="resolved-v1",
        work_root=work_root,
    )

    migrate_v1_to_v2(
        inventory,
        backup_root=backup_root,
        backup_id="resolved-v1",
        work_root=work_root,
        busy_timeout_ms=5000,
    )

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        marker = connection.execute(
            "SELECT observed_status, resolution_fact_sha256 "
            "FROM bank_legacy_escrow_observations WHERE escrow_id='esc-1'"
        ).fetchone()
        assert marker is not None
        assert marker[0] == "released"
        assert isinstance(marker[1], str) and len(marker[1]) == 64
        assert audit_receipt_ownership(connection).ownership_clean is True
    finally:
        connection.close()


@pytest.mark.parametrize("secondary", ["close", "evidence"])
def test_secondary_cleanup_failure_preserves_precommit_primary(
    tmp_path: Path,
    secondary: str,
) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, _database = _prepared_v1(tmp_path)

    def fail_before_commit(point: str) -> None:
        if point == "before_commit":
            raise RuntimeError("primary precommit failure")

    secondary_patch = (
        patch(
            "db_gateway_service.migration._dynamic_close",
            side_effect=RuntimeError("secondary close failure"),
        )
        if secondary == "close"
        else patch(
            "db_gateway_service.recovery.BackupBoundSource.write_evidence",
            side_effect=RuntimeError("secondary evidence failure"),
        )
    )
    with (
        patch("db_gateway_service.receipt_migration._fault", side_effect=fail_before_commit),
        secondary_patch,
        pytest.raises(MigrationError) as captured,
    ):
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp04",
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert captured.value.reason == "migration_failed"
    assert isinstance(captured.value.__cause__, RuntimeError)
    assert str(captured.value.__cause__) == "primary precommit failure"
    notes = getattr(captured.value.__cause__, "__notes__", [])
    assert any("secondary" in note.lower() or "also failed" in note.lower() for note in notes)


def test_live_writer_open_failure_is_structured_and_releases_authority(tmp_path: Path) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, database = _prepared_v1(tmp_path)
    with (
        patch(
            "db_gateway_service.receipt_migration._open_live_writer",
            side_effect=sqlite3.OperationalError("injected open failure"),
        ),
        pytest.raises(MigrationError) as captured,
    ):
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp04",
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert captured.value.reason == "source_open_failed"
    assert captured.value.safe_details["observation"] == "not_applied_v1_observed"
    assert Path(str(captured.value.safe_details["evidence_path"])).is_file()
    assert classify_schema(sqlite3.connect(database)).state is SchemaState.CURRENT_V1
    result = migrate_v1_to_v2(
        inventory,
        backup_root=backup_root,
        backup_id="before-mp04",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert result.schema_version == 2


def test_postcommit_cleanup_failure_has_observation_evidence_and_cause(tmp_path: Path) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2
    from db_gateway_service.receipt_migration import _dispose_writer as real_dispose

    inventory, backup_root, work_root, _database = _prepared_v1(tmp_path)

    def report_cleanup_failure(connection: sqlite3.Connection, primary: BaseException | None):
        codes, _error = real_dispose(connection, primary)
        return [*codes, "dynamic_close_failed"], RuntimeError("reported close failure")

    with (
        patch(
            "db_gateway_service.receipt_migration._dispose_writer",
            side_effect=report_cleanup_failure,
        ),
        pytest.raises(MigrationError) as captured,
    ):
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp04",
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert captured.value.reason == "post_commit_cleanup_failed"
    assert captured.value.safe_details["observation"] == "committed_v2_observed"
    assert captured.value.safe_details["native_close_completed"] is True
    assert Path(str(captured.value.safe_details["evidence_path"])).is_file()
    assert isinstance(captured.value.__cause__, RuntimeError)


def test_postcommit_evidence_failure_reports_committed_v2(tmp_path: Path) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, _database = _prepared_v1(tmp_path)
    with (
        patch(
            "db_gateway_service.receipt_migration._write_evidence",
            side_effect=RuntimeError("injected evidence failure"),
        ),
        pytest.raises(MigrationError) as captured,
    ):
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp04",
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert captured.value.reason == "post_commit_evidence_failed"
    assert captured.value.safe_details["observation"] == "committed_v2_observed"
    assert isinstance(captured.value.__cause__, RuntimeError)


def test_commit_then_raise_is_uncertain_but_observes_valid_v2(tmp_path: Path) -> None:
    from db_gateway_service.migration import MigrationError, migrate_v1_to_v2

    inventory, backup_root, work_root, database = _prepared_v1(tmp_path)

    def commit_then_raise(connection: sqlite3.Connection) -> None:
        connection.execute("COMMIT").close()
        raise sqlite3.OperationalError("acknowledgment lost after commit")

    with (
        patch(
            "db_gateway_service.receipt_migration._commit",
            side_effect=commit_then_raise,
        ) as commit,
        pytest.raises(MigrationError) as captured,
    ):
        migrate_v1_to_v2(
            inventory,
            backup_root=backup_root,
            backup_id="before-mp04",
            work_root=work_root,
            busy_timeout_ms=5000,
        )

    assert commit.call_count == 1
    assert captured.value.reason == "commit_outcome_uncertain"
    assert captured.value.safe_details["observation"] == "committed_v2_observed"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V2
        assert audit_receipt_ownership(connection).ownership_clean is True
    finally:
        connection.close()
