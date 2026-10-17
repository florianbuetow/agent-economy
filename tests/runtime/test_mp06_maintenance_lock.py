"""MP-06 maintenance-lock bindings in the isolated runtime."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scripts.isolated_test_runtime import IsolatedRuntime
from service_commons.maintenance_lock import (
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_maintenance_lock,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _config(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text())
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    "services",
    [
        ("db-gateway",),
        ("task-board",),
        ("ui",),
        ("db-gateway", "ui"),
        ("db-gateway", "task-board", "ui"),
    ],
)
def test_partial_runtime_configs_share_one_run_owned_maintenance_path(
    tmp_path: Path,
    services: tuple[str, ...],
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=services,
    )
    try:
        expected = str(runtime.run_dir / "maintenance.lock")
        for service in services:
            recovery = _config(runtime.config_paths[service]).get("recovery")
            assert isinstance(recovery, dict)
            assert recovery["maintenance_lock_path"] == expected
        runtime._validate_maintenance_lock_paths()
    finally:
        runtime.stop()


def test_runtime_rejects_three_different_maintenance_paths_before_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = ("db-gateway", "task-board", "ui")
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=services,
    )
    try:
        for service in services:
            config = _config(runtime.config_paths[service])
            recovery = config.get("recovery")
            assert isinstance(recovery, dict)
            recovery["maintenance_lock_path"] = str(runtime.run_dir / f"{service}.lock")
            runtime.config_paths[service].write_text(yaml.safe_dump(config, sort_keys=False))

        monkeypatch.setattr(
            runtime,
            "_ensure_keys_provisioned",
            lambda: pytest.fail("key provisioning ran before maintenance-path validation"),
        )
        with pytest.raises(ValueError, match="must share the run-owned maintenance lock"):
            runtime.start()
        assert runtime.processes == {}
    finally:
        runtime.stop()


def test_live_gateway_holds_runtime_maintenance_lock_shared(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    lock_path = runtime.run_dir / "maintenance.lock"
    try:
        runtime.start()
        with (
            pytest.raises(MaintenanceLockError) as exc_info,
            hold_maintenance_lock(
                lock_path,
                mode=MaintenanceLockMode.EXCLUSIVE,
                blocking=False,
            ),
        ):
            pytest.fail("exclusive maintenance entered while runtime Gateway was active")
        assert exc_info.value.reason == "busy"
    finally:
        runtime.stop()

    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass
