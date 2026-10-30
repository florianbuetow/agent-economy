"""Migrate legacy domain facts produced by an actual signed public workload."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from db_gateway_service.money_audit import audit_money
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.recovery import (
    PublicationState,
    create_backup,
    restore_backup,
    verify_backup,
)
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from db_gateway_service.task_contract_audit import audit_task_contracts
from scripts.isolated_test_runtime import IsolatedRuntime
from scripts.recovery_targets import build_target_bindings
from service_auth import AgentFactory
from service_auth.financial_identity import operation_id_for
from service_commons.recovery_contract import RecoveryInventoryV1
from tests.runtime.test_recovery_live_restore import (
    DELIVERABLE,
    _command_body,
    _endpoints,
    _public_workload,
    _task_version,
)

pytestmark = pytest.mark.integration


async def signed_download_asset(agent: object, task_id: str, asset_id: str) -> bytes:
    """Signed Task Board asset download (the SDK method sends no token)."""
    auth_header = agent._auth_header
    request_raw = agent._request_raw
    base_url = agent.config.task_board_url
    headers = auth_header(
        {"action": "download_asset", "task_id": task_id, "asset_id": asset_id},
        audience="task-board",
        resource=f"GET /tasks/{task_id}/assets/{asset_id}",
    )
    url = f"{base_url}/tasks/{task_id}/assets/{asset_id}"
    response = await request_raw("GET", url, headers=headers)
    response.raise_for_status()
    return bytes(response.content)


REPO_ROOT = Path(__file__).resolve().parents[2]
_LEGACY_DOMAIN_TABLES = (
    "bank_accounts",
    "bank_escrow",
    "bank_transactions",
    "board_assets",
    "board_bids",
    "board_tasks",
    "court_claims",
    "court_rebuttals",
    "court_rulings",
    "events",
    "identity_agents",
    "reputation_feedback",
    "sqlite_sequence",
)


def _logical_rows(connection):
    """Snapshot frozen v0/v1 domain rows, storage types, rowids and sequences."""
    result = {}
    for table in _LEGACY_DOMAIN_TABLES:
        columns = sorted(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
        assert columns, f"Missing frozen legacy domain table: {table}"
        selected = ", ".join(f'"{column}"' for column in columns)
        storage_types = ", ".join(f'typeof("{column}")' for column in columns)
        rows = connection.execute(
            f'SELECT rowid, {selected}, {storage_types} FROM "{table}" ORDER BY rowid'
        ).fetchall()
        result[table] = (columns, rows)
    return result


def _install_legacy_fixture(database):
    """Copy frozen domain rows into synthetic v0 without v2 receipt sidecars."""
    connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)
    try:
        original = _logical_rows(connection)
        connection.execute("PRAGMA foreign_keys=OFF")
        for (table,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall():
            connection.execute(f'DROP TABLE "{table}"')
        connection.commit()
        connection.executescript(load_schema_sql(SchemaState.SUPPORTED_V0))
        connection.execute("PRAGMA foreign_keys=OFF")
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (0,)
        for table, (columns, rows) in original.items():
            if table == "sqlite_sequence":
                continue
            selected = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _ in range(len(columns) + 1))
            connection.executemany(
                f'INSERT INTO "{table}" (rowid, {selected}) VALUES ({placeholders})',
                [row[: len(columns) + 1] for row in rows],
            )
        columns, rows = original["sqlite_sequence"]
        connection.execute("DELETE FROM sqlite_sequence")
        connection.executemany(
            "INSERT INTO sqlite_sequence (rowid, name, seq) VALUES (?, ?, ?)",
            [row[: len(columns) + 1] for row in rows],
        )
        connection.commit()
        assert classify_schema(connection).state is SchemaState.SUPPORTED_V0
        assert audit_money(connection).financially_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert _logical_rows(connection) == original
        return original
    finally:
        connection.close()


def _file_hashes(root):
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mode & 0o777,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


async def _resume_signed_workload(config_path, receipt):
    factory = AgentFactory(config_path=config_path)
    poster = factory.user_agent("operator")
    worker = factory.create_agent("alice")
    try:
        await poster.register()
        await worker.register()
        assert poster.agent_id == receipt["poster_id"]
        assert worker.agent_id == receipt["worker_id"]
        task = await poster.get_task(receipt["task_id"])
        assert task["status"] == "submitted"
        assert task["worker_id"] == receipt["worker_id"]
        assert (await poster.get_balance())["balance"] == receipt["balance"]
        assert (
            await signed_download_asset(poster, receipt["task_id"], receipt["asset_id"])
            == DELIVERABLE
        )
        before_worker = (await worker.get_balance())["balance"]
        submitted_version = _task_version(task, expected_status="submitted")
        assert poster.agent_id is not None
        approved = _command_body(
            await poster.approve_task(
                receipt["task_id"],
                operation_id=operation_id_for(
                    "runtime-public-approve", (receipt["task_id"], poster.agent_id)
                ),
                expected_lifecycle_version=submitted_version,
            ),
            expected_status_code=200,
        )
        _task_version(approved, expected_status="approved")
        assert (await poster.get_task(receipt["task_id"]))["status"] == "approved"
        assert (await worker.get_balance())["balance"] == before_worker + 100
    finally:
        await worker.close()
        await poster.close()


def _assert_pre_migration_restore(
    inventory, runtime, backup_root, work_root, target, original_rows
):
    bindings = build_target_bindings(
        inventory,
        target_root=target,
        process_cwds={config.logical_id: config.process_cwd for config in inventory.configs},
        agents_package_root=REPO_ROOT / "agents",
        endpoint_values=_endpoints(inventory, runtime),
    )
    restored = restore_backup(
        backup_root=backup_root,
        backup_id="legacy-live-work",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    assert restored.state is PublicationState.COMPLETE
    restored_uri = bindings.resource_paths["database.primary"].as_uri() + "?mode=ro"
    with closing(sqlite3.connect(restored_uri, uri=True)) as db:
        assert classify_schema(db).state is SchemaState.SUPPORTED_V0
        assert _logical_rows(db) == original_rows


def _assert_v1_database(database, original_rows, backup, backup_id):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as migrated:
        assert classify_schema(migrated).state is SchemaState.CURRENT_V1
        assert _logical_rows(migrated) == original_rows
        assert audit_money(migrated).financially_clean
        assert migrated.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            1,
            0,
            backup_id,
            backup.manifest_sha256,
        )


def _migrate_v1_to_v2(runtime, inventory_path, backup_root, backup_id, work_root):
    try:
        assert (
            runtime.run_command(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "db_gateway_service.recovery_cli",
                    "migrate-v1-to-v2",
                    "--inventory",
                    str(inventory_path),
                    "--backup-root",
                    str(backup_root),
                    "--backup-id",
                    backup_id,
                    "--work-root",
                    str(work_root),
                    "--busy-timeout-ms",
                    "5000",
                ],
                cwd=REPO_ROOT / "services/db-gateway",
            )
            == 0
        )
    finally:
        runtime.stop()


def _assert_v2_database(database, original_rows, v1_backup, v1_backup_id, v2_backup):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as migrated:
        assert classify_schema(migrated).state is SchemaState.CURRENT_V2
        assert _logical_rows(migrated) == original_rows
        assert audit_money(migrated).financially_clean
        ownership = audit_receipt_ownership(migrated)
        assert ownership.ownership_clean
        assert ownership.native_receipt_count == 0
        assert ownership.legacy_transaction_count == ownership.transaction_count
        assert ownership.legacy_escrow_count == ownership.escrow_count
        assert migrated.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            2,
            1,
            "before-receipt-migration",
            v2_backup.manifest_sha256,
        )
        cutover = migrated.execute(
            "SELECT cutover_kind, source_schema_version, source_migrated_from_version, "
            "source_migration_backup_id, source_migration_backup_manifest_sha256, "
            "migration_backup_id, migration_backup_manifest_sha256, "
            "legacy_transaction_count, legacy_escrow_count FROM bank_receipt_cutover"
        ).fetchone()
        assert cutover == (
            "v1_to_v2",
            1,
            0,
            v1_backup_id,
            v1_backup.manifest_sha256,
            "before-receipt-migration",
            v2_backup.manifest_sha256,
            ownership.transaction_count,
            ownership.escrow_count,
        )


def _upgrade_v1_to_v2(
    runtime,
    inventory,
    inventory_path,
    database,
    original_rows,
    v1_backup,
    v1_backup_id,
    backup_root,
    work_root,
):
    v2_backup = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="before-receipt-migration",
        work_root=work_root,
    )
    assert v2_backup.state is PublicationState.COMPLETE
    v2_backup_hashes = _file_hashes(v2_backup.location)
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="before-receipt-migration",
        work_root=work_root,
    )
    assert verified.artifact_verified and verified.identity_complete
    _migrate_v1_to_v2(
        runtime,
        inventory_path,
        backup_root,
        "before-receipt-migration",
        work_root,
    )
    _assert_v2_database(database, original_rows, v1_backup, v1_backup_id, v2_backup)
    assert _file_hashes(v2_backup.location) == v2_backup_hashes
    return v2_backup, v2_backup_hashes


def _migrate_v2_to_v3(runtime, inventory_path, backup_root, backup_id, work_root):
    try:
        assert (
            runtime.run_command(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "db_gateway_service.recovery_cli",
                    "migrate-v2-to-v3",
                    "--inventory",
                    str(inventory_path),
                    "--backup-root",
                    str(backup_root),
                    "--backup-id",
                    backup_id,
                    "--work-root",
                    str(work_root),
                    "--busy-timeout-ms",
                    "5000",
                ],
                cwd=REPO_ROOT / "services/db-gateway",
            )
            == 0
        )
    finally:
        runtime.stop()


def _assert_v3_database(database, original_rows, v3_backup):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as migrated:
        assert classify_schema(migrated).state is SchemaState.CURRENT_V3
        assert _logical_rows(migrated) == original_rows
        assert audit_money(migrated).financially_clean
        assert audit_receipt_ownership(migrated).ownership_clean
        assert audit_task_contracts(migrated).contracts_clean
        assert migrated.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrated.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            3,
            2,
            "before-task-migration",
            v3_backup.manifest_sha256,
        )
        assert migrated.execute("SELECT COUNT(*) FROM board_task_operations").fetchone() == (0,)
        assert migrated.execute("SELECT COUNT(*) FROM board_task_contracts").fetchone() == (
            len(original_rows["board_tasks"][1]),
        )


def _upgrade_v2_to_v3(
    runtime,
    inventory,
    inventory_path,
    database,
    original_rows,
    backup_root,
    work_root,
):
    v3_backup = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="before-task-migration",
        work_root=work_root,
    )
    assert v3_backup.state is PublicationState.COMPLETE
    v3_backup_hashes = _file_hashes(v3_backup.location)
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="before-task-migration",
        work_root=work_root,
    )
    assert verified.artifact_verified and verified.identity_complete
    _migrate_v2_to_v3(
        runtime,
        inventory_path,
        backup_root,
        "before-task-migration",
        work_root,
    )
    _assert_v3_database(database, original_rows, v3_backup)
    assert _file_hashes(v3_backup.location) == v3_backup_hashes
    return v3_backup, v3_backup_hashes


def _migrate_v3_to_v4(runtime, inventory_path, backup_root, backup_id, work_root):
    try:
        assert (
            runtime.run_command(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "db_gateway_service.recovery_cli",
                    "migrate-v3-to-v4",
                    "--inventory",
                    str(inventory_path),
                    "--backup-root",
                    str(backup_root),
                    "--backup-id",
                    backup_id,
                    "--work-root",
                    str(work_root),
                    "--busy-timeout-ms",
                    "5000",
                ],
                cwd=REPO_ROOT / "services/db-gateway",
            )
            == 0
        )
    finally:
        runtime.stop()


def _assert_v4_database(database, original_rows, v4_backup):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as migrated:
        assert classify_schema(migrated).state is SchemaState.CURRENT_V4
        assert _logical_rows(migrated) == original_rows
        assert audit_money(migrated).financially_clean
        assert audit_receipt_ownership(migrated).ownership_clean
        assert audit_task_contracts(migrated).contracts_clean
        assert audit_publications(migrated).publications_clean
        assert migrated.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrated.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            4,
            3,
            "before-publication-migration",
            v4_backup.manifest_sha256,
        )


def _upgrade_v3_to_v4(
    runtime,
    inventory,
    inventory_path,
    database,
    original_rows,
    backup_root,
    work_root,
):
    v4_backup = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="before-publication-migration",
        work_root=work_root,
    )
    assert v4_backup.state is PublicationState.COMPLETE
    v4_backup_hashes = _file_hashes(v4_backup.location)
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="before-publication-migration",
        work_root=work_root,
    )
    assert verified.artifact_verified and verified.identity_complete
    _migrate_v3_to_v4(
        runtime,
        inventory_path,
        backup_root,
        "before-publication-migration",
        work_root,
    )
    _assert_v4_database(database, original_rows, v4_backup)
    assert _file_hashes(v4_backup.location) == v4_backup_hashes
    return v4_backup, v4_backup_hashes


def _assert_runtime_artifacts_unchanged(runtime, original_keys, original_assets):
    assert _file_hashes(runtime.run_dir / "keys") == original_keys
    assert _file_hashes(runtime.run_dir / "assets") == original_assets


def _upgrade_v4_to_v5(
    runtime, inventory, inventory_path, database, original_rows, backup_root, work_root
):
    """Preserve the verified V4 result, then explicitly admit the current runtime."""
    backup_id = "before-effect-recovery-migration"
    backup = create_backup(
        inventory, backup_root=backup_root, backup_id=backup_id, work_root=work_root
    )
    assert backup.state is PublicationState.COMPLETE
    hashes = _file_hashes(backup.location)
    try:
        assert (
            runtime.run_command(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    "-m",
                    "db_gateway_service.recovery_cli",
                    "migrate-v4-to-v5",
                    "--inventory",
                    str(inventory_path),
                    "--backup-root",
                    str(backup_root),
                    "--backup-id",
                    backup_id,
                    "--work-root",
                    str(work_root),
                    "--busy-timeout-ms",
                    "5000",
                    "--current-task-board-caller-kid",
                    "gateway-task-board",
                ],
                cwd=REPO_ROOT / "services/db-gateway",
            )
            == 0
        )
    finally:
        runtime.stop()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as migrated:
        assert classify_schema(migrated).state is SchemaState.CURRENT_V5
        assert _logical_rows(migrated) == original_rows
        assert migrated.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            5,
            4,
            backup_id,
            backup.manifest_sha256,
        )
        assert migrated.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
    assert _file_hashes(backup.location) == hashes
    return backup, hashes


def _upgrade_v2_to_v4_preserving_v2(
    runtime,
    inventory,
    inventory_path,
    database,
    original_rows,
    backup_root,
    work_root,
    v2_backup,
    v2_backup_hashes,
):
    v3_backup, v3_backup_hashes = _upgrade_v2_to_v3(
        runtime,
        inventory,
        inventory_path,
        database,
        original_rows,
        backup_root,
        work_root,
    )
    assert _file_hashes(v2_backup.location) == v2_backup_hashes
    v4_backup, v4_backup_hashes = _upgrade_v3_to_v4(
        runtime,
        inventory,
        inventory_path,
        database,
        original_rows,
        backup_root,
        work_root,
    )
    assert _file_hashes(v2_backup.location) == v2_backup_hashes
    assert _file_hashes(v3_backup.location) == v3_backup_hashes
    return v3_backup, v3_backup_hashes, v4_backup, v4_backup_hashes


def _assert_upgrade_backups_unchanged(
    v2_backup,
    v2_backup_hashes,
    v3_backup,
    v3_backup_hashes,
    v4_backup,
    v4_backup_hashes,
):
    assert _file_hashes(v2_backup.location) == v2_backup_hashes
    assert _file_hashes(v3_backup.location) == v3_backup_hashes
    assert _file_hashes(v4_backup.location) == v4_backup_hashes


def test_migration_cli_preserves_live_receipts_and_allows_signed_continuation(
    tmp_path: Path,
):
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    try:
        runtime.start()
        assert runtime.provision() == 0
        receipt = asyncio.run(_public_workload(runtime.agent_config_path))
        prepared = runtime.prepare_recovery_and_stop(
            registry_timeout_seconds=10.0, registry_max_attempts=2
        )
    finally:
        runtime.stop()
    (tmp_path / "runtime-before-migration.json").write_bytes(
        (runtime.run_dir / "runtime.json").read_bytes()
    )
    inventory = RecoveryInventoryV1.model_validate_json(prepared.inventory_path.read_bytes())
    assert all(
        not record.running and record.group_drained for record in inventory.process_ownership
    )
    database = runtime.run_dir / "data/economy.db"
    original_rows = _install_legacy_fixture(database)
    original_keys = _file_hashes(runtime.run_dir / "keys")
    original_assets = _file_hashes(runtime.run_dir / "assets")
    work_root = tmp_path / "migration-work"
    work_root.mkdir(mode=0o700)
    backup_root = runtime.run_dir / "backups"
    backup = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="legacy-live-work",
        work_root=work_root,
    )
    assert backup.state is PublicationState.COMPLETE
    original_artifact = _file_hashes(backup.location)

    try:
        exit_code = runtime.run_command(
            [
                "uv",
                "run",
                "python",
                "-m",
                "db_gateway_service.recovery_cli",
                "migrate",
                "--inventory",
                str(prepared.inventory_path),
                "--backup-root",
                str(backup_root),
                "--backup-id",
                "legacy-live-work",
                "--work-root",
                str(work_root),
                "--busy-timeout-ms",
                "5000",
            ],
            cwd=REPO_ROOT / "services/db-gateway",
        )
        assert exit_code == 0
    finally:
        runtime.stop()
    _assert_v1_database(database, original_rows, backup, "legacy-live-work")
    evidence = list(work_root.rglob("migration-result.json"))
    assert len(evidence) == 1
    assert json.loads(evidence[0].read_text())["status"] == "committed_v1_verified"
    assert evidence[0].stat().st_mode & 0o777 == 0o600
    verified = verify_backup(
        backup_root=backup_root, backup_id="legacy-live-work", work_root=work_root
    )
    assert verified.artifact_verified and verified.identity_complete
    assert verified.migration_eligible is False
    _assert_pre_migration_restore(
        inventory,
        runtime,
        backup_root,
        work_root,
        tmp_path / "restored-pre-migration",
        original_rows,
    )
    assert _file_hashes(backup.location) == original_artifact
    _assert_runtime_artifacts_unchanged(runtime, original_keys, original_assets)

    v2_backup, v2_backup_hashes = _upgrade_v1_to_v2(
        runtime,
        inventory,
        prepared.inventory_path,
        database,
        original_rows,
        backup,
        "legacy-live-work",
        backup_root,
        work_root,
    )
    assert _file_hashes(backup.location) == original_artifact

    v3_backup, v3_backup_hashes, v4_backup, v4_backup_hashes = _upgrade_v2_to_v4_preserving_v2(
        runtime,
        inventory,
        prepared.inventory_path,
        database,
        original_rows,
        backup_root,
        work_root,
        v2_backup,
        v2_backup_hashes,
    )

    v5_backup, v5_backup_hashes = _upgrade_v4_to_v5(
        runtime,
        inventory,
        prepared.inventory_path,
        database,
        original_rows,
        backup_root,
        work_root,
    )
    try:
        runtime.start()
        asyncio.run(_resume_signed_workload(runtime.agent_config_path, receipt))
    finally:
        runtime.stop()
    _assert_runtime_artifacts_unchanged(runtime, original_keys, original_assets)
    assert _file_hashes(v5_backup.location) == v5_backup_hashes
    _assert_upgrade_backups_unchanged(
        v2_backup,
        v2_backup_hashes,
        v3_backup,
        v3_backup_hashes,
        v4_backup,
        v4_backup_hashes,
    )
