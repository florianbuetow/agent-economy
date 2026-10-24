"""Real process death during migration preserves a live workload and permits recovery."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import selectors
import signal
import sqlite3
import subprocess
from pathlib import Path

import pytest
from scripts.isolated_test_runtime import IsolatedRuntime
from service_commons.maintenance_lock import MaintenanceLockMode, hold_existing_maintenance_lock
from service_commons.recovery_contract import RecoveryInventoryV1
from tests.runtime.test_migration_live_workload import (
    _assert_upgrade_backups_unchanged,
    _assert_v1_database,
    _file_hashes,
    _install_legacy_fixture,
    _logical_rows,
    _resume_signed_workload,
    _upgrade_v1_to_v2,
    _upgrade_v2_to_v4_preserving_v2,
    _upgrade_v4_to_v5,
)
from tests.runtime.test_recovery_live_restore import _public_workload

from db_gateway_service.money_audit import audit_money
from db_gateway_service.recovery import PublicationState, create_backup, verify_backup
from db_gateway_service.schema import SchemaState, classify_schema

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]

_CRASH_PROGRAM = """
import json
import os
from pathlib import Path
import signal
import sys

from service_commons.recovery_contract import RecoveryInventoryV1
from db_gateway_service import migration

inventory_path, backup_root, work_root, fault_point = sys.argv[1:]
inventory = RecoveryInventoryV1.model_validate_json(Path(inventory_path).read_bytes())

def pause_after_statement(point):
    if point == fault_point:
        print(json.dumps({"point": point, "pid": os.getpid(), "pgid": os.getpgrp()}), flush=True)
        while True:
            signal.pause()

migration._fault = pause_after_statement
migration.migrate_v0_to_v1(
    inventory,
    backup_root=Path(backup_root),
    backup_id="before-crash",
    work_root=Path(work_root),
    busy_timeout_ms=5000,
)
raise AssertionError("Migration did not reach the requested crash barrier")
"""


def _kill_paused_migration(runtime, inventory_path, backup_root, work_root, fault_point):
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        "-c",
        _CRASH_PROGRAM,
        str(inventory_path),
        str(backup_root),
        str(work_root),
        fault_point,
    ]
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT / "services/db-gateway",
        env=runtime.environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=30), "Migration did not reach its deterministic barrier"
            line = process.stdout.readline()
        assert line, "Migration exited before its deterministic barrier"
        barrier = json.loads(line)
        assert barrier["point"] == fault_point
        assert barrier["pgid"] == process.pid
        assert process.poll() is None
        assert os.getpgid(barrier["pid"]) == process.pid
        assert os.getsid(barrier["pid"]) == process.pid
        # Kill the owned Python child, allowing uv to reap it and leave no orphan.
        os.kill(barrier["pid"], signal.SIGKILL)
        return_code = process.wait(timeout=15)
        assert return_code != 0
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)
        return {
            **barrier,
            "supervisor_pid": process.pid,
            "signal": int(signal.SIGKILL),
            "return_code": return_code,
            "group_absent": True,
        }
    finally:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def _assert_recovered_legacy(database, original_rows):
    with contextlib.closing(
        sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        assert classify_schema(connection).state is SchemaState.SUPPORTED_V0
        assert _logical_rows(connection) == original_rows
        assert audit_money(connection).financially_clean
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _resume_migration(runtime, inventory_path, backup_root, work_root):
    try:
        assert (
            runtime.run_command(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "db_gateway_service.recovery_cli",
                    "migrate",
                    "--inventory",
                    str(inventory_path),
                    "--backup-root",
                    str(backup_root),
                    "--backup-id",
                    "after-crash",
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


@pytest.mark.parametrize("fault_point", ["after_drop:board_bids", "after_copy:bank_transactions"])
def test_process_death_preserves_legacy_and_signed_workflow_can_resume(tmp_path, fault_point):
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
    (tmp_path / "runtime-before-crash.json").write_bytes(
        (runtime.run_dir / "runtime.json").read_bytes()
    )
    inventory = RecoveryInventoryV1.model_validate_json(prepared.inventory_path.read_bytes())
    database = runtime.run_dir / "data/economy.db"
    original_rows = _install_legacy_fixture(database)
    original_keys = _file_hashes(runtime.run_dir / "keys")
    original_assets = _file_hashes(runtime.run_dir / "assets")
    work_root = tmp_path / "crash-work"
    work_root.mkdir(mode=0o700)
    backup_root = runtime.run_dir / "backups"
    original = create_backup(
        inventory, backup_root=backup_root, backup_id="before-crash", work_root=work_root
    )
    assert original.state is PublicationState.COMPLETE
    original_hashes = _file_hashes(original.location)

    killed = _kill_paused_migration(
        runtime, prepared.inventory_path, backup_root, work_root, fault_point
    )
    (tmp_path / "crash-process.json").write_text(json.dumps(killed, indent=2) + "\n")
    _assert_recovered_legacy(database, original_rows)
    with hold_existing_maintenance_lock(
        inventory.maintenance_lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
        expected_device=inventory.maintenance_lock_identity.device,
        expected_inode=inventory.maintenance_lock_identity.inode,
    ) as held:
        held.verify_identity()
    assert verify_backup(
        backup_root=backup_root, backup_id="before-crash", work_root=work_root
    ).identity_complete
    assert _file_hashes(original.location) == original_hashes

    # A crash can change raw WAL bytes. Capture a new exact source binding, not an implicit retry.
    fresh = create_backup(
        inventory, backup_root=backup_root, backup_id="after-crash", work_root=work_root
    )
    assert fresh.state is PublicationState.COMPLETE
    _resume_migration(runtime, prepared.inventory_path, backup_root, work_root)
    _assert_v1_database(database, original_rows, fresh, "after-crash")

    v2_backup, v2_backup_hashes = _upgrade_v1_to_v2(
        runtime,
        inventory,
        prepared.inventory_path,
        database,
        original_rows,
        fresh,
        "after-crash",
        backup_root,
        work_root,
    )
    with contextlib.closing(
        sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        assert classify_schema(connection).state is SchemaState.CURRENT_V2
        assert _logical_rows(connection) == original_rows
        assert audit_money(connection).financially_clean
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
    assert _file_hashes(original.location) == original_hashes
    _assert_upgrade_backups_unchanged(
        v2_backup,
        v2_backup_hashes,
        v3_backup,
        v3_backup_hashes,
        v4_backup,
        v4_backup_hashes,
    )
    assert _file_hashes(v5_backup.location) == v5_backup_hashes
    assert _file_hashes(runtime.run_dir / "keys") == original_keys
    assert _file_hashes(runtime.run_dir / "assets") == original_assets
