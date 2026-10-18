"""Neutral serialized contracts for recovery inventory and target bindings.

The models in this module validate captured facts only. They perform no filesystem
I/O and do not decide whether an inventory is complete enough for backup, restore,
or migration.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)

CapturedValue = StrictStr | StrictInt | tuple[StrictStr, ...]


class ResourceKind(StrEnum):
    """Filesystem shape of a captured logical resource."""

    FILE = "file"
    DIRECTORY = "directory"


class CaptureKind(StrEnum):
    """Mechanical handling required when snapshotting a source resource."""

    COPY = "copy"
    KEYPAIR = "keypair"
    CONFIG = "config"
    DIRECTORY_ONLY = "directory_only"
    ABSENT_FILE = "absent_file"
    ABSENT_DIRECTORY = "absent_directory"
    RUNTIME_LOCK = "runtime_lock"


class KeypairRole(StrEnum):
    """Trust domain in which a configured keypair is used."""

    ECONOMIC = "economic"
    GATEWAY = "gateway"


class RegistrationRequirement(StrEnum):
    """Whether a key is expected to have an economic identity registration."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


class CapturedRegistrationState(StrEnum):
    """Registration state observed for one public key at inventory capture time."""

    REGISTERED = "registered"
    UNREGISTERED = "unregistered"


class BindingKind(StrEnum):
    """Interpretation of a captured configuration field."""

    FILESYSTEM = "filesystem"
    NETWORK = "network"
    PORT = "port"
    ORIGINS = "origins"


class PathResolutionBase(StrEnum):
    """Captured anchor used by an application to resolve a filesystem field."""

    PROCESS_CWD = "process_cwd"
    CONFIG_PARENT = "config_parent"
    AGENTS_PACKAGE_ROOT = "agents_package_root"
    ABSOLUTE = "absolute"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_identifier(value: str, *, field_name: str) -> str:
    if not value or value.strip() != value:
        msg = f"{field_name} must be non-empty and have no surrounding whitespace"
        raise ValueError(msg)
    return value


def _require_path_component(value: str, *, field_name: str) -> str:
    _require_identifier(value, field_name=field_name)
    if value in {".", ".."} or "/" in value or "\\" in value or Path(value).is_absolute():
        msg = f"{field_name} must be one filename-safe path component"
        raise ValueError(msg)
    if any(unicodedata.category(character) == "Cc" for character in value):
        msg = f"{field_name} must not contain control characters"
        raise ValueError(msg)
    return value


def _require_canonical_absolute(value: Path, *, field_name: str) -> Path:
    if not value.is_absolute():
        msg = f"{field_name} must be absolute"
        raise ValueError(msg)
    normalized = Path(os.path.normpath(str(value)))
    if normalized != value:
        msg = f"{field_name} must be lexically normalized"
        raise ValueError(msg)
    return value


def _require_within_capture_root(captured_path: Path, capture_root: Path) -> None:
    if not captured_path.is_relative_to(capture_root):
        raise ValueError("captured_path must be within or equal to capture_root")


