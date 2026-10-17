"""Pure target bindings preserve the complete captured recovery topology."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from service_auth import AgentFactory, generate_keypair, load_public_key, public_key_to_b64
from service_commons.recovery_contract import (
    BindingKind,
    CapturedValue,
    CaptureKind,
    MaintenanceLockIdentityV1,
    OwnedProcessV1,
    RecoveryInventoryV1,
    ResourceKind,
    SourceConfigV1,
    SourceFieldBindingV1,
    SourceResourceV1,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def _mapping(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _nested_mapping(value: dict[str, object], key: str) -> dict[str, object]:
    nested = value[key]
    assert isinstance(nested, dict)
    return nested


@pytest.fixture
def recovery_inventory(tmp_path: Path) -> Iterator[RecoveryInventoryV1]:
    from scripts.recovery_inventory import build_recovery_inventory

    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    agent_config = _mapping(runtime.agent_config_path)
    _nested_mapping(agent_config, "task_feeder")["tasks_file"] = str(
        runtime.run_dir / "data" / "tasks.jsonl"
    )
    runtime.agent_config_path.write_text(yaml.safe_dump(agent_config, sort_keys=False))
    (runtime.run_dir / "data" / "economy.db").write_bytes(b"disposable-primary-db")
    maintenance_lock = runtime.run_dir / "maintenance.lock"
    maintenance_lock.touch(mode=0o600)
    AgentFactory(config_path=runtime.agent_config_path).provision_configured_keys()

    gateway_config = _mapping(runtime.gateway_config_path)
    trusted_callers = _nested_mapping(_nested_mapping(gateway_config, "auth"), "trusted_callers")
    for handle, caller in trusted_callers.items():
        assert isinstance(caller, dict)
        public_path = caller["public_key_path"]
        assert isinstance(public_path, str)
        generate_keypair(handle, Path(public_path).parent)

    registry: dict[str, str] = {}
    for index, handle in enumerate(("platform", "operator"), start=1):
        public_key = load_public_key(runtime.run_dir / "keys" / f"{handle}.pub")
        registry[f"ed25519:{public_key_to_b64(public_key)}"] = f"agent-{index}"

    process_cwds = {
        **{service: REPO_ROOT / "services" / service for service in SERVICE_ORDER},
        "agents": REPO_ROOT / "agents",
    }
    process_ownership = tuple(
        OwnedProcessV1(
            logical_id=f"service:{service}",
            config_id=service,
            process_cwd=process_cwds[service],
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
    lock_stat = maintenance_lock.stat()
    try:
        yield build_recovery_inventory(
            runtime_id=runtime.run_id,
            source_root=runtime.run_dir,
            config_paths={**runtime.config_paths, "agents": runtime.agent_config_path},
            process_cwds=process_cwds,
            agents_package_root=REPO_ROOT / "agents",
            code_root=REPO_ROOT,
            maintenance_lock_identity=MaintenanceLockIdentityV1(
                device=lock_stat.st_dev,
                inode=lock_stat.st_ino,
            ),
            registry_bindings=registry,
            required_registered_handles=frozenset({"platform", "operator"}),
            process_ownership=process_ownership,
        )
    finally:
        runtime.stop()


def _target_endpoint_values(inventory: RecoveryInventoryV1) -> dict[str, CapturedValue]:
    values: dict[str, CapturedValue] = {}
    next_port = 21000
    for binding in inventory.field_bindings:
        if binding.binding_kind is BindingKind.FILESYSTEM:
            continue
        if binding.binding_kind is BindingKind.PORT:
            values[binding.binding_id] = next_port
            next_port += 1
        elif binding.binding_kind is BindingKind.NETWORK:
            values[binding.binding_id] = f"http://127.0.0.1:{next_port}"
            next_port += 1
        else:
            values[binding.binding_id] = (f"http://127.0.0.1:{next_port}",)
            next_port += 1
    return values


def _minimal_inventory(
    *,
    source_root: Path,
    resources: tuple[SourceResourceV1, ...],
    bindings: tuple[SourceFieldBindingV1, ...] = (),
) -> RecoveryInventoryV1:
    config_path = source_root / "configs" / "gateway.yaml"
    return RecoveryInventoryV1(
        contract_version=1,
        runtime_id="target-test",
        source_root=source_root,
        agents_package_root=source_root / "installed-agents",
        maintenance_lock_path=source_root / "maintenance.lock",
        maintenance_lock_identity=MaintenanceLockIdentityV1(device=1, inode=2),
        resources=resources,
        configs=(
            SourceConfigV1(
                logical_id="gateway",
                capture_root=source_root,
                captured_path=config_path,
                process_cwd=source_root / "process",
                sha256="0" * 64,
            ),
        ),
        field_bindings=bindings,
        keypairs=(),
        process_ownership=(),
    )


def _resource(
    source_root: Path,
    logical_id: str,
    relative: str,
    *,
    kind: ResourceKind,
    capture_kind: CaptureKind,
) -> SourceResourceV1:
    return SourceResourceV1(
        logical_id=logical_id,
        kind=kind,
        capture_kind=capture_kind,
        capture_root=source_root,
        captured_path=source_root / relative,
    )


def test_generated_inventory_maps_owned_and_external_resources_without_io(
    recovery_inventory: RecoveryInventoryV1,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.recovery_targets import build_target_bindings

    target_root = tmp_path / "restored"
    installed_root = tmp_path / "installed"
    process_cwds = {
        config.logical_id: installed_root / "processes" / config.logical_id
        for config in recovery_inventory.configs
    }
    endpoint_values = _target_endpoint_values(recovery_inventory)

    def unexpected_io(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("target binding construction must not perform filesystem I/O")

    monkeypatch.setattr(Path, "exists", unexpected_io)
    monkeypatch.setattr(Path, "is_file", unexpected_io)
    monkeypatch.setattr(Path, "is_dir", unexpected_io)
    monkeypatch.setattr(Path, "read_bytes", unexpected_io)
    monkeypatch.setattr(Path, "stat", unexpected_io)
    result = build_target_bindings(
        recovery_inventory,
        target_root=target_root,
        process_cwds=process_cwds,
        agents_package_root=installed_root / "agents",
        endpoint_values=endpoint_values,
    )

    assert set(result.resource_paths) == {
        resource.logical_id for resource in recovery_inventory.resources
    }
    for resource in recovery_inventory.resources:
        if resource.captured_path.is_relative_to(recovery_inventory.source_root):
            expected = target_root / resource.captured_path.relative_to(
                recovery_inventory.source_root
            )
        else:
            expected = (
                target_root / "captured-code" / resource.logical_id / resource.captured_path.name
            )
        assert result.resource_paths[resource.logical_id] == expected
    assert result.process_cwds == process_cwds
    assert result.agents_package_root == installed_root / "agents"
    assert result.endpoint_values == endpoint_values


@pytest.mark.parametrize(
    ("bad_values", "match"),
    [
        ({}, "endpoint_values.*exact"),
        (
            {"gateway:port": 8123, "unexpected:endpoint": "http://127.0.0.1:8124"},
            "endpoint_values.*exact",
        ),
        ({"gateway:port": True}, "strict integer"),
        ({"gateway:port": 0}, "between 1 and 65535"),
        ({"gateway:port": 65536}, "between 1 and 65535"),
        ({"gateway:port": "8123"}, "strict integer"),
    ],
)
def test_endpoint_inventory_and_port_values_are_strict(
    tmp_path: Path,
    bad_values: dict[str, CapturedValue],
    match: str,
) -> None:
    from scripts.recovery_targets import build_target_bindings

    source_root = tmp_path / "source"
    binding = SourceFieldBindingV1(
        binding_id="gateway:port",
        config_id="gateway",
        field_path=("server", "port"),
        binding_kind=BindingKind.PORT,
        captured_value=8007,
        resolution_base=None,
        resolved_source_path=None,
        resource_id=None,
    )
    inventory = _minimal_inventory(source_root=source_root, resources=(), bindings=(binding,))

    with pytest.raises((TypeError, ValueError), match=match):
        build_target_bindings(
            inventory,
            target_root=tmp_path / "target",
            process_cwds={"gateway": tmp_path / "installed" / "gateway"},
            agents_package_root=tmp_path / "installed" / "agents",
            endpoint_values=bad_values,
        )


@pytest.mark.parametrize(
    ("kind", "value", "match"),
    [
        (BindingKind.NETWORK, "", "non-empty string"),
        (BindingKind.NETWORK, ("http://one",), "non-empty string"),
        (BindingKind.ORIGINS, (), "non-empty tuple"),
        (BindingKind.ORIGINS, ("",), "non-empty strings"),
        (BindingKind.ORIGINS, "http://one", "non-empty tuple"),
    ],
)
def test_non_port_endpoint_types_match_binding_kind(
    tmp_path: Path,
    kind: BindingKind,
    value: CapturedValue,
    match: str,
) -> None:
    from scripts.recovery_targets import build_target_bindings

    source_root = tmp_path / "source"
    binding = SourceFieldBindingV1(
        binding_id="gateway:endpoint",
        config_id="gateway",
        field_path=("endpoint",),
        binding_kind=kind,
        captured_value=("http://source",) if kind is BindingKind.ORIGINS else "http://source",
        resolution_base=None,
        resolved_source_path=None,
        resource_id=None,
    )
    inventory = _minimal_inventory(source_root=source_root, resources=(), bindings=(binding,))

    with pytest.raises((TypeError, ValueError), match=match):
        build_target_bindings(
            inventory,
            target_root=tmp_path / "target",
            process_cwds={"gateway": tmp_path / "installed" / "gateway"},
            agents_package_root=tmp_path / "installed" / "agents",
            endpoint_values={binding.binding_id: value},
        )


def test_process_cwd_inventory_must_match_configs_exactly(tmp_path: Path) -> None:
    from scripts.recovery_targets import build_target_bindings

    inventory = _minimal_inventory(source_root=tmp_path / "source", resources=())
    with pytest.raises(ValueError, match=r"process_cwds.*exact"):
        build_target_bindings(
            inventory,
            target_root=tmp_path / "target",
            process_cwds={},
            agents_package_root=tmp_path / "installed" / "agents",
            endpoint_values={},
        )


def test_undeclared_external_resource_is_rejected(tmp_path: Path) -> None:
    from scripts.recovery_targets import build_target_bindings

    source_root = tmp_path / "source"
    external_root = tmp_path / "external"
    resource = SourceResourceV1(
        logical_id="assets.root",
        kind=ResourceKind.DIRECTORY,
        capture_kind=CaptureKind.COPY,
        capture_root=external_root,
        captured_path=external_root / "assets",
    )
    inventory = _minimal_inventory(source_root=source_root, resources=(resource,))

    with pytest.raises(ValueError, match="outside source_root"):
        build_target_bindings(
            inventory,
            target_root=tmp_path / "target",
            process_cwds={"gateway": tmp_path / "installed" / "gateway"},
            agents_package_root=tmp_path / "installed" / "agents",
            endpoint_values={},
        )


def test_equal_target_paths_are_rejected(tmp_path: Path) -> None:
    from scripts.recovery_targets import build_target_bindings

    source_root = tmp_path / "source"
    shared_path = source_root / "shared"
    resources = (
        SourceResourceV1(
            logical_id="first",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.COPY,
            capture_root=source_root,
            captured_path=shared_path,
        ),
        SourceResourceV1(
            logical_id="second",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.COPY,
            capture_root=source_root,
            captured_path=shared_path,
        ),
    )
    inventory = _minimal_inventory(source_root=source_root, resources=resources)

    with pytest.raises(ValueError, match="equal target paths"):
        build_target_bindings(
            inventory,
            target_root=tmp_path / "target",
            process_cwds={"gateway": tmp_path / "installed" / "gateway"},
            agents_package_root=tmp_path / "installed" / "agents",
            endpoint_values={},
        )


def test_only_directory_only_resource_may_contain_another_resource(tmp_path: Path) -> None:
    from scripts.recovery_targets import build_target_bindings

    source_root = tmp_path / "source"
    copy_parent = _resource(
        source_root,
        "copied.tree",
        "tree",
        kind=ResourceKind.DIRECTORY,
        capture_kind=CaptureKind.COPY,
    )
    child = _resource(
        source_root,
        "copied.child",
        "tree/child.txt",
        kind=ResourceKind.FILE,
        capture_kind=CaptureKind.COPY,
    )
    invalid = _minimal_inventory(source_root=source_root, resources=(copy_parent, child))

    with pytest.raises(ValueError, match="DIRECTORY_ONLY"):
        build_target_bindings(
            invalid,
            target_root=tmp_path / "target",
            process_cwds={"gateway": tmp_path / "installed" / "gateway"},
            agents_package_root=tmp_path / "installed" / "agents",
            endpoint_values={},
        )

    directory_only_parent = copy_parent.model_copy(
        update={"capture_kind": CaptureKind.DIRECTORY_ONLY}
    )
    valid = _minimal_inventory(
        source_root=source_root,
        resources=(directory_only_parent, child),
    )
    result = build_target_bindings(
        valid,
        target_root=tmp_path / "target-allowed",
        process_cwds={"gateway": tmp_path / "installed" / "gateway"},
        agents_package_root=tmp_path / "installed" / "agents",
        endpoint_values={},
    )

    assert result.resource_paths["copied.child"].is_relative_to(
        result.resource_paths["copied.tree"]
    )
