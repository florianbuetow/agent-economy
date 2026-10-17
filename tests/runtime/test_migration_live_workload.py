"""Migrate a legacy-layout fixture carrying actual signed public-API receipts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from scripts.isolated_test_runtime import IsolatedRuntime
from scripts.recovery_targets import build_target_bindings
from service_auth import AgentFactory
from service_commons.recovery_contract import RecoveryInventoryV1
from tests.runtime.test_recovery_live_restore import DELIVERABLE, _endpoints, _public_workload

from db_gateway_service.money_audit import audit_money
from db_gateway_service.recovery import (
    PublicationState,
    create_backup,
    restore_backup,
    verify_backup,
)
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def _logical_rows(connection):
    """Independent row/storage-type evidence, including rowids and event sequence."""
    result = {}
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name != 'schema_metadata' ORDER BY name"
    ).fetchall()
    for (table,) in tables:
        columns = sorted(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
        selected = ", ".join(f'"{column}"' for column in columns)
        storage_types = ", ".join(f'typeof("{column}")' for column in columns)
        rows = connection.execute(
            f'SELECT rowid, {selected}, {storage_types} FROM "{table}" ORDER BY rowid'
        ).fetchall()
        result[table] = (columns, rows)
    return result


def _install_legacy_fixture(database):
    """Rebuild only disposable test state in the frozen legacy schema, keeping every row."""
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
        assert await poster.download_asset(receipt["task_id"], receipt["asset_id"]) == DELIVERABLE
        before_worker = (await worker.get_balance())["balance"]
        await poster.approve_task(receipt["task_id"])
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


def _assert_migrated_database(database, original_rows, backup):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as migrated:
        assert classify_schema(migrated).state is SchemaState.CURRENT_V1
        assert _logical_rows(migrated) == original_rows
        assert audit_money(migrated).financially_clean
        assert migrated.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            1,
            0,
            "legacy-live-work",
            backup.manifest_sha256,
        )


def test_migration_cli_preserves_live_receipts_and_allows_signed_continuation(tmp_path: Path):
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
        inventory, backup_root=backup_root, backup_id="legacy-live-work", work_root=work_root
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
    _assert_migrated_database(database, original_rows, backup)
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
    assert _file_hashes(runtime.run_dir / "keys") == original_keys
    assert _file_hashes(runtime.run_dir / "assets") == original_assets

    try:
        runtime.start()
        asyncio.run(_resume_signed_workload(runtime.agent_config_path, receipt))
    finally:
        runtime.stop()
    assert _file_hashes(runtime.run_dir / "keys") == original_keys
    assert _file_hashes(runtime.run_dir / "assets") == original_assets