def _require_digest(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        msg = "sha256 must be exactly 64 lowercase hexadecimal characters"
        raise ValueError(msg)
    return value


def _require_contract_version(value: object) -> object:
    if type(value) is not int:
        raise ValueError("contract_version must be the strict integer 1")
    return value


def _freeze_mapping[KeyT, ValueT](value: Mapping[KeyT, ValueT]) -> Mapping[KeyT, ValueT]:
    if not isinstance(value, Mapping):
        raise TypeError("mapping fields require a mapping value")
    return MappingProxyType(dict(value))


class SourceResourceV1(_ContractModel):
    """One logical source filesystem resource and its captured absolute path."""

    logical_id: StrictStr
    kind: ResourceKind
    capture_kind: CaptureKind
    capture_root: Path
    captured_path: Path

    @field_validator("logical_id")
    @classmethod
    def validate_logical_id(cls, value: str) -> str:
        return _require_path_component(value, field_name="logical_id")

    @field_validator("capture_root", "captured_path")
    @classmethod
    def validate_capture_path(cls, value: Path, info: object) -> Path:
        field_name = getattr(info, "field_name", "path")
        return _require_canonical_absolute(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_capture_shape(self) -> Self:
        _require_within_capture_root(self.captured_path, self.capture_root)
        file_capture_kinds = {
            CaptureKind.COPY,
            CaptureKind.KEYPAIR,
            CaptureKind.CONFIG,
            CaptureKind.ABSENT_FILE,
            CaptureKind.RUNTIME_LOCK,
        }
        directory_capture_kinds = {
            CaptureKind.COPY,
            CaptureKind.DIRECTORY_ONLY,
            CaptureKind.ABSENT_DIRECTORY,
        }
        allowed = file_capture_kinds if self.kind is ResourceKind.FILE else directory_capture_kinds
        if self.capture_kind not in allowed:
            raise ValueError("capture_kind is incompatible with the resource filesystem shape")
        return self


class SourceConfigV1(_ContractModel):
    """Exact selected source configuration and the process CWD that consumed it."""

    logical_id: StrictStr
    capture_root: Path
    captured_path: Path
    process_cwd: Path
    sha256: StrictStr

    @field_validator("logical_id")
    @classmethod
    def validate_logical_id(cls, value: str) -> str:
        return _require_path_component(value, field_name="logical_id")

    @field_validator("capture_root", "captured_path", "process_cwd")
    @classmethod
    def validate_absolute_path(cls, value: Path, info: object) -> Path:
        field_name = getattr(info, "field_name", "path")
        return _require_canonical_absolute(value, field_name=field_name)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        return _require_digest(value)

    @model_validator(mode="after")
    def validate_capture_boundary(self) -> Self:
        _require_within_capture_root(self.captured_path, self.capture_root)
        return self


class SourceFieldBindingV1(_ContractModel):
    """One concrete captured config field and its unambiguous interpretation."""

    binding_id: StrictStr
    config_id: StrictStr
    field_path: tuple[StrictStr | StrictInt, ...]
    binding_kind: BindingKind
    captured_value: CapturedValue
    resolution_base: PathResolutionBase | None
    resolved_source_path: Path | None
    resource_id: StrictStr | None

    @field_validator("binding_id", "config_id")
    @classmethod
    def validate_identifier(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "identifier")
        return _require_identifier(value, field_name=field_name)

    @field_validator("field_path")
    @classmethod
    def validate_field_path(
        cls, value: tuple[StrictStr | StrictInt, ...]
    ) -> tuple[StrictStr | StrictInt, ...]:
        if not value:
            raise ValueError("field_path must contain at least one segment")
        for segment in value:
            if isinstance(segment, str):
                _require_identifier(segment, field_name="field_path segment")
                if "*" in segment or "." in segment:
                    raise ValueError("field_path string segments must be concrete and undotted")
            elif segment < 0:
                raise ValueError("field_path integer segments must be non-negative")
        return value

    @field_validator("resolved_source_path")
    @classmethod
    def validate_resolved_source_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return _require_canonical_absolute(value, field_name="resolved_source_path")

    @model_validator(mode="after")
    def validate_binding_shape(self) -> Self:
        if self.binding_kind is BindingKind.FILESYSTEM:
            self._validate_filesystem_shape()
            return self

        self._validate_nonfilesystem_shape()
        validators = {
            BindingKind.NETWORK: self._validate_network_value,
            BindingKind.PORT: self._validate_port_value,
            BindingKind.ORIGINS: self._validate_origins_value,
        }
        validators[self.binding_kind]()
        return self

    def _validate_filesystem_shape(self) -> None:
        if not isinstance(self.captured_value, str) or not self.captured_value:
            raise ValueError("filesystem captured_value must be a non-empty string")
        if self.resolution_base is None:
            raise ValueError("filesystem binding requires resolution_base")
        if self.resolved_source_path is None:
            raise ValueError("filesystem binding requires resolved_source_path")
        if self.resource_id is None:
            raise ValueError("filesystem binding requires resource_id")
        _require_identifier(self.resource_id, field_name="resource_id")

    def _validate_nonfilesystem_shape(self) -> None:
        if self.resolution_base is not None:
            raise ValueError("non-filesystem binding forbids resolution_base")
        if self.resolved_source_path is not None:
            raise ValueError("non-filesystem binding forbids resolved_source_path")
        if self.resource_id is not None:
            raise ValueError("non-filesystem binding forbids resource_id")

    def _validate_network_value(self) -> None:
        if not isinstance(self.captured_value, str) or not self.captured_value:
            raise ValueError("network captured_value must be a non-empty string")

    def _validate_port_value(self) -> None:
        if isinstance(self.captured_value, bool) or not isinstance(self.captured_value, int):
            raise ValueError("port captured_value must be a strict integer")

    def _validate_origins_value(self) -> None:
        if not isinstance(self.captured_value, tuple) or any(
            not origin for origin in self.captured_value
        ):
            raise ValueError("origins captured_value must be a tuple of non-empty strings")


class SourceKeypairV1(_ContractModel):
    """Canonical key resources and explicit captured identity facts for one handle.

    The captured fingerprint and registration facts do not by themselves prove
    identity completeness. Recovery consumers must verify them against the
    captured key bytes and identity registry.
    """

    handle: StrictStr
    role: KeypairRole
    private_resource_id: StrictStr
    public_resource_id: StrictStr
    expected_public_fingerprint_sha256: StrictStr
    registration_requirement: RegistrationRequirement
    captured_registration_state: CapturedRegistrationState
    expected_registered_agent_id: StrictStr | None

    @field_validator("handle")
    @classmethod
    def validate_handle(cls, value: str) -> str:
        return _require_path_component(value, field_name="handle")

    @field_validator("private_resource_id", "public_resource_id")
    @classmethod
    def validate_resource_id(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "resource_id")
        return _require_identifier(value, field_name=field_name)

    @field_validator("expected_public_fingerprint_sha256")
    @classmethod
    def validate_expected_public_fingerprint_sha256(cls, value: str) -> str:
        return _require_digest(value)

    @field_validator("expected_registered_agent_id")
    @classmethod
    def validate_expected_registered_agent_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_identifier(value, field_name="expected_registered_agent_id")

    @model_validator(mode="after")
    def validate_keypair_facts(self) -> Self:
        if self.private_resource_id == self.public_resource_id:
            raise ValueError("private and public key resources must be distinct")

        is_registered = self.captured_registration_state is CapturedRegistrationState.REGISTERED
        if is_registered and self.expected_registered_agent_id is None:
            raise ValueError("registered keys require expected_registered_agent_id")
        if not is_registered and self.expected_registered_agent_id is not None:
            raise ValueError("unregistered keys forbid expected_registered_agent_id")

        if self.role is KeypairRole.GATEWAY:
            if self.registration_requirement is not RegistrationRequirement.FORBIDDEN:
                raise ValueError("gateway keys must forbid economic registration")
            if is_registered:
                raise ValueError("gateway keys must be captured as unregistered")
            return self

        if self.registration_requirement is RegistrationRequirement.FORBIDDEN:
            raise ValueError("economic keys cannot forbid economic registration")
        if self.registration_requirement is RegistrationRequirement.REQUIRED and not is_registered:
            raise ValueError("required economic keys must be captured as registered")
        return self


class MaintenanceLockIdentityV1(_ContractModel):
    """Kernel identity captured from the maintenance lock held by the runtime.

    These values are source evidence for detecting path replacement. They do not
    independently authorize signaling or prove process ownership.
    """

    device: StrictInt
    inode: StrictInt

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: int) -> int:
        if value < 0:
            raise ValueError("maintenance lock device must be non-negative")
        return value

    @field_validator("inode")
    @classmethod
    def validate_inode(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("maintenance lock inode must be positive")
        return value


class OwnedProcessV1(_ContractModel):
    """Recorded supervisor facts for one source-runtime process.

    These facts never authorize signaling a process and do not independently prove
    quiescence. Recovery consumers still require actual supervisor ownership,
    maintenance-lock exclusion, and any applicable fresh-host checks.
    """

    logical_id: StrictStr
    config_id: StrictStr | None
    process_cwd: Path
    agents_package_root: Path | None
    pid: StrictInt | None
    pgid: StrictInt | None
    sid: StrictInt | None
    running: StrictBool
    group_drained: StrictBool
    leader_returncode: StrictInt | None
    cleanup_error: StrictStr | None

    @field_validator("logical_id")
    @classmethod
    def validate_logical_id(cls, value: str) -> str:
        return _require_identifier(value, field_name="logical_id")

    @field_validator("config_id")
    @classmethod
    def validate_config_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_identifier(value, field_name="config_id")

    @field_validator("process_cwd", "agents_package_root")
    @classmethod
    def validate_absolute_path(cls, value: Path | None, info: object) -> Path | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "path")
        return _require_canonical_absolute(value, field_name=field_name)

    @field_validator("cleanup_error")
    @classmethod
    def validate_cleanup_error(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_identifier(value, field_name="cleanup_error")

    @model_validator(mode="after")
    def validate_process_facts(self) -> Self:
        identities = (self.pid, self.pgid, self.sid)
        present = tuple(identifier is not None for identifier in identities)
        if any(present) and not all(present):
            raise ValueError("pid, pgid, and sid must be present together or all be None")
        if all(present) and any(cast("int", identifier) <= 0 for identifier in identities):
            raise ValueError("pid, pgid, and sid must be positive strict integers")
        if self.running and not all(present):
            raise ValueError("a running process requires pid, pgid, and sid")
        if self.running and self.group_drained:
            raise ValueError("a running process group cannot be recorded as drained")
        if self.running and self.leader_returncode is not None:
            raise ValueError("a running process cannot have a leader return code")
        if self.group_drained and not all(present):
            raise ValueError("a drained process group requires its recorded process identities")
        if not any(present) and self.leader_returncode is not None:
            raise ValueError("a leader return code requires recorded process identities")
        return self


class RecoveryInventoryV1(_ContractModel):
    """Versioned source facts consumed by recovery operations."""

    contract_version: Literal[1]
    runtime_id: StrictStr
    source_root: Path
    agents_package_root: Path
    maintenance_lock_path: Path
    maintenance_lock_identity: MaintenanceLockIdentityV1
    resources: tuple[SourceResourceV1, ...]
    configs: tuple[SourceConfigV1, ...]
    field_bindings: tuple[SourceFieldBindingV1, ...]
    keypairs: tuple[SourceKeypairV1, ...]
    process_ownership: tuple[OwnedProcessV1, ...]

    @field_validator("contract_version", mode="before")
    @classmethod
    def validate_contract_version(cls, value: object) -> object:
        return _require_contract_version(value)

    @field_validator("runtime_id")
    @classmethod
    def validate_runtime_id(cls, value: str) -> str:
        return _require_identifier(value, field_name="runtime_id")

    @field_validator("source_root", "agents_package_root", "maintenance_lock_path")
    @classmethod
    def validate_absolute_path(cls, value: Path, info: object) -> Path:
        field_name = getattr(info, "field_name", "path")
        return _require_canonical_absolute(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_references_and_resolution(self) -> Self:
        resources = _unique_by_id(self.resources, "resource", id_field="logical_id")
        configs = _unique_by_id(self.configs, "config", id_field="logical_id")
        _unique_by_id(self.field_bindings, "binding", id_field="binding_id")
        _unique_by_id(self.process_ownership, "process", id_field="logical_id")
        self._validate_resource_topology()
        self._validate_keypairs(resources)

        config_field_pairs: set[tuple[str, tuple[str | int, ...]]] = set()
        for binding in self.field_bindings:
            pair = (binding.config_id, tuple(binding.field_path))
            if pair in config_field_pairs:
                raise ValueError("config and field_path pairs must be unique")
            config_field_pairs.add(pair)

            config = configs.get(binding.config_id)
            if config is None:
                raise ValueError(f"binding references unknown config: {binding.config_id}")
            if binding.binding_kind is BindingKind.FILESYSTEM:
                self._validate_filesystem_binding(binding, config, resources)

        for process in self.process_ownership:
            if process.config_id is not None:
                config = configs.get(process.config_id)
                if config is None:
                    raise ValueError(f"process references unknown config: {process.config_id}")
                if process.process_cwd != config.process_cwd:
                    raise ValueError("process_cwd must match the referenced config process_cwd")
            if (
                process.agents_package_root is not None
                and process.agents_package_root != self.agents_package_root
            ):
                raise ValueError(
                    "process agents_package_root must match the inventory agents_package_root"
                )
        return self

    def _validate_resource_topology(self) -> None:
        shape_by_path: dict[Path, tuple[ResourceKind, CaptureKind]] = {}
        runtime_locks: list[SourceResourceV1] = []
        for resource in self.resources:
            shape = (resource.kind, resource.capture_kind)
            prior_shape = shape_by_path.setdefault(resource.captured_path, shape)
            if prior_shape != shape:
                raise ValueError(
                    "resources sharing a captured_path must agree on kind and capture_kind"
                )
            if resource.capture_kind is CaptureKind.RUNTIME_LOCK:
                runtime_locks.append(resource)

        if len(runtime_locks) > 1:
            raise ValueError("inventory may contain at most one runtime lock resource")
        if runtime_locks and runtime_locks[0].captured_path != self.maintenance_lock_path:
            raise ValueError("runtime lock resource path must match maintenance_lock_path")

    def _validate_keypairs(self, resources: dict[str, SourceResourceV1]) -> None:
        handles: set[str] = set()
        referenced_resources: set[str] = set()
        economic_identity_by_fingerprint: dict[
            str, tuple[CapturedRegistrationState, str | None]
        ] = {}
        registered_fingerprint_by_agent_id: dict[str, str] = {}
        economic_fingerprints: set[str] = set()
        gateway_fingerprints: set[str] = set()
        for keypair in self.keypairs:
            if keypair.handle in handles:
                raise ValueError(f"duplicate keypair handle: {keypair.handle}")
            handles.add(keypair.handle)

            pair_references = (keypair.private_resource_id, keypair.public_resource_id)
            if any(reference in referenced_resources for reference in pair_references):
                raise ValueError("keypair resources may be referenced by only one keypair")
            referenced_resources.update(pair_references)

            private_resource = resources.get(keypair.private_resource_id)
            public_resource = resources.get(keypair.public_resource_id)
            if private_resource is None or public_resource is None:
                raise ValueError("keypair references an unknown resource")
            self._validate_key_resource(private_resource, keypair.handle, suffix=".key")
            self._validate_key_resource(public_resource, keypair.handle, suffix=".pub")
            if private_resource.captured_path.parent != public_resource.captured_path.parent:
                raise ValueError("private and public key resources must share one parent directory")

            if keypair.role is KeypairRole.ECONOMIC:
                economic_fingerprints.add(keypair.expected_public_fingerprint_sha256)
                identity = (
                    keypair.captured_registration_state,
                    keypair.expected_registered_agent_id,
                )
                prior_identity = economic_identity_by_fingerprint.setdefault(
                    keypair.expected_public_fingerprint_sha256,
                    identity,
                )
                if prior_identity != identity:
                    raise ValueError(
                        "economic key aliases with one public fingerprint must agree "
                        "on captured registration identity"
                    )
            else:
                fingerprint = keypair.expected_public_fingerprint_sha256
                if fingerprint in gateway_fingerprints:
                    raise ValueError("gateway key fingerprints must be unique")
                gateway_fingerprints.add(fingerprint)

            if keypair.captured_registration_state is CapturedRegistrationState.REGISTERED:
                agent_id = cast("str", keypair.expected_registered_agent_id)
                prior_fingerprint = registered_fingerprint_by_agent_id.setdefault(
                    agent_id,
                    keypair.expected_public_fingerprint_sha256,
                )
                if prior_fingerprint != keypair.expected_public_fingerprint_sha256:
                    raise ValueError(
                        "one registered agent_id cannot map to multiple public fingerprints"
                    )

        if economic_fingerprints & gateway_fingerprints:
            raise ValueError("gateway and economic key fingerprints must be disjoint")

    @staticmethod
    def _validate_key_resource(resource: SourceResourceV1, handle: str, *, suffix: str) -> None:
        if resource.kind is not ResourceKind.FILE:
            raise ValueError("keypair resources must be files")
        if resource.capture_kind is not CaptureKind.KEYPAIR:
            raise ValueError("keypair resources must use keypair capture intent")
        if resource.captured_path.name != f"{handle}{suffix}":
            raise ValueError("keypair resource filename does not match its canonical handle")

    def _validate_filesystem_binding(
        self,
        binding: SourceFieldBindingV1,
        config: SourceConfigV1,
        resources: dict[str, SourceResourceV1],
    ) -> None:
        captured_value = cast("str", binding.captured_value)
        resolution_base = cast("PathResolutionBase", binding.resolution_base)
        resolved_source_path = cast("Path", binding.resolved_source_path)
        resource_id = cast("str", binding.resource_id)

        resource = resources.get(resource_id)
        if resource is None:
            raise ValueError(f"binding references unknown resource: {resource_id}")
        if resource.captured_path != resolved_source_path:
            raise ValueError("binding resolved_source_path must match its logical resource path")

        captured_path = Path(captured_value)
        if captured_path.is_absolute():
            if resolution_base is not PathResolutionBase.ABSOLUTE:
                raise ValueError("absolute captured path requires the absolute resolution base")
            expected = captured_path
        else:
            if resolution_base is PathResolutionBase.ABSOLUTE:
                raise ValueError("relative captured path cannot use the absolute resolution base")
            bases = {
                PathResolutionBase.PROCESS_CWD: config.process_cwd,
                PathResolutionBase.CONFIG_PARENT: config.captured_path.parent,
                PathResolutionBase.AGENTS_PACKAGE_ROOT: self.agents_package_root,
            }
            expected = bases[resolution_base] / captured_path

        normalized_expected = Path(os.path.normpath(str(expected)))
        if normalized_expected != resolved_source_path:
            raise ValueError(
                "resolved_source_path is inconsistent with its captured resolution base"
            )


class TargetBindingsV1(_ContractModel):
    """Explicit paths and endpoint values for a fresh recovery target."""

    contract_version: Literal[1]
    target_root: Path
    process_cwds: Mapping[StrictStr, Path]
    agents_package_root: Path
    resource_paths: Mapping[StrictStr, Path]
    endpoint_values: Mapping[StrictStr, CapturedValue]

    @field_validator("contract_version", mode="before")
    @classmethod
    def validate_contract_version(cls, value: object) -> object:
        return _require_contract_version(value)

    @field_validator("target_root", "agents_package_root")
    @classmethod
    def validate_absolute_path(cls, value: Path, info: object) -> Path:
        field_name = getattr(info, "field_name", "path")
        return _require_canonical_absolute(value, field_name=field_name)

    @field_validator("process_cwds", "resource_paths")
    @classmethod
    def freeze_path_mapping(cls, value: Mapping[str, Path]) -> Mapping[str, Path]:
        return _freeze_mapping(value)

    @field_validator("endpoint_values")
    @classmethod
    def freeze_endpoint_mapping(
        cls, value: Mapping[str, CapturedValue]
    ) -> Mapping[str, CapturedValue]:
        return _freeze_mapping(value)

    @field_serializer("process_cwds", "resource_paths")
    def serialize_path_mapping(self, value: Mapping[str, Path]) -> dict[str, Path]:
        return dict(value)

    @field_serializer("endpoint_values")
    def serialize_endpoint_mapping(
        self, value: Mapping[str, CapturedValue]
    ) -> dict[str, CapturedValue]:
        return dict(value)

    @model_validator(mode="after")
    def validate_target_bindings(self) -> Self:
        for logical_id, process_cwd in self.process_cwds.items():
            _require_identifier(logical_id, field_name="process_cwds key")
            _require_canonical_absolute(process_cwd, field_name="process_cwd")
        for logical_id, resource_path in self.resource_paths.items():
            _require_identifier(logical_id, field_name="resource_paths key")
            _require_canonical_absolute(resource_path, field_name="resource_path")
            if resource_path == self.target_root or not resource_path.is_relative_to(
                self.target_root
            ):
                raise ValueError("target resource paths must be beneath target_root")
        for binding_id, value in self.endpoint_values.items():
            _require_identifier(binding_id, field_name="endpoint_values key")
            if isinstance(value, str) and not value:
                raise ValueError("endpoint string values must be non-empty")
            if isinstance(value, tuple) and any(not item for item in value):
                raise ValueError("endpoint tuple values must contain only non-empty strings")
        return self


def _unique_by_id[ItemT: object](
    items: tuple[ItemT, ...],
    item_name: str,
    *,
    id_field: str,
) -> dict[str, ItemT]:
    indexed: dict[str, ItemT] = {}
    for item in items:
        identifier = getattr(item, id_field)
        if identifier in indexed:
            raise ValueError(f"duplicate {item_name} identifier: {identifier}")
        indexed[identifier] = item
    return indexed


CanonicalRecoveryModel = RecoveryInventoryV1 | TargetBindingsV1


def canonical_json_bytes(model: CanonicalRecoveryModel) -> bytes:
    """Serialize a recovery contract to stable UTF-8 JSON bytes."""

    payload = model.model_dump(mode="json")
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(model: CanonicalRecoveryModel) -> str:
    """Return the SHA-256 hex digest of canonical recovery-contract JSON."""

    return hashlib.sha256(canonical_json_bytes(model)).hexdigest()
