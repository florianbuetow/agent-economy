"""A restored runtime is adopted only from independently verified evidence."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError
from scripts.isolated_test_runtime import SERVICE_ORDER
from service_commons.recovery_contract import (
    CaptureKind,
    MaintenanceLockIdentityV1,
    RecoveryInventoryV1,
    ResourceKind,
    SourceConfigV1,
    SourceResourceV1,
    TargetBindingsV1,
)

from db_gateway_service.recovery import VerificationResult

if TYPE_CHECKING:
    from scripts.recovery_adoption import RestoredRuntimeView

pytestmark = pytest.mark.integration
_CONFIG_IDS = frozenset((*SERVICE_ORDER, "agents"))


def _contracts(
    tmp_path: Path,
    *,
    config_ids: frozenset[str] = _CONFIG_IDS,
    manifest_target_root: Path | None = None,
    duplicate_config_resource: bool = False,
) -> tuple[RecoveryInventoryV1, TargetBindingsV1]:
    source_root = tmp_path / "source"
    target_root = manifest_target_root or tmp_path / "target"
    configs = tuple(
        SourceConfigV1(
            logical_id=config_id,
            capture_root=source_root,
            captured_path=source_root / "configs" / f"{config_id}.yaml",
            process_cwd=tmp_path / "installed" / config_id,
            sha256="0" * 64,
        )
        for config_id in sorted(config_ids)
    )
    resources = [
        SourceResourceV1(
            logical_id=f"config.{config.logical_id}",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
            capture_root=source_root,
            captured_path=config.captured_path,
        )
        for config in configs
    ]
    if duplicate_config_resource:
        config = configs[0]
        resources.append(
            SourceResourceV1(
                logical_id="config.duplicate",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.CONFIG,
                capture_root=source_root,
                captured_path=config.captured_path,
            )
        )
    inventory = RecoveryInventoryV1(
        contract_version=1,
        runtime_id="restored-view-test",
        source_root=source_root,
        agents_package_root=tmp_path / "installed" / "agents",
        maintenance_lock_path=source_root / "maintenance.lock",
        maintenance_lock_identity=MaintenanceLockIdentityV1(device=1, inode=2),
        resources=tuple(resources),
        configs=configs,
        field_bindings=(),
        keypairs=(),
        process_ownership=(),
    )
    bindings = TargetBindingsV1(
        contract_version=1,
        target_root=target_root,
        process_cwds={config.logical_id: config.process_cwd for config in configs},
        agents_package_root=tmp_path / "installed" / "agents",
        resource_paths={
            resource.logical_id: target_root / "configs" / f"{resource.logical_id}.yaml"
            for resource in resources
        },
        endpoint_values={},
    )
    return inventory, bindings


def _write_manifest(
    target_root: Path,
    inventory: RecoveryInventoryV1,
    bindings: TargetBindingsV1,
) -> bytes:
    target_root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "source_inventory": inventory.model_dump(mode="json"),
            "target_bindings": bindings.model_dump(mode="json"),
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    (target_root / "restore-manifest.json").write_bytes(payload)
    return payload


def _verification(payload: bytes, **changes: object) -> VerificationResult:
    values: dict[str, object] = {
        "artifact_verified": True,
        "snapshot_preserved": True,
        "identity_complete": False,
        "migration_eligible": False,
        "operational_restore_verified": False,
        "finding_codes": ("identity_incomplete",),
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
    }
    values.update(changes)
    return VerificationResult(**values)  # type: ignore[arg-type]


def _module_with_verifier(
    monkeypatch: pytest.MonkeyPatch,
    result: VerificationResult,
) -> object:
    module = importlib.import_module("scripts.recovery_adoption")

    def fake_verify_restore(*, target_root: Path, work_root: Path) -> VerificationResult:
        assert target_root.is_absolute()
        assert work_root.is_absolute()
        return result

    monkeypatch.setattr(module, "verify_restore", fake_verify_restore)
    return module


def _adopt(
    module: object,
    *,
    repo_root: Path,
    target_root: Path,
    work_root: Path,
) -> RestoredRuntimeView:
    from scripts.recovery_adoption import adopt_restored_check_only

    assert vars(module)["adopt_restored_check_only"] is adopt_restored_check_only
    return adopt_restored_check_only(
        repo_root=repo_root,
        target_root=target_root,
        work_root=work_root,
    )


def test_recovery_adoption_api_exists() -> None:
    module = importlib.import_module("scripts.recovery_adoption")

    assert callable(module.adopt_restored_check_only)
    signature = inspect.signature(module.adopt_restored_check_only)
    assert tuple(signature.parameters) == ("repo_root", "target_root", "work_root")
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is inspect.Parameter.empty
        for parameter in signature.parameters.values()
    )
    assert module.RestoredRuntimeView.__dataclass_params__.frozen is True
    assert "__dict__" not in module.RestoredRuntimeView.__slots__


def test_verified_full_runtime_yields_immutable_check_only_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "target"
    inventory, bindings = _contracts(tmp_path)
    payload = _write_manifest(target_root, inventory, bindings)
    result = _verification(payload)
    module = _module_with_verifier(monkeypatch, result)
    before = hashlib.sha256((target_root / "restore-manifest.json").read_bytes()).hexdigest()

    view = _adopt(
        module,
        repo_root=tmp_path / "installed-repository-does-not-need-to-exist",
        target_root=target_root,
        work_root=tmp_path / "audit-work",
    )

    assert view.repo_root == tmp_path / "installed-repository-does-not-need-to-exist"
    assert view.target_root == target_root
    assert view.result == result
    assert view.result is not result
    assert view.result.identity_complete is False
    assert view.result.migration_eligible is False
    assert view.result.operational_restore_verified is False
    assert view.result.finding_codes == ("identity_incomplete",)
    assert view.source_inventory == inventory
    assert view.source_inventory is not inventory
    assert view.target_bindings == bindings
    assert view.target_bindings is not bindings
    assert isinstance(view.config_paths, MappingProxyType)
    assert set(view.config_paths) == _CONFIG_IDS
    assert view.config_paths == {
        config.logical_id: bindings.resource_paths[f"config.{config.logical_id}"]
        for config in inventory.configs
    }
    assert not hasattr(view, "start")
    assert not hasattr(view, "run_command")
    assert not hasattr(view, "provision")
    assert (
        hashlib.sha256((target_root / "restore-manifest.json").read_bytes()).hexdigest() == before
    )

    with pytest.raises(TypeError):
        cast("dict[str, Path]", view.config_paths)["ui"] = tmp_path / "elsewhere"
    with pytest.raises(FrozenInstanceError):
        setattr(view, "target_root", tmp_path / "elsewhere")  # noqa: B010
    with pytest.raises(ValidationError):
        setattr(view.source_inventory, "runtime_id", "changed")  # noqa: B010


@pytest.mark.parametrize(
    ("flag", "value"),
    [("artifact_verified", False), ("snapshot_preserved", False)],
)
def test_unverified_or_unpreserved_restore_cannot_be_adopted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
    value: bool,
) -> None:
    target_root = tmp_path / "target"
    inventory, bindings = _contracts(tmp_path)
    payload = _write_manifest(target_root, inventory, bindings)
    module = _module_with_verifier(monkeypatch, _verification(payload, **{flag: value}))

    with pytest.raises(ValueError, match=r"artifact_verified.*snapshot_preserved"):
        _adopt(
            module,
            repo_root=tmp_path / "repo",
            target_root=target_root,
            work_root=tmp_path / "work",
        )


def test_manifest_must_match_the_exact_bytes_verified_by_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "target"
    inventory, bindings = _contracts(tmp_path)
    payload = _write_manifest(target_root, inventory, bindings)
    result = _verification(payload, manifest_sha256="f" * 64)
    module = _module_with_verifier(monkeypatch, result)

    with pytest.raises(ValueError, match="manifest digest"):
        _adopt(
            module,
            repo_root=tmp_path / "repo",
            target_root=target_root,
            work_root=tmp_path / "work",
        )


def test_malformed_digest_bound_manifest_is_rejected_after_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "target"
    target_root.mkdir()
    payload = b"{malformed"
    (target_root / "restore-manifest.json").write_bytes(payload)
    module = _module_with_verifier(monkeypatch, _verification(payload))

    with pytest.raises(ValueError, match="restore manifest"):
        _adopt(
            module,
            repo_root=tmp_path / "repo",
            target_root=target_root,
            work_root=tmp_path / "work",
        )


def test_manifest_target_root_must_equal_the_adopted_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "target"
    inventory, bindings = _contracts(
        tmp_path,
        manifest_target_root=tmp_path / "different-target",
    )
    payload = _write_manifest(target_root, inventory, bindings)
    module = _module_with_verifier(monkeypatch, _verification(payload))

    with pytest.raises(ValueError, match="target_root"):
        _adopt(
            module,
            repo_root=tmp_path / "repo",
            target_root=target_root,
            work_root=tmp_path / "work",
        )


def test_adoption_requires_exactly_one_config_resource_for_all_eight_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_root = tmp_path / "target"
    partial_inventory, partial_bindings = _contracts(
        tmp_path,
        config_ids=frozenset(config_id for config_id in _CONFIG_IDS if config_id != "ui"),
    )
    payload = _write_manifest(target_root, partial_inventory, partial_bindings)
    module = _module_with_verifier(monkeypatch, _verification(payload))

    with pytest.raises(ValueError, match=r"seven service.*agents"):
        _adopt(
            module,
            repo_root=tmp_path / "repo",
            target_root=target_root,
            work_root=tmp_path / "work",
        )

    duplicate_root = tmp_path / "duplicate-target"
    duplicate_inventory, duplicate_bindings = _contracts(
        tmp_path,
        manifest_target_root=duplicate_root,
        duplicate_config_resource=True,
    )
    duplicate_payload = _write_manifest(
        duplicate_root,
        duplicate_inventory,
        duplicate_bindings,
    )
    module = _module_with_verifier(monkeypatch, _verification(duplicate_payload))
    with pytest.raises(ValueError, match="exactly one CONFIG resource"):
        _adopt(
            module,
            repo_root=tmp_path / "repo",
            target_root=duplicate_root,
            work_root=tmp_path / "work-two",
        )


@pytest.mark.parametrize("argument", ["repo_root", "target_root", "work_root"])
def test_all_path_arguments_are_required_canonical_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argument: str,
) -> None:
    target_root = tmp_path / "target"
    inventory, bindings = _contracts(tmp_path)
    payload = _write_manifest(target_root, inventory, bindings)
    module = _module_with_verifier(monkeypatch, _verification(payload))
    arguments = {
        "repo_root": tmp_path / "repo",
        "target_root": target_root,
        "work_root": tmp_path / "work",
    }
    arguments[argument] = Path("relative")

    with pytest.raises(ValueError, match=f"{argument}.*absolute"):
        _adopt(module, **arguments)
