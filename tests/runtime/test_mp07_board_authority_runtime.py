"""MP-07 runtime ownership and recovery proofs for Task Board authority."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
import yaml
from scripts import isolated_test_runtime, recovery_fields
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from scripts.recovery_inventory import build_recovery_inventory
from scripts.recovery_targets import build_target_bindings
from service_auth import AgentFactory, generate_keypair, load_public_key, public_key_to_b64
from service_commons.recovery_contract import (
    BindingKind,
    CapturedValue,
    MaintenanceLockIdentityV1,
    OwnedProcessV1,
    RecoveryInventoryV1,
)

from db_gateway_service.recovery import (
    PublicationState,
    create_backup,
    restore_backup,
    verify_backup,
)
from db_gateway_service.schema import SchemaState, load_schema_sql

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def _mapping(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _nested(value: dict[str, object], key: str) -> dict[str, object]:
    nested = value[key]
    assert isinstance(nested, dict)
    return nested


def _process_cwds() -> dict[str, Path]:
    return {
        **{service: REPO_ROOT / "services" / service for service in SERVICE_ORDER},
        "agents": REPO_ROOT / "agents",
    }


def _stopped_processes(runtime: IsolatedRuntime) -> tuple[OwnedProcessV1, ...]:
    records = tuple(
        OwnedProcessV1(
            logical_id=f"service:{service}",
            config_id=service,
            process_cwd=REPO_ROOT / "services" / service,
            agents_package_root=None,
            pid=None,
            pgid=None,
            sid=None,
            running=False,
            group_drained=False,
            leader_returncode=None,
            cleanup_error=None,
        )
        for service in SERVICE_ORDER
    )
    return (
        *records,
        OwnedProcessV1(
            logical_id="source-supervisor",
            config_id=None,
            process_cwd=runtime.run_dir,
            agents_package_root=REPO_ROOT / "agents",
            pid=None,
            pgid=None,
            sid=None,
            running=False,
            group_drained=False,
            leader_returncode=None,
            cleanup_error=None,
        ),
    )


def _canonical_public(runtime: IsolatedRuntime, handle: str) -> str:
    public_key = load_public_key(runtime.run_dir / "keys" / f"{handle}.pub")
    return f"ed25519:{public_key_to_b64(public_key)}"


def _provision_inventory_inputs(runtime: IsolatedRuntime) -> None:
    (runtime.run_dir / "maintenance.lock").touch(mode=0o600)
    AgentFactory(config_path=runtime.agent_config_path).provision_configured_keys()
    gateway = _mapping(runtime.gateway_config_path)
    trusted = _nested(_nested(gateway, "auth"), "trusted_callers")
    for handle, caller_value in trusted.items():
        assert isinstance(handle, str)
        assert isinstance(caller_value, dict)
        public_path = caller_value["public_key_path"]
        assert isinstance(public_path, str)
        generate_keypair(handle, Path(public_path).parent)
    database = runtime.run_dir / "data" / "economy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V3))
        connection.executemany(
            "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
            "VALUES (?, ?, ?, ?)",
            (
                ("agent-platform", "Platform", _canonical_public(runtime, "platform"), "now"),
                ("agent-operator", "Operator", _canonical_public(runtime, "operator"), "now"),
            ),
        )


def _inventory(runtime: IsolatedRuntime) -> RecoveryInventoryV1:
    lock = (runtime.run_dir / "maintenance.lock").stat()
    registry = {
        _canonical_public(runtime, "platform"): "agent-platform",
        _canonical_public(runtime, "operator"): "agent-operator",
    }
    return build_recovery_inventory(
        runtime_id=runtime.run_id,
        source_root=runtime.run_dir,
        config_paths={**runtime.config_paths, "agents": runtime.agent_config_path},
        process_cwds=_process_cwds(),
        agents_package_root=REPO_ROOT / "agents",
        code_root=REPO_ROOT,
        maintenance_lock_identity=MaintenanceLockIdentityV1(
            device=lock.st_dev,
            inode=lock.st_ino,
        ),
        registry_bindings=registry,
        required_registered_handles=frozenset({"platform", "operator"}),
        process_ownership=_stopped_processes(runtime),
    )


def _target_endpoints(inventory: RecoveryInventoryV1) -> dict[str, CapturedValue]:
    endpoints: dict[str, CapturedValue] = {}
    next_port = 31000
    for binding in inventory.field_bindings:
        if binding.binding_kind is BindingKind.FILESYSTEM:
            continue
        if binding.binding_kind is BindingKind.PORT:
            endpoints[binding.binding_id] = next_port
        elif binding.binding_kind is BindingKind.ORIGINS:
            endpoints[binding.binding_id] = (f"http://127.0.0.1:{next_port}",)
        else:
            endpoints[binding.binding_id] = f"http://127.0.0.1:{next_port}"
        next_port += 1
    return endpoints


def test_runtime_rebinds_board_authority_to_owned_economic_platform_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_config = REPO_ROOT / "services" / "db-gateway" / "config.yaml"
    source_before = hashlib.sha256(source_config.read_bytes()).hexdigest()
    ordinary_root = tmp_path / "ordinary-runtime"
    ordinary_public_key = ordinary_root / "keys" / "platform.pub"
    real_load = isolated_test_runtime._load_yaml

    def load_with_ordinary_authority(path: Path) -> dict[str, object]:
        config = real_load(path)
        if path == source_config:
            _nested(config, "board_authority")["platform_public_key_path"] = str(
                ordinary_public_key
            )
        return config

    monkeypatch.setattr(isolated_test_runtime, "_load_yaml", load_with_ordinary_authority)
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    try:
        assert not ordinary_root.exists()
        runtime._ensure_keys_provisioned()
        gateway = _mapping(runtime.gateway_config_path)
        task_board = _mapping(runtime.config_paths["task-board"])
        authority = _nested(gateway, "board_authority")
        deadline = _nested(task_board, "deadline_evaluation")
        runtime_platform = runtime.run_dir / "keys" / "platform.pub"
        gateway_task_board = runtime.run_dir / "keys" / "gateway-task-board.pub"

        assert authority["platform_public_key_path"] == str(runtime_platform)
        assert authority["deadline_actor_id"] == deadline["actor_id"]
        assert runtime_platform.read_bytes() != gateway_task_board.read_bytes()
        assert load_public_key(Path(str(authority["platform_public_key_path"]))) == load_public_key(
            runtime_platform
        )
        assert not ordinary_root.exists()
        assert hashlib.sha256(source_config.read_bytes()).hexdigest() == source_before
    finally:
        runtime.stop()
    assert not runtime.processes
    assert all(record["group_drained"] for record in runtime.bootstrap_records)


def test_runtime_rejects_disagreeing_explicit_deadline_actors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_board_source = REPO_ROOT / "services" / "task-board" / "config.yaml"
    real_load = isolated_test_runtime._load_yaml

    def load_with_mismatched_actor(path: Path) -> dict[str, object]:
        config = real_load(path)
        if path == task_board_source:
            _nested(config, "deadline_evaluation")["actor_id"] = "different-deadline-actor"
        return config

    monkeypatch.setattr(isolated_test_runtime, "_load_yaml", load_with_mismatched_actor)
    with pytest.raises(ValueError, match=r"deadline_actor_id must match.*actor_id"):
        IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    assert not any(tmp_path.rglob("*.key"))
    assert not any(tmp_path.rglob("*.pub"))


def test_recovery_classifies_board_authority_as_platform_public_key(
    tmp_path: Path,
) -> None:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    try:
        gateway_path = runtime.gateway_config_path
        fields = recovery_fields.configuration_fields(
            "db-gateway",
            _mapping(gateway_path),
            config_path=gateway_path,
            process_cwd=REPO_ROOT / "services" / "db-gateway",
            agents_package_root=REPO_ROOT / "agents",
        )
        authority = next(
            field
            for field in fields
            if field.field_path == ("board_authority", "platform_public_key_path")
        )
        assert authority.binding_kind is BindingKind.FILESYSTEM
        assert authority.resolved_source_path == runtime.run_dir / "keys" / "platform.pub"
    finally:
        runtime.stop()


def test_recovery_restores_board_authority_to_restored_platform_key(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    try:
        _provision_inventory_inputs(runtime)
        source_platform_bytes = (runtime.run_dir / "keys" / "platform.pub").read_bytes()
        inventory = _inventory(runtime)
        binding = next(
            item
            for item in inventory.field_bindings
            if item.config_id == "db-gateway"
            and item.field_path == ("board_authority", "platform_public_key_path")
        )
        assert binding.resource_id == "key.platform.public"

        work_root = tmp_path / "recovery-work"
        work_root.mkdir(mode=0o700)
        backup = create_backup(
            inventory,
            backup_root=runtime.run_dir / "backups",
            backup_id="board-authority",
            work_root=work_root,
        )
        assert backup.state is PublicationState.COMPLETE
        verified = verify_backup(
            backup_root=runtime.run_dir / "backups",
            backup_id="board-authority",
            work_root=work_root,
        )
        assert (
            verified.artifact_verified,
            verified.snapshot_preserved,
            verified.identity_complete,
        ) == (True, True, True), verified

        target_root = tmp_path / "restored"
        target_bindings = build_target_bindings(
            inventory,
            target_root=target_root,
            process_cwds=_process_cwds(),
            agents_package_root=REPO_ROOT / "agents",
            endpoint_values=_target_endpoints(inventory),
        )
        restored = restore_backup(
            backup_root=runtime.run_dir / "backups",
            backup_id="board-authority",
            target_bindings=target_bindings,
            target_root=target_root,
            work_root=work_root,
        )
        assert restored.state is PublicationState.COMPLETE
        restored_gateway = _mapping(target_bindings.resource_paths["config.db-gateway"])
        restored_authority = _nested(restored_gateway, "board_authority")
        restored_platform = target_bindings.resource_paths["key.platform.public"]
        assert restored_authority["platform_public_key_path"] == str(restored_platform)
        assert restored_platform.read_bytes() == source_platform_bytes
    finally:
        runtime.stop()
