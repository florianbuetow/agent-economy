"""Recovery source facts are anchored at process startup, before later capture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts.isolated_test_runtime import IsolatedRuntime

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_startup_records_exact_configuration_cwd_and_maintenance_identity(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    try:
        runtime.start()
        facts = runtime._startup_inputs["db-gateway"]
        assert facts["config_path"] == str(runtime.config_paths["db-gateway"])
        assert (
            facts["config_sha256"]
            == hashlib.sha256(runtime.config_paths["db-gateway"].read_bytes()).hexdigest()
        )
        assert facts["agent_config_path"] == str(runtime.agent_config_path)
        assert (
            facts["agent_config_sha256"]
            == hashlib.sha256(runtime.agent_config_path.read_bytes()).hexdigest()
        )
        assert facts["process_cwd"] == str(REPO_ROOT / "services" / "db-gateway")
        metadata = (runtime.run_dir / "maintenance.lock").stat()
        assert runtime._maintenance_identity == (metadata.st_dev, metadata.st_ino)
        runtime._validate_recovery_startup_inputs()
        manifest = json.loads((runtime.run_dir / "runtime.json").read_text())
        assert manifest["startup_inputs"]["db-gateway"] == facts
        assert manifest["maintenance_identity"] == {
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
        }
        assert manifest["recovery"] == {"state": "not_prepared"}
    finally:
        runtime.stop()


def test_config_changed_during_startup_cannot_be_recorded_as_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    wait_for_health = runtime._wait_for_health

    def wait_and_change(service, process):
        wait_for_health(service, process)
        path = runtime.config_paths[service]
        path.write_bytes(path.read_bytes() + b"\n# changed after service consumed config\n")

    monkeypatch.setattr(runtime, "_wait_for_health", wait_and_change)
    try:
        with pytest.raises(RuntimeError, match="configuration changed"):
            runtime.start()
        assert runtime.processes == {}
        assert runtime._reservations == {}
    finally:
        runtime.stop()


def test_maintenance_replacement_during_startup_cannot_anchor_a_second_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    wait_for_health = runtime._wait_for_health

    def wait_and_replace(service, process):
        wait_for_health(service, process)
        path = runtime.run_dir / "maintenance.lock"
        path.rename(runtime.run_dir / "old-maintenance.lock")
        path.touch(mode=0o600)

    monkeypatch.setattr(runtime, "_wait_for_health", wait_and_replace)
    try:
        with pytest.raises(RuntimeError, match="maintenance"):
            runtime.start()
        assert runtime.processes == {}
    finally:
        runtime.stop()


def test_owned_phase_command_records_its_actual_working_directory(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    command_cwd = runtime.run_dir / "data"
    try:
        assert runtime.run_command(["/usr/bin/true"], cwd=command_cwd) == 0
        assert runtime.command_records[0]["cwd"] == str(command_cwd)
        assert runtime.command_records[0]["group_drained"] is True
        manifest = json.loads((runtime.run_dir / "runtime.json").read_text())
        assert manifest["commands"][0]["cwd"] == str(command_cwd)
    finally:
        runtime.stop()


def test_caller_command_mutation_cannot_rewrite_execution_evidence(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    command = ["/usr/bin/true"]
    try:
        assert runtime.run_command(command, cwd=runtime.run_dir) == 0
        command[0] = "/usr/bin/false"
        runtime._write_manifest()
        assert runtime.command_records[0]["command"] == ["/usr/bin/true"]
        manifest = json.loads((runtime.run_dir / "runtime.json").read_text())
        assert manifest["commands"][0]["command"] == ["/usr/bin/true"]
    finally:
        runtime.stop()


def test_global_exclusion_does_not_attribute_ownership_to_unstarted_services(
    tmp_path: Path,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    try:
        runtime.start()
        runtime._assert_maintenance_exclusion()
        assert set(runtime._startup_inputs) == {"db-gateway"}
        assert set(runtime.process_records) == {"db-gateway"}
        manifest = json.loads((runtime.run_dir / "runtime.json").read_text())
        assert set(manifest["startup_inputs"]) == {"db-gateway"}
        assert set(manifest["processes"]) == {"db-gateway"}
    finally:
        runtime.stop()


def test_repeated_completed_stop_keeps_published_manifest_unchanged(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    runtime.stop()
    manifest = runtime.run_dir / "runtime.json"
    before = manifest.stat()
    contents = manifest.read_bytes()
    runtime.stop()
    after = manifest.stat()
    assert (after.st_dev, after.st_ino, after.st_mtime_ns) == (
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
    )
    assert manifest.read_bytes() == contents


def test_stop_after_new_command_records_the_new_cleanup(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    runtime.stop()
    assert runtime.run_command(["/usr/bin/true"], cwd=runtime.run_dir) == 0
    runtime.stop()
    manifest = json.loads((runtime.run_dir / "runtime.json").read_text())
    assert len(manifest["commands"]) == 1
    assert manifest["commands"][0]["group_drained"]
    assert not manifest["commands"][0]["running"]
