"""Recovery inventories are derived from every generated runtime configuration."""

from __future__ import annotations

import base64
import hashlib
import importlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import yaml
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from service_auth import AgentFactory, generate_keypair, load_public_key, public_key_to_b64
from service_commons.recovery_contract import (
    CapturedRegistrationState,
    CaptureKind,
    KeypairRole,
    MaintenanceLockIdentityV1,
    OwnedProcessV1,
    RecoveryInventoryV1,
    RegistrationRequirement,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_recovery_inventory_builder_api_exists() -> None:
    module = importlib.import_module("scripts.recovery_inventory")
    assert callable(module.build_recovery_inventory)
    assert callable(module.read_recovery_source_file)


def test_source_reader_pins_ancestor_chain_during_directory_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("scripts.recovery_inventory")
    source_root = tmp_path / "source"
    attacker_root = tmp_path / "attacker"
    parked_root = tmp_path / "parked-source"
    source_root.mkdir()
    attacker_root.mkdir()
    selected = source_root / "selected.yaml"
    selected.write_bytes(b"trusted: source\n")
    (attacker_root / selected.name).write_bytes(b"trusted: attacker\n")
    real_open = os.open
    retargeted = False

    def racing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal retargeted
        path_text = os.fsdecode(path)
        final_absolute_open = dir_fd is None and Path(path_text) == selected
        final_relative_open = dir_fd is not None and path_text == selected.name
        if not retargeted and (final_absolute_open or final_relative_open):
            source_root.rename(parked_root)
            source_root.symlink_to(attacker_root, target_is_directory=True)
            retargeted = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", racing_open)
    try:
        assert module.read_recovery_source_file(selected, label="selected config") == (
            b"trusted: source\n"
        )
        assert retargeted is True
    finally:
        if source_root.is_symlink():
            source_root.unlink()
        if parked_root.exists():
            parked_root.rename(source_root)


def _mapping(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _write_mapping(path: Path, value: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def _nested_mapping(value: dict[str, object], key: str) -> dict[str, object]:
    nested = value[key]
    assert isinstance(nested, dict)
    return nested


@pytest.fixture
def configured_runtime(tmp_path: Path) -> Iterator[IsolatedRuntime]:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    agent_config = _mapping(runtime.agent_config_path)
    tasks_path = runtime.run_dir / "data" / "tasks.jsonl"
    _nested_mapping(agent_config, "task_feeder")["tasks_file"] = str(tasks_path)
    _write_mapping(runtime.agent_config_path, agent_config)

    (runtime.run_dir / "data" / "economy.db").write_bytes(b"disposable-primary-db")
    (runtime.run_dir / "maintenance.lock").touch(mode=0o600)
    AgentFactory(config_path=runtime.agent_config_path).provision_configured_keys()
    gateway_config = _mapping(runtime.gateway_config_path)
    auth = _nested_mapping(gateway_config, "auth")
    trusted_callers = _nested_mapping(auth, "trusted_callers")
    for handle, caller_value in trusted_callers.items():
        assert isinstance(caller_value, dict)
        public_path_value = caller_value["public_key_path"]
        assert isinstance(public_path_value, str)
        generate_keypair(handle, Path(public_path_value).parent)
    try:
        yield runtime
    finally:
        runtime.stop()


def _config_paths(runtime: IsolatedRuntime) -> dict[str, Path]:
    return {**runtime.config_paths, "agents": runtime.agent_config_path}


def _process_cwds() -> dict[str, Path]:
    return {
        **{service: REPO_ROOT / "services" / service for service in SERVICE_ORDER},
        "agents": REPO_ROOT / "agents",
    }


def _canonical_public_key(runtime: IsolatedRuntime, handle: str) -> str:
    public_key = load_public_key(runtime.run_dir / "keys" / f"{handle}.pub")
    return f"ed25519:{public_key_to_b64(public_key)}"


def _registry(runtime: IsolatedRuntime, handles: tuple[str, ...]) -> dict[str, str]:
    return {
        _canonical_public_key(runtime, handle): f"agent-{index}"
        for index, handle in enumerate(handles, start=1)
    }


def _lock_identity(runtime: IsolatedRuntime) -> MaintenanceLockIdentityV1:
    lock_stat = (runtime.run_dir / "maintenance.lock").stat()
    return MaintenanceLockIdentityV1(device=lock_stat.st_dev, inode=lock_stat.st_ino)


def _process_ownership(runtime: IsolatedRuntime) -> tuple[OwnedProcessV1, ...]:
    service_records = tuple(
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
        *service_records,
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


def _build(
    runtime: IsolatedRuntime,
    *,
    registry: dict[str, str] | None = None,
    required: frozenset[str] = frozenset({"platform", "operator"}),
    process_ownership: tuple[OwnedProcessV1, ...] | None = None,
) -> RecoveryInventoryV1:
    module = importlib.import_module("scripts.recovery_inventory")
    return cast(
        "RecoveryInventoryV1",
        module.build_recovery_inventory(
            runtime_id=runtime.run_id,
            source_root=runtime.run_dir,
            config_paths=_config_paths(runtime),
            process_cwds=_process_cwds(),
            agents_package_root=REPO_ROOT / "agents",
            code_root=REPO_ROOT,
            maintenance_lock_identity=_lock_identity(runtime),
            registry_bindings=registry
            if registry is not None
            else _registry(runtime, ("platform", "operator", "alice")),
            required_registered_handles=required,
            process_ownership=process_ownership
            if process_ownership is not None
            else _process_ownership(runtime),
        ),
    )


def test_generated_full_runtime_builds_exhaustive_deduplicated_inventory(
    configured_runtime: IsolatedRuntime,
) -> None:
    inventory = _build(configured_runtime)
    resources = {resource.logical_id: resource for resource in inventory.resources}
    bindings = {
        (binding.config_id, binding.field_path): binding for binding in inventory.field_bindings
    }
    keypairs = {keypair.handle: keypair for keypair in inventory.keypairs}

    assert len(inventory.configs) == 8
    assert len(inventory.field_bindings) == 62
    assert len(bindings) == len(inventory.field_bindings)
    assert inventory.maintenance_lock_path == configured_runtime.run_dir / "maintenance.lock"
    assert resources["database.primary"].capture_kind is CaptureKind.COPY
    assert resources["database.schema"].captured_path == (
        REPO_ROOT / "docs" / "specifications" / "schema.sql"
    )
    assert resources["assets.root"].capture_kind is CaptureKind.COPY
    assert resources["frontend.web"].capture_kind is CaptureKind.COPY
    assert resources["runtime.maintenance_lock"].capture_kind is CaptureKind.RUNTIME_LOCK
    assert resources["logs.root"].capture_kind is CaptureKind.DIRECTORY_ONLY
    assert resources["recovery.backups"].capture_kind is CaptureKind.DIRECTORY_ONLY
    assert resources["keys.root"].capture_kind is CaptureKind.DIRECTORY_ONLY
    assert resources["database.legacy.task-board"].capture_kind is CaptureKind.ABSENT_FILE
    assert resources["agents.tasks"].capture_kind is CaptureKind.ABSENT_FILE
    assert resources["agents.runtime_state"].capture_kind is CaptureKind.ABSENT_DIRECTORY
    assert resources["agents.runtime_state"].captured_path == (
        configured_runtime.run_dir / "data" / "agent-runtime"
    )
    assert (
        bindings[("agents", ("runtime_state", "directory"))].resource_id == "agents.runtime_state"
    )
    assert (
        bindings[("db-gateway", ("database", "path"))].resource_id
        == bindings[("ui", ("database", "path"))].resource_id
        == "database.primary"
    )
    assert {
        bindings[(service, ("logging", "directory"))].resource_id for service in SERVICE_ORDER
    } == {"logs.root"}
    assert {
        bindings[(service, ("recovery", "maintenance_lock_path"))].resource_id
        for service in ("db-gateway", "task-board", "ui")
    } == {"runtime.maintenance_lock"}
    assert bindings[("ui", ("user_agent", "agent_config_path"))].resource_id == "config.agents"
    assert keypairs["platform"].registration_requirement is RegistrationRequirement.REQUIRED
    assert keypairs["platform"].captured_registration_state is CapturedRegistrationState.REGISTERED
    assert keypairs["alice"].registration_requirement is RegistrationRequirement.OPTIONAL
    assert keypairs["alice"].captured_registration_state is CapturedRegistrationState.REGISTERED
    assert keypairs["bob"].captured_registration_state is CapturedRegistrationState.UNREGISTERED
    assert keypairs["gateway-identity"].role is KeypairRole.GATEWAY
    assert (
        keypairs["gateway-identity"].registration_requirement is RegistrationRequirement.FORBIDDEN
    )
    assert len(keypairs) == 13
    assert {record.config_id for record in inventory.process_ownership} == {
        *SERVICE_ORDER,
        None,
    }
    assert inventory.process_ownership[-1].logical_id == "source-supervisor"
    assert all(
        config.sha256 == hashlib.sha256(config.captured_path.read_bytes()).hexdigest()
        for config in inventory.configs
    )
    assert "BEGIN PRIVATE KEY" not in inventory.model_dump_json()


def test_present_optional_files_are_captured_without_changing_other_semantics(
    configured_runtime: IsolatedRuntime,
) -> None:
    tasks_path = configured_runtime.run_dir / "data" / "tasks.jsonl"
    legacy_path = configured_runtime.run_dir / "data" / "task-board.db"
    tasks_path.write_text('{"task":"disposable"}\n')
    legacy_path.write_bytes(b"disposable-legacy-db")

    resources = {resource.logical_id: resource for resource in _build(configured_runtime).resources}

    assert resources["agents.tasks"].capture_kind is CaptureKind.COPY
    assert resources["database.legacy.task-board"].capture_kind is CaptureKind.COPY


def test_distinct_service_log_directories_get_stable_per_config_resources(
    configured_runtime: IsolatedRuntime,
) -> None:
    for service in SERVICE_ORDER:
        log_root = configured_runtime.run_dir / "logs" / service
        log_root.mkdir()
        config = _mapping(configured_runtime.config_paths[service])
        _nested_mapping(config, "logging")["directory"] = str(log_root)
        _write_mapping(configured_runtime.config_paths[service], config)

    inventory = _build(configured_runtime)
    bindings = {
        (binding.config_id, binding.field_path): binding for binding in inventory.field_bindings
    }
    expected = {
        "central-bank": "logs.root",
        "court": "logs.court",
        "db-gateway": "logs.db-gateway",
        "identity": "logs.identity",
        "reputation": "logs.reputation",
        "task-board": "logs.task-board",
        "ui": "logs.ui",
    }

    assert {
        service: bindings[(service, ("logging", "directory"))].resource_id
        for service in SERVICE_ORDER
    } == expected
    resources = {resource.logical_id: resource for resource in inventory.resources}
    assert {
        logical_id: resources[logical_id].captured_path for logical_id in expected.values()
    } == {
        logical_id: configured_runtime.run_dir / "logs" / service
        for service, logical_id in expected.items()
    }


def test_identity_policy_rejects_missing_required_and_registered_gateway_keys(
    configured_runtime: IsolatedRuntime,
) -> None:
    module = importlib.import_module("scripts.recovery_inventory")
    with pytest.raises(ValueError, match=r"(?i)required.*operator"):
        _build(configured_runtime, registry=_registry(configured_runtime, ("platform",)))

    gateway_registry = _registry(configured_runtime, ("platform", "operator"))
    gateway_registry[_canonical_public_key(configured_runtime, "gateway-identity")] = (
        "agent-gateway"
    )
    with pytest.raises(ValueError, match=r"Gateway.*gateway-identity"):
        _build(configured_runtime, registry=gateway_registry)

    with pytest.raises(ValueError, match="canonical Ed25519"):
        module.build_recovery_inventory(
            runtime_id=configured_runtime.run_id,
            source_root=configured_runtime.run_dir,
            config_paths=_config_paths(configured_runtime),
            process_cwds=_process_cwds(),
            agents_package_root=REPO_ROOT / "agents",
            code_root=REPO_ROOT,
            maintenance_lock_identity=_lock_identity(configured_runtime),
            registry_bindings={"ed25519:not-canonical": "agent-1"},
            required_registered_handles=frozenset(),
            process_ownership=_process_ownership(configured_runtime),
        )

    with pytest.raises(ValueError, match="canonical Ed25519"):
        module.build_recovery_inventory(
            runtime_id=configured_runtime.run_id,
            source_root=configured_runtime.run_dir,
            config_paths=_config_paths(configured_runtime),
            process_cwds=_process_cwds(),
            agents_package_root=REPO_ROOT / "agents",
            code_root=REPO_ROOT,
            maintenance_lock_identity=_lock_identity(configured_runtime),
            registry_bindings={
                f"ed25519:{base64.b64encode(bytes(32)).decode('ascii')}": "agent-zero"
            },
            required_registered_handles=frozenset(),
            process_ownership=_process_ownership(configured_runtime),
        )

    canonical_key = _canonical_public_key(configured_runtime, "platform")
    for invalid_agent_id in (".", "..", "agent id", "agent/id", "agent\\id", "agent\x00id"):
        with pytest.raises(ValueError, match="registry agent IDs"):
            _build(
                configured_runtime,
                registry={canonical_key: invalid_agent_id},
                required=frozenset(),
            )


def test_every_service_requires_one_matching_process_ownership_record(
    configured_runtime: IsolatedRuntime,
) -> None:
    records = _process_ownership(configured_runtime)
    missing_ui = tuple(record for record in records if record.config_id != "ui")
    with pytest.raises(ValueError, match=r"exactly one.*ui"):
        _build(configured_runtime, process_ownership=missing_ui)

    identity = next(record for record in records if record.config_id == "identity")
    duplicate_identity = identity.model_copy(update={"logical_id": "duplicate-identity"})
    with pytest.raises(ValueError, match=r"exactly one.*identity"):
        _build(configured_runtime, process_ownership=(*records, duplicate_identity))

    mismatched = tuple(
        record.model_copy(update={"process_cwd": configured_runtime.run_dir})
        if record.config_id == "court"
        else record
        for record in records
    )
    with pytest.raises(ValueError, match=r"court.*process_cwd"):
        _build(configured_runtime, process_ownership=mismatched)


def test_shared_resource_disagreement_and_partial_runtime_fail_closed(
    configured_runtime: IsolatedRuntime,
    tmp_path: Path,
) -> None:
    ui_config = _mapping(configured_runtime.config_paths["ui"])
    _nested_mapping(ui_config, "database")["path"] = str(tmp_path / "other.db")
    _write_mapping(configured_runtime.config_paths["ui"], ui_config)
    with pytest.raises(ValueError, match=r"database\.primary"):
        _build(configured_runtime)

    ui_config = _mapping(configured_runtime.config_paths["ui"])
    _nested_mapping(ui_config, "database")["path"] = str(
        configured_runtime.run_dir / "data" / "economy.db"
    )
    _nested_mapping(ui_config, "recovery")["maintenance_lock_path"] = str(tmp_path / "other.lock")
    _write_mapping(configured_runtime.config_paths["ui"], ui_config)
    with pytest.raises(ValueError, match=r"runtime\.maintenance_lock"):
        _build(configured_runtime)

    module = importlib.import_module("scripts.recovery_inventory")
    with pytest.raises(ValueError, match="full seven-service runtime"):
        module.build_recovery_inventory(
            runtime_id=configured_runtime.run_id,
            source_root=configured_runtime.run_dir,
            config_paths={"agents": configured_runtime.agent_config_path},
            process_cwds={"agents": REPO_ROOT / "agents"},
            agents_package_root=REPO_ROOT / "agents",
            code_root=REPO_ROOT,
            maintenance_lock_identity=_lock_identity(configured_runtime),
            registry_bindings={},
            required_registered_handles=frozenset(),
            process_ownership=_process_ownership(configured_runtime),
        )


def test_selected_agent_config_and_safe_input_paths_are_enforced(
    configured_runtime: IsolatedRuntime,
    tmp_path: Path,
) -> None:
    ui_config = _mapping(configured_runtime.config_paths["ui"])
    _nested_mapping(ui_config, "user_agent")["agent_config_path"] = str(
        tmp_path / "different-agents.yaml"
    )
    _write_mapping(configured_runtime.config_paths["ui"], ui_config)
    with pytest.raises(ValueError, match=r"config\.agents"):
        _build(configured_runtime)

    original_agents = configured_runtime.agent_config_path
    symlinked_agents = configured_runtime.run_dir / "configs" / "agents-link.yaml"
    symlinked_agents.symlink_to(original_agents)
    config_paths = _config_paths(configured_runtime)
    config_paths["agents"] = symlinked_agents
    module = importlib.import_module("scripts.recovery_inventory")
    with pytest.raises(ValueError, match="symlink"):
        module.build_recovery_inventory(
            runtime_id=configured_runtime.run_id,
            source_root=configured_runtime.run_dir,
            config_paths=config_paths,
            process_cwds=_process_cwds(),
            agents_package_root=REPO_ROOT / "agents",
            code_root=REPO_ROOT,
            maintenance_lock_identity=_lock_identity(configured_runtime),
            registry_bindings=_registry(configured_runtime, ("platform", "operator")),
            required_registered_handles=frozenset({"platform", "operator"}),
            process_ownership=_process_ownership(configured_runtime),
        )


def test_configured_capture_cannot_escape_declared_source_and_code_roots(
    configured_runtime: IsolatedRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_config = _mapping(configured_runtime.agent_config_path)
    escaped = tmp_path / "outside-source" / "tasks.jsonl"
    escaped.parent.mkdir()
    escaped.write_text('{"task":"outside"}\n')
    _nested_mapping(agent_config, "task_feeder")["tasks_file"] = str(escaped)
    _write_mapping(configured_runtime.agent_config_path, agent_config)
    real_lstat = Path.lstat
    outside_probes: list[Path] = []

    def tracking_lstat(path: Path) -> os.stat_result:
        if path in {escaped.parent, escaped}:
            outside_probes.append(path)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", tracking_lstat)

    with pytest.raises(ValueError, match=r"agents\.tasks.*source_root"):
        _build(configured_runtime)
    assert outside_probes == []
