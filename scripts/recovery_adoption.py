"""Read-only adoption of an independently verified restored runtime."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from scripts.isolated_test_runtime import SERVICE_ORDER
from scripts.recovery_inventory import read_recovery_source_file
from service_commons.recovery_contract import (
    CaptureKind,
    RecoveryInventoryV1,
    TargetBindingsV1,
)

from db_gateway_service.recovery import VerificationResult, verify_restore

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

_REQUIRED_CONFIG_IDS = frozenset((*SERVICE_ORDER, "agents"))
_RESTORE_MANIFEST = "restore-manifest.json"


@dataclass(frozen=True, slots=True)
class RestoredRuntimeView:
    """Immutable evidence needed by a separate restored-runtime lifecycle."""

    repo_root: Path
    target_root: Path
    result: VerificationResult
    target_bindings: TargetBindingsV1
    source_inventory: RecoveryInventoryV1
    config_paths: Mapping[str, Path]


def _canonical_absolute(path: Path, *, label: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError(f"{label} must be an absolute pathlib.Path")
    if Path(os.path.normpath(str(path))) != path:
        raise ValueError(f"{label} must be lexically normalized")
    return path


def _manifest_mapping(manifest_bytes: bytes) -> dict[str, Any]:
    try:
        value: object = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("restore manifest is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise ValueError("restore manifest must be a string-keyed JSON object")
    return value


def _copy_verification(result: VerificationResult) -> VerificationResult:
    if not isinstance(result, VerificationResult):
        raise TypeError("verify_restore must return VerificationResult")
    return VerificationResult(
        artifact_verified=result.artifact_verified,
        snapshot_preserved=result.snapshot_preserved,
        identity_complete=result.identity_complete,
        migration_eligible=result.migration_eligible,
        operational_restore_verified=result.operational_restore_verified,
        finding_codes=tuple(result.finding_codes),
        manifest_sha256=result.manifest_sha256,
    )


def _contracts_from_manifest(
    manifest: Mapping[str, Any],
) -> tuple[RecoveryInventoryV1, TargetBindingsV1]:
    try:
        inventory = RecoveryInventoryV1.model_validate_json(
            json.dumps(
                manifest["source_inventory"],
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        target_bindings = TargetBindingsV1.model_validate_json(
            json.dumps(
                manifest["target_bindings"],
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("restore manifest contains invalid recovery contracts") from exc
    return inventory, target_bindings


def _restored_config_paths(
    inventory: RecoveryInventoryV1,
    target_bindings: TargetBindingsV1,
) -> Mapping[str, Path]:
    config_ids = frozenset(config.logical_id for config in inventory.configs)
    if config_ids != _REQUIRED_CONFIG_IDS or len(inventory.configs) != len(_REQUIRED_CONFIG_IDS):
        raise ValueError(
            "restored runtime view requires all seven service configurations and agents"
        )

    config_paths: dict[str, Path] = {}
    for config in inventory.configs:
        matching_resources = tuple(
            resource
            for resource in inventory.resources
            if resource.capture_kind is CaptureKind.CONFIG
            and resource.captured_path == config.captured_path
        )
        if len(matching_resources) != 1:
            raise ValueError(
                f"configuration {config.logical_id} must have exactly one CONFIG resource"
            )
        resource_id = matching_resources[0].logical_id
        try:
            config_paths[config.logical_id] = target_bindings.resource_paths[resource_id]
        except KeyError as exc:
            raise ValueError(f"target bindings omit configuration resource {resource_id}") from exc
    return MappingProxyType(dict(config_paths))


def adopt_restored_check_only(
    *,
    repo_root: Path,
    target_root: Path,
    work_root: Path,
) -> RestoredRuntimeView:
    """Verify and expose a restored runtime without granting lifecycle authority."""

    repo_root = _canonical_absolute(repo_root, label="repo_root")
    target_root = _canonical_absolute(target_root, label="target_root")
    work_root = _canonical_absolute(work_root, label="work_root")

    verified = _copy_verification(verify_restore(target_root=target_root, work_root=work_root))
    if not verified.artifact_verified or not verified.snapshot_preserved:
        raise ValueError("adoption requires artifact_verified and snapshot_preserved")

    manifest_bytes = read_recovery_source_file(
        target_root / _RESTORE_MANIFEST,
        label="restore manifest",
    )
    if hashlib.sha256(manifest_bytes).hexdigest() != verified.manifest_sha256:
        raise ValueError("restore manifest digest differs from verification result")

    manifest = _manifest_mapping(manifest_bytes)
    source_inventory, target_bindings = _contracts_from_manifest(manifest)
    if target_bindings.target_root != target_root:
        raise ValueError("restore manifest target_root differs from adopted target_root")
    config_paths = _restored_config_paths(source_inventory, target_bindings)

    return RestoredRuntimeView(
        repo_root=repo_root,
        target_root=target_root,
        result=verified,
        target_bindings=target_bindings,
        source_inventory=source_inventory,
        config_paths=config_paths,
    )
