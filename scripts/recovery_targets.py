"""Build explicit recovery targets from one complete captured inventory.

This module only projects already-captured facts into a caller-selected target
layout. It performs no filesystem access, reserves no ports, and does not retain
source endpoint values implicitly.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from service_commons.recovery_contract import (
    BindingKind,
    CapturedValue,
    CaptureKind,
    RecoveryInventoryV1,
    SourceResourceV1,
    TargetBindingsV1,
)

_EXTERNAL_CODE_RESOURCES = frozenset({"database.schema", "frontend.web"})


def _canonical_absolute(path: Path, *, label: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError(f"{label} must be an absolute pathlib.Path")
    if Path(os.path.normpath(path)) != path:
        raise ValueError(f"{label} must be lexically normalized")
    return path


def _require_mapping(value: object, *, label: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return value


def _resource_target(
    resource: SourceResourceV1,
    *,
    source_root: Path,
    target_root: Path,
) -> Path:
    if resource.captured_path.is_relative_to(source_root):
        relative = resource.captured_path.relative_to(source_root)
        if relative == Path():
            raise ValueError(f"Recovery resource {resource.logical_id} cannot equal source_root")
        return target_root / relative

    if resource.logical_id not in _EXTERNAL_CODE_RESOURCES:
        raise ValueError(
            f"Recovery resource {resource.logical_id} is outside source_root and is not "
            "a declared external code input"
        )
    basename = resource.captured_path.name
    if not basename or basename in {".", ".."}:
        raise ValueError(f"External recovery resource {resource.logical_id} has no safe basename")
    return target_root / "captured-code" / resource.logical_id / basename


def _validate_target_topology(
    resources: tuple[SourceResourceV1, ...],
    resource_paths: Mapping[str, Path],
) -> None:
    indexed = tuple((resource, resource_paths[resource.logical_id]) for resource in resources)
    for index, (resource, path) in enumerate(indexed):
        for other_resource, other_path in indexed[index + 1 :]:
            if path == other_path:
                raise ValueError(
                    "Recovery resources have equal target paths: "
                    f"{resource.logical_id}, {other_resource.logical_id}"
                )
            if other_path.is_relative_to(path):
                ancestor_resource = resource
            elif path.is_relative_to(other_path):
                ancestor_resource = other_resource
            else:
                continue
            if ancestor_resource.capture_kind is not CaptureKind.DIRECTORY_ONLY:
                raise ValueError(
                    "Only a DIRECTORY_ONLY resource may contain another target resource: "
                    f"{ancestor_resource.logical_id}"
                )


def _validate_endpoint_value(
    binding_kind: BindingKind,
    value: object,
    *,
    binding_id: str,
) -> CapturedValue:
    if binding_kind is BindingKind.PORT:
        if type(value) is not int:
            raise TypeError(f"Target port {binding_id} must be a strict integer")
        if not 1 <= value <= 65535:
            raise ValueError(f"Target port {binding_id} must be between 1 and 65535")
        return value
    if binding_kind is BindingKind.NETWORK:
        if type(value) is not str or not value:
            raise TypeError(f"Target network value {binding_id} must be a non-empty string")
        return value
    if binding_kind is BindingKind.ORIGINS:
        if not isinstance(value, tuple) or not value:
            raise TypeError(f"Target origins value {binding_id} must be a non-empty tuple")
        if any(type(origin) is not str or not origin for origin in value):
            raise TypeError(
                f"Target origins value {binding_id} must contain only non-empty strings"
            )
        return value
    raise ValueError(f"Unsupported non-filesystem binding kind: {binding_kind}")


def build_target_bindings(
    inventory: RecoveryInventoryV1,
    *,
    target_root: Path,
    process_cwds: Mapping[str, Path],
    agents_package_root: Path,
    endpoint_values: Mapping[str, CapturedValue],
) -> TargetBindingsV1:
    """Return a complete, explicit target contract without filesystem access."""
    if not isinstance(inventory, RecoveryInventoryV1):
        raise TypeError("inventory must be RecoveryInventoryV1")
    target_root = _canonical_absolute(target_root, label="target_root")
    agents_package_root = _canonical_absolute(
        agents_package_root,
        label="agents_package_root",
    )

    _require_mapping(process_cwds, label="process_cwds")
    supplied_cwds = process_cwds
    config_ids = {config.logical_id for config in inventory.configs}
    if set(supplied_cwds) != config_ids:
        raise ValueError("process_cwds keys must match the config inventory exactly")
    normalized_cwds: dict[str, Path] = {}
    for config_id, process_cwd in supplied_cwds.items():
        if type(config_id) is not str:
            raise TypeError("process_cwds keys must be strings")
        normalized_cwds[config_id] = _canonical_absolute(
            process_cwd,
            label=f"process_cwds[{config_id}]",
        )

    _require_mapping(endpoint_values, label="endpoint_values")
    supplied_endpoints = endpoint_values
    endpoint_bindings = {
        binding.binding_id: binding
        for binding in inventory.field_bindings
        if binding.binding_kind is not BindingKind.FILESYSTEM
    }
    if set(supplied_endpoints) != set(endpoint_bindings):
        raise ValueError("endpoint_values keys must match the endpoint inventory exactly")
    normalized_endpoints = {
        binding_id: _validate_endpoint_value(
            endpoint_bindings[binding_id].binding_kind,
            value,
            binding_id=binding_id,
        )
        for binding_id, value in supplied_endpoints.items()
    }

    resource_paths = {
        resource.logical_id: _resource_target(
            resource,
            source_root=inventory.source_root,
            target_root=target_root,
        )
        for resource in inventory.resources
    }
    _validate_target_topology(inventory.resources, resource_paths)

    return TargetBindingsV1(
        contract_version=1,
        target_root=target_root,
        process_cwds=normalized_cwds,
        agents_package_root=agents_package_root,
        resource_paths=resource_paths,
        endpoint_values=normalized_endpoints,
    )
