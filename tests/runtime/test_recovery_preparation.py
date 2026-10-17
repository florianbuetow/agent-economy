"""Recovery preparation publishes only a stable, fully drained source inventory."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from service_commons.recovery_contract import RecoveryInventoryV1

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class _Inventory:
    token: str
    process_ownership: tuple[Any, ...]

    def model_copy(self, *, update: dict[str, object]) -> _Inventory:
        token = update.get("token", self.token)
        process_ownership = update.get("process_ownership", self.process_ownership)
        if type(token) is not str or not isinstance(process_ownership, tuple):
            raise TypeError("Invalid controlled inventory update")
        return _Inventory(token=token, process_ownership=process_ownership)

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {
            "token": self.token,
            "process_ownership": [
                process.model_dump(mode="json") for process in self.process_ownership
            ],
        }


@dataclass
class _Process:
    pid: int
    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


def _record(pid: int, *, cwd: Path | None = None) -> dict[str, object]:
    record: dict[str, object] = {
        "pid": pid,
        "pgid": pid,
        "sid": pid,
        "running": True,
        "leader_returncode": None,
        "group_drained": False,
        "cleanup_error": None,
    }
    if cwd is not None:
        record["cwd"] = str(cwd)
    return record


@dataclass
class _Runtime:
    repo_root: Path
    run_dir: Path
    config_paths: dict[str, Path]
    agent_config_path: Path
    _startup_inputs: dict[str, dict[str, str]]
    _maintenance_identity: tuple[int, int]
    run_id: str = "runtime-prepare-test"
    services: tuple[str, ...] = SERVICE_ORDER
    processes: dict[str, _Process] = field(default_factory=dict)
    process_records: dict[str, dict[str, object]] = field(default_factory=dict)
    bootstrap_records: list[dict[str, object]] = field(default_factory=list)
    command_records: list[dict[str, object]] = field(default_factory=list)
    active_command: _Process | None = None
    _recovery_status: dict[str, object] = field(default_factory=lambda: {"state": "not_prepared"})
    validation_calls: int = 0
    stop_calls: int = 0
    manifest_calls: int = 0
    fail_validation_call: int | None = None
    fail_stop: bool = False
    fail_manifest_call: int | None = None

    def service_url(self, service: str) -> str:
        assert service == "identity"
        return "http://identity.test"

    def _validate_recovery_startup_inputs(self) -> None:
        self.validation_calls += 1
        if self.validation_calls == self.fail_validation_call:
            raise ValueError("startup evidence changed")

    def _write_manifest(self) -> None:
        self.manifest_calls += 1
        if self.manifest_calls == self.fail_manifest_call:
            raise OSError("manifest fault")
        (self.run_dir / "runtime.json").write_text(
            json.dumps({"recovery": self._recovery_status}, sort_keys=True)
        )

    def stop(self) -> None:
        self.stop_calls += 1
        if self.active_command is not None:
            self.active_command.returncode = 0
            self.active_command = None
        if self.fail_stop:
            for service in SERVICE_ORDER[:-1]:
                if service in self.processes:
                    self._drain(service)
            raise RuntimeError("final service did not drain")
        for service in SERVICE_ORDER:
            if service in self.processes:
                self._drain(service)

    def _drain(self, service: str) -> None:
        process = self.processes.pop(service)
        process.returncode = 0
        record = self.process_records[service]
        record["running"] = False
        record["leader_returncode"] = 0
        record["group_drained"] = True
        record["cleanup_error"] = None


@pytest.fixture
def runtime(tmp_path: Path) -> _Runtime:
    run_dir = tmp_path / "runtime"
    config_dir = run_dir / "configs"
    config_dir.mkdir(parents=True)
    (run_dir / "maintenance.lock").touch(mode=0o600)
    config_paths: dict[str, Path] = {}
    startup_inputs: dict[str, dict[str, str]] = {}
    for service in SERVICE_ORDER:
        config_path = config_dir / f"{service}.yaml"
        config: dict[str, object] = {"service": {"name": service}}
        if service == "ui":
            config["user_agent"] = {"handle": "operator"}
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        config_paths[service] = config_path
        startup_inputs[service] = {
            "config_path": str(config_path),
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "agent_config_path": str(config_dir / "agents.yaml"),
            "agent_config_sha256": "0" * 64,
            "process_cwd": str(tmp_path / "services" / service),
        }
    agent_config = config_dir / "agents.yaml"
    agent_config.write_text("treasury:\n  handle: platform\n")
    lock_stat = (run_dir / "maintenance.lock").stat()
    value = _Runtime(
        repo_root=tmp_path,
        run_dir=run_dir,
        config_paths=config_paths,
        agent_config_path=agent_config,
        _startup_inputs=startup_inputs,
        _maintenance_identity=(lock_stat.st_dev, lock_stat.st_ino),
    )
    for index, service in enumerate(SERVICE_ORDER, start=1001):
        value.processes[service] = _Process(index)
        value.process_records[service] = _record(index)
    value.bootstrap_records.append(_record(2001, cwd=tmp_path / "agents"))
    value.bootstrap_records[0].update(
        running=False,
        leader_returncode=0,
        group_drained=True,
    )
    return value


def _install_controlled_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    *,
    tokens: list[str] | None = None,
    registries: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    inventory_tokens = iter(tokens or ["same", "same", "same"])
    registry_values = list(registries or [{"key": "agent"}, {"key": "agent"}])
    registry_calls: list[dict[str, str]] = []
    build_calls: list[dict[str, object]] = []

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["base_url"] == "http://identity.test"
            assert kwargs["timeout"] == 2.5
            assert kwargs["follow_redirects"] is False

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def capture(_client: object, *, max_attempts: int) -> dict[str, str]:
        assert max_attempts == 3
        value = registry_values[len(registry_calls)]
        registry_calls.append(value)
        return value

    def build(**kwargs: object) -> _Inventory:
        build_calls.append(kwargs)
        ownership = kwargs["process_ownership"]
        assert isinstance(ownership, tuple)
        return _Inventory(next(inventory_tokens), ownership)

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module, "capture_registry_bindings", capture)
    monkeypatch.setattr(module, "build_recovery_inventory", build)
    monkeypatch.setattr(module, "RecoveryInventoryV1", _Inventory)

    def identity(pid: int) -> int:
        return pid

    monkeypatch.setattr(module.os, "getpgid", identity)
    monkeypatch.setattr(module.os, "getsid", identity)
    return build_calls, registry_calls


def test_recovery_preparation_api_exists() -> None:
    module = importlib.import_module("scripts.recovery_preparation")

    assert callable(module.prepare_recovery_and_stop)
    assert module.PreparedRecovery.__dataclass_params__.frozen is True


def test_success_requires_two_live_views_then_a_matching_stopped_inventory(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    build_calls, registry_calls = _install_controlled_dependencies(monkeypatch, module)

    result = module.prepare_recovery_and_stop(
        runtime,
        registry_timeout_seconds=2.5,
        registry_max_attempts=3,
    )

    assert runtime.stop_calls == 1
    assert runtime.validation_calls >= 2
    assert registry_calls == [{"key": "agent"}, {"key": "agent"}]
    assert len(build_calls) == 3
    assert {call["required_registered_handles"] for call in build_calls} == {
        frozenset({"platform", "operator"})
    }
    live_records = build_calls[0]["process_ownership"]
    stopped_records = build_calls[-1]["process_ownership"]
    assert isinstance(live_records, tuple)
    assert isinstance(stopped_records, tuple)
    live_services = [record for record in live_records if record.config_id is not None]
    assert len(live_services) == len(SERVICE_ORDER)
    assert all(record.running for record in live_services)
    assert all(not record.running and record.group_drained for record in stopped_records)
    assert any(record.logical_id == "key-bootstrap-0" for record in stopped_records)
    assert result.inventory_path == runtime.run_dir / "recovery-inventory.json"
    assert result.runtime_manifest_path == runtime.run_dir / "runtime.json"
    payload = result.inventory_path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == result.inventory_sha256
    assert payload == payload.rstrip(b"\n")
    assert runtime._recovery_status["state"] == "ready"
    assert runtime._recovery_status["inventory_sha256"] == result.inventory_sha256
    assert runtime._recovery_status["inventory"] == json.loads(payload)


def test_drained_nonzero_phase_history_is_preserved_without_blocking_recovery(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    build_calls, _registry_calls = _install_controlled_dependencies(monkeypatch, module)
    failed_phase = _record(2002, cwd=runtime.repo_root / "agents")
    failed_phase.update(
        running=False,
        leader_returncode=7,
        group_drained=True,
    )
    runtime.command_records.append(failed_phase)

    module.prepare_recovery_and_stop(
        runtime,
        registry_timeout_seconds=2.5,
        registry_max_attempts=3,
    )

    final_records = build_calls[-1]["process_ownership"]
    assert isinstance(final_records, tuple)
    phase_record = next(
        record for record in final_records if record.logical_id == "phase-command-0"
    )
    assert phase_record.leader_returncode == 7
    assert phase_record.running is False
    assert phase_record.group_drained is True


@pytest.mark.parametrize(
    "invalid_state",
    [
        {"leader_returncode": None},
        {"group_drained": False},
        {"cleanup_error": "cleanup failed"},
    ],
)
def test_incomplete_or_cleanup_failed_command_history_still_blocks_recovery(
    runtime: _Runtime,
    invalid_state: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")

    def identity(pid: int) -> int:
        return pid

    monkeypatch.setattr(module.os, "getpgid", identity)
    monkeypatch.setattr(module.os, "getsid", identity)
    phase = _record(2002, cwd=runtime.repo_root / "agents")
    phase.update(
        running=False,
        leader_returncode=9,
        group_drained=True,
    )
    phase.update(invalid_state)
    runtime.command_records.append(phase)

    with pytest.raises(module.RecoveryPreparationError) as exc_info:
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=2.5,
            registry_max_attempts=3,
        )

    assert exc_info.value.reason == "process_not_drained"
    assert runtime.stop_calls == 1
    assert not (runtime.run_dir / "recovery-inventory.json").exists()


@pytest.mark.parametrize(
    ("tokens", "validation_failure"),
    [(["first", "second"], None), (["same", "same"], 2)],
)
def test_changed_keys_resources_or_startup_evidence_fail_before_publication_and_still_stop(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
    tokens: list[str],
    validation_failure: int | None,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    _install_controlled_dependencies(monkeypatch, module, tokens=tokens)
    runtime.fail_validation_call = validation_failure

    with pytest.raises((RuntimeError, ValueError)):
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=2.5,
            registry_max_attempts=3,
        )

    assert runtime.stop_calls == 1
    assert not (runtime.run_dir / "recovery-inventory.json").exists()
    assert runtime._recovery_status["state"] == "failed"


def test_partial_runtime_fails_closed_but_cleanup(
    runtime: _Runtime,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    runtime.services = SERVICE_ORDER[:-1]

    with pytest.raises(ValueError, match="full seven-service"):
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=2.5,
            registry_max_attempts=3,
        )

    assert runtime.stop_calls == 1
    assert not (runtime.run_dir / "recovery-inventory.json").exists()


@pytest.mark.parametrize(
    ("timeout", "attempts"),
    [(float("inf"), 3), (2.5, 0), (True, 3), (2.5, True)],
)
def test_invalid_bounds_still_cleanup(
    runtime: _Runtime,
    timeout: object,
    attempts: object,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")

    with pytest.raises(ValueError):
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=timeout,
            registry_max_attempts=attempts,
        )

    assert runtime.stop_calls == 1
    assert not (runtime.run_dir / "recovery-inventory.json").exists()


def test_active_command_fails_and_is_cleanup_drained(runtime: _Runtime) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    runtime.active_command = _Process(3001)

    with pytest.raises(module.RecoveryPreparationError) as exc_info:
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=2.5,
            registry_max_attempts=3,
        )

    assert exc_info.value.reason == "active_command"
    assert runtime.stop_calls == 1
    assert not (runtime.run_dir / "recovery-inventory.json").exists()


def test_drain_failure_is_reported_without_publishing(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    _install_controlled_dependencies(monkeypatch, module)
    runtime.fail_stop = True

    with pytest.raises(RuntimeError, match="final service did not drain"):
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=2.5,
            registry_max_attempts=3,
        )

    assert not (runtime.run_dir / "recovery-inventory.json").exists()
    assert runtime._recovery_status["state"] == "failed"


def test_manifest_failure_after_inventory_publication_is_uncertain_and_preserves_exact_bytes(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    _install_controlled_dependencies(monkeypatch, module)
    runtime.fail_manifest_call = 2

    with pytest.raises(OSError, match="manifest fault"):
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=2.5,
            registry_max_attempts=3,
        )

    inventory_path = runtime.run_dir / "recovery-inventory.json"
    payload = inventory_path.read_bytes()
    assert runtime._recovery_status == {
        "state": "publication_uncertain",
        "inventory_path": str(inventory_path),
        "inventory_sha256": hashlib.sha256(payload).hexdigest(),
    }
    original_status = dict(runtime._recovery_status)
    runtime.fail_manifest_call = None

    with pytest.raises(ValueError, match="finite positive"):
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=float("inf"),
            registry_max_attempts=3,
        )

    assert inventory_path.read_bytes() == payload
    assert runtime._recovery_status == original_status


def test_repeated_preparation_preserves_existing_ready_inventory_and_metadata(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    _install_controlled_dependencies(monkeypatch, module)
    prepared = module.prepare_recovery_and_stop(
        runtime,
        registry_timeout_seconds=2.5,
        registry_max_attempts=3,
    )
    original_bytes = prepared.inventory_path.read_bytes()
    original_status = json.loads(json.dumps(runtime._recovery_status))

    with pytest.raises(module.RecoveryPreparationError) as exc_info:
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=2.5,
            registry_max_attempts=3,
        )

    assert exc_info.value.reason == "inventory_exists"
    assert runtime.stop_calls == 2
    assert prepared.inventory_path.read_bytes() == original_bytes
    assert runtime._recovery_status == original_status

    with pytest.raises(ValueError, match="finite positive"):
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=float("inf"),
            registry_max_attempts=3,
        )

    assert runtime.stop_calls == 3
    assert prepared.inventory_path.read_bytes() == original_bytes
    assert runtime._recovery_status == original_status


def test_stale_inventory_without_published_status_records_failed_attempt(
    runtime: _Runtime,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    inventory_path = runtime.run_dir / "recovery-inventory.json"
    inventory_path.write_bytes(b"stale")

    with pytest.raises(module.RecoveryPreparationError) as exc_info:
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=2.5,
            registry_max_attempts=3,
        )

    assert exc_info.value.reason == "inventory_exists"
    assert inventory_path.read_bytes() == b"stale"
    assert runtime._recovery_status == {
        "state": "failed",
        "error_type": "RecoveryPreparationError",
    }


def test_capture_failure_remains_primary_when_cleanup_also_fails(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    _install_controlled_dependencies(monkeypatch, module)

    def fail_capture(**_kwargs: object) -> object:
        raise ValueError("capture primary")

    monkeypatch.setattr(module, "build_recovery_inventory", fail_capture)
    runtime.fail_stop = True

    with pytest.raises(ValueError, match="capture primary") as exc_info:
        module.prepare_recovery_and_stop(
            runtime,
            registry_timeout_seconds=2.5,
            registry_max_attempts=3,
        )

    assert any("cleanup also failed" in note for note in exc_info.value.__notes__)
    assert not (runtime.run_dir / "recovery-inventory.json").exists()


def test_inventory_publication_rejects_a_symlinked_run_directory_ancestor(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    actual_parent = tmp_path / "actual-parent"
    run_dir = actual_parent / "runtime"
    run_dir.mkdir(parents=True)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(actual_parent, target_is_directory=True)

    with pytest.raises(module.RecoveryPreparationError) as exc_info:
        module._publish_inventory(linked_parent / "runtime", b"{}")

    assert exc_info.value.reason == "unsafe_run_directory"
    assert not (run_dir / "recovery-inventory.json").exists()


def test_inventory_publication_forces_exact_private_mode_under_restrictive_umask(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    run_dir = tmp_path / "runtime"
    run_dir.mkdir()
    previous_umask = os.umask(0o777)
    try:
        inventory_path = module._publish_inventory(run_dir, b"{}")
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(inventory_path.stat().st_mode) == 0o600


def test_run_directory_fsync_preserves_primary_when_descriptor_close_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.recovery_preparation")
    run_dir = tmp_path / "runtime"
    run_dir.mkdir()
    real_close = module.os.close

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("fsync primary")

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError("close secondary")

    monkeypatch.setattr(module.os, "fsync", fail_fsync)
    monkeypatch.setattr(module.os, "close", close_then_fail)

    with pytest.raises(OSError, match="fsync primary") as exc_info:
        module._fsync_run_directory(run_dir)

    assert any("close" in note for note in exc_info.value.__notes__)


def test_live_full_runtime_prepares_canonical_stopped_inventory(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=Path(__file__).resolve().parents[2], artifacts_root=tmp_path
    )
    try:
        runtime.start()
        assert runtime.provision() == 0

        result = runtime.prepare_recovery_and_stop(
            registry_timeout_seconds=10.0,
            registry_max_attempts=3,
        )
    finally:
        if runtime.processes or runtime.active_command is not None:
            runtime.stop()

    payload = result.inventory_path.read_bytes()
    inventory = RecoveryInventoryV1.model_validate_json(payload)
    assert hashlib.sha256(payload).hexdigest() == result.inventory_sha256
    assert len(inventory.configs) == 8
    assert {record.logical_id for record in inventory.process_ownership} >= set(SERVICE_ORDER)
    assert all(
        not record.running and record.group_drained and record.cleanup_error is None
        for record in inventory.process_ownership
    )
    runtime_manifest = json.loads(result.runtime_manifest_path.read_text())
    assert runtime_manifest["recovery"]["state"] == "ready"
    assert runtime_manifest["recovery"]["inventory_sha256"] == result.inventory_sha256
