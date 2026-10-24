"""A public-API workload survives artifact restore with original runtime files absent."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from scripts.recovery_adoption import adopt_restored_check_only
from scripts.recovery_targets import build_target_bindings
from service_auth import AgentFactory, TaskBoardCommandResponse
from service_auth.financial_identity import operation_id_for
from service_commons.recovery_contract import BindingKind, RecoveryInventoryV1

if TYPE_CHECKING:
    from service_commons.recovery_contract import TargetBindingsV1

    from db_gateway_service.recovery import VerificationResult

from db_gateway_service.recovery import (
    PublicationState,
    create_backup,
    restore_backup,
    verify_backup,
    verify_restore,
)

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]
DELIVERABLE = b"Recovery acceptance: the submitted answer is forty-two.\n"
ACCEPTANCE_WINDOW_SECONDS = 1800


def _task_version(task: dict[str, object], *, expected_status: str) -> int:
    assert task["status"] == expected_status
    lifecycle_version = task["lifecycle_version"]
    assert type(lifecycle_version) is int
    return lifecycle_version


def _command_body(
    response: TaskBoardCommandResponse, *, expected_status_code: int
) -> dict[str, object]:
    assert isinstance(response, TaskBoardCommandResponse)
    assert response.status_code == expected_status_code
    assert isinstance(response.body, dict)
    return response.body


async def _public_workload(config_path: Path) -> dict[str, object]:
    factory = AgentFactory(config_path=config_path)
    poster = factory.user_agent("operator")
    worker = factory.create_agent("alice")
    try:
        await poster.register()
        await worker.register()
        assert poster.agent_id is not None
        assert worker.agent_id is not None
        await worker.create_account()
        posting = poster.prepare_task_posting(
            title="Recover submitted work",
            spec="Upload result.txt containing the answer.",
            reward=100,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=3600,
            review_deadline_seconds=3600,
            acceptance_window_seconds=ACCEPTANCE_WINDOW_SECONDS,
        )
        publication = await poster.submit_task_posting(posting)
        assert publication.status_code == 201
        assert publication.body["publication_state"] == "published"
        assert publication.body["operation_id"] == posting.operation_id
        assert publication.body["request_sha256"] == posting.request_sha256
        task = publication.body["result"]
        assert isinstance(task, dict)
        assert task["task_id"] == posting.task_id
        task_id = task["task_id"]
        assert isinstance(task_id, str)
        open_version = _task_version(task, expected_status="open")
        bid = _command_body(
            await worker.submit_bid(
                task_id=task_id,
                amount=90,
                operation_id=operation_id_for("runtime-public-bid", (task_id, worker.agent_id)),
                expected_lifecycle_version=open_version,
            ),
            expected_status_code=201,
        )
        bid_id = bid["bid_id"]
        assert isinstance(bid_id, str)
        accepted = _command_body(
            await poster.accept_bid(
                task_id=task_id,
                bid_id=bid_id,
                operation_id=operation_id_for("runtime-public-accept", (task_id, bid_id)),
                expected_lifecycle_version=open_version,
            ),
            expected_status_code=200,
        )
        accepted_version = _task_version(accepted, expected_status="accepted")
        asset = await worker.upload_asset(task_id, "result.txt", DELIVERABLE)
        submitted = _command_body(
            await worker.submit_deliverable(
                task_id,
                operation_id=operation_id_for("runtime-public-submit", (task_id, worker.agent_id)),
                expected_lifecycle_version=accepted_version,
            ),
            expected_status_code=200,
        )
        _task_version(submitted, expected_status="submitted")
        balance = await poster.get_balance()
        return {
            "poster_id": poster.agent_id,
            "worker_id": worker.agent_id,
            "task_id": task_id,
            "asset_id": asset["asset_id"],
            "balance": balance["balance"],
        }
    finally:
        await worker.close()
        await poster.close()


def _endpoints(inventory: RecoveryInventoryV1, runtime: IsolatedRuntime):
    ports = {service: 30001 + index for index, service in enumerate(SERVICE_ORDER)}
    replacements = {
        runtime.service_url(service): f"http://127.0.0.1:{port}" for service, port in ports.items()
    }
    result = {}
    for binding in inventory.field_bindings:
        if binding.binding_kind is BindingKind.FILESYSTEM:
            continue
        if binding.binding_kind is BindingKind.PORT:
            value = ports[binding.config_id]
        elif binding.binding_kind is BindingKind.ORIGINS:
            value = (f"http://127.0.0.1:{ports['ui']}", f"http://localhost:{ports['ui']}")
        else:
            value = replacements.get(binding.captured_value, binding.captured_value)
        result[binding.binding_id] = value
    return result


def _assert_database_receipts(
    database: Path, work_root: Path, receipt: dict[str, object]
) -> tuple[str, int]:
    clone = work_root / "proof.db"
    shutil.copyfile(database, clone)
    wal = Path(f"{database}-wal")
    if wal.exists():
        shutil.copyfile(wal, Path(f"{clone}-wal"))
    connection = sqlite3.connect(clone)
    try:
        assert connection.execute(
            "SELECT balance FROM bank_accounts WHERE account_id=?", (receipt["poster_id"],)
        ).fetchone() == (receipt["balance"],)
        assert connection.execute(
            "SELECT status, poster_id, worker_id FROM board_tasks WHERE task_id=?",
            (receipt["task_id"],),
        ).fetchone() == ("submitted", receipt["poster_id"], receipt["worker_id"])
        assert connection.execute(
            "SELECT amount, status FROM bank_escrow WHERE task_id=?", (receipt["task_id"],)
        ).fetchone() == (100, "locked")
        asset_path, size = connection.execute(
            "SELECT storage_path, size_bytes FROM board_assets WHERE asset_id=?",
            (receipt["asset_id"],),
        ).fetchone()
        return asset_path, size
    finally:
        connection.close()


def _target_snapshot(root: Path) -> dict[str, tuple[int, int, int, int, int, str | None]]:
    snapshot = {}
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.stat(follow_symlinks=False)
        digest = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if stat.S_ISREG(metadata.st_mode)
            else None
        )
        snapshot[path.relative_to(root).as_posix()] = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            digest,
        )
    return snapshot


def _assert_readonly_adoption(
    target: Path,
    work_root: Path,
    inventory: RecoveryInventoryV1,
    bindings: TargetBindingsV1,
    verified: VerificationResult,
) -> None:
    before_adoption = _target_snapshot(target)
    view = adopt_restored_check_only(repo_root=REPO_ROOT, target_root=target, work_root=work_root)
    assert view.result == verified
    assert view.target_bindings == bindings
    assert view.source_inventory == inventory
    assert set(view.config_paths) == {*SERVICE_ORDER, "agents"}
    assert not any(hasattr(view, operation) for operation in ("start", "provision", "run_command"))
    assert _target_snapshot(target) == before_adoption


def test_full_runtime_artifact_restore_preserves_submitted_work_without_source_files(
    tmp_path: Path,
) -> None:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    try:
        runtime.start()
        assert runtime.provision() == 0
        receipt = asyncio.run(_public_workload(runtime.agent_config_path))
        prepared = runtime.prepare_recovery_and_stop(
            registry_timeout_seconds=10.0,
            registry_max_attempts=2,
        )
    finally:
        runtime.stop()
    inventory = RecoveryInventoryV1.model_validate_json(prepared.inventory_path.read_bytes())
    assert all(
        not process.running and process.group_drained for process in inventory.process_ownership
    )
    assert any(
        pair.handle == "alice" and pair.expected_registered_agent_id == receipt["worker_id"]
        for pair in inventory.keypairs
    )

    work_root = tmp_path / "verification-work"
    work_root.mkdir(mode=0o700)
    backup_root = runtime.run_dir / "backups"
    backup = create_backup(
        inventory, backup_root=backup_root, backup_id="submitted-work", work_root=work_root
    )
    assert backup.state is PublicationState.COMPLETE
    source_database_hash = hashlib.sha256(
        (runtime.run_dir / "data" / "economy.db").read_bytes()
    ).hexdigest()
    for directory in ("data", "assets", "keys", "configs"):
        (runtime.run_dir / directory).rename(runtime.run_dir / f"unavailable-{directory}")
        assert not (runtime.run_dir / directory).exists()
    assert verify_backup(
        backup_root=backup_root, backup_id="submitted-work", work_root=work_root
    ).identity_complete

    target = tmp_path / "restored"
    bindings = build_target_bindings(
        inventory,
        target_root=target,
        process_cwds={config.logical_id: config.process_cwd for config in inventory.configs},
        agents_package_root=REPO_ROOT / "agents",
        endpoint_values=_endpoints(inventory, runtime),
    )
    restored = restore_backup(
        backup_root=backup_root,
        backup_id="submitted-work",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    assert restored.state is PublicationState.COMPLETE
    verified = verify_restore(target_root=target, work_root=work_root)
    assert verified.artifact_verified and verified.snapshot_preserved and verified.identity_complete
    assert verified.operational_restore_verified is False
    assert "unknown_schema" in verified.finding_codes
    _assert_readonly_adoption(target, work_root, inventory, bindings, verified)
    database = bindings.resource_paths["database.primary"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == source_database_hash

    asset_path, size = _assert_database_receipts(database, work_root, receipt)
    assert size == len(DELIVERABLE)
    assert not Path(asset_path).is_absolute()
    target_asset = bindings.resource_paths["assets.root"] / asset_path
    assert target_asset.read_bytes() == DELIVERABLE
    assert not (runtime.run_dir / "assets" / asset_path).exists()
    assert not bindings.resource_paths["agents.tasks"].exists()
    assert not bindings.resource_paths["database.legacy.task-board"].exists()
    locks = set()
    for service in ("db-gateway", "task-board", "ui"):
        config = yaml.safe_load(bindings.resource_paths[f"config.{service}"].read_bytes())
        locks.add(config["recovery"]["maintenance_lock_path"])
    assert locks == {str(bindings.resource_paths["runtime.maintenance_lock"])}
