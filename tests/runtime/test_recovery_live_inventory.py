"""The actual seven-service runtime can produce an offline verified backup."""

from pathlib import Path

import httpx
import pytest
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from scripts.recovery_inventory import build_recovery_inventory
from scripts.recovery_registry import capture_registry_bindings
from service_commons.maintenance_lock import MaintenanceLockMode, hold_maintenance_lock
from service_commons.recovery_contract import MaintenanceLockIdentityV1, OwnedProcessV1

from db_gateway_service.recovery import PublicationState, create_backup, verify_backup

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_live_full_runtime_inventory_produces_identity_complete_backup(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    try:
        runtime.start()
        assert runtime.provision() == 0
        with httpx.Client(
            base_url=runtime.service_url("identity"),
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            registry = capture_registry_bindings(client, max_attempts=2)
        with hold_maintenance_lock(
            runtime.run_dir / "maintenance.lock",
            mode=MaintenanceLockMode.SHARED,
            blocking=False,
        ) as held:
            lock_identity = MaintenanceLockIdentityV1(device=held.device, inode=held.inode)
    finally:
        runtime.stop()

    processes = tuple(
        OwnedProcessV1(
            logical_id=service,
            config_id=service,
            process_cwd=REPO_ROOT / "services" / service,
            agents_package_root=None,
            **runtime.process_records[service],
        )
        for service in SERVICE_ORDER
    )
    inventory = build_recovery_inventory(
        runtime_id=runtime.run_id,
        source_root=runtime.run_dir,
        code_root=REPO_ROOT,
        config_paths={**runtime.config_paths, "agents": runtime.agent_config_path},
        process_cwds={
            **{service: REPO_ROOT / "services" / service for service in SERVICE_ORDER},
            "agents": REPO_ROOT / "agents",
        },
        agents_package_root=REPO_ROOT / "agents",
        maintenance_lock_identity=lock_identity,
        registry_bindings=registry,
        required_registered_handles=frozenset({"platform", "operator"}),
        process_ownership=processes,
    )
    assert len(inventory.configs) == 8
    assert len(inventory.field_bindings) == 60
    assert all(process.group_drained and not process.running for process in processes)

    work_root = tmp_path / "recovery-work"
    work_root.mkdir(mode=0o700)
    result = create_backup(
        inventory,
        backup_root=runtime.run_dir / "backups",
        backup_id="live-runtime",
        work_root=work_root,
    )
    assert result.state is PublicationState.COMPLETE
    verified = verify_backup(
        backup_root=runtime.run_dir / "backups",
        backup_id="live-runtime",
        work_root=work_root,
    )
    assert verified.artifact_verified is True
    assert verified.snapshot_preserved is True
    assert verified.identity_complete is True
    assert verified.operational_restore_verified is False
