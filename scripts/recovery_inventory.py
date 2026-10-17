"""Build a neutral recovery inventory from one fully configured source runtime."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from scripts.recovery_fields import CapturedField, configuration_fields
from service_auth import snapshot_keypairs
from service_commons.recovery_contract import (
    BindingKind,
    CapturedRegistrationState,
    CaptureKind,
    KeypairRole,
    MaintenanceLockIdentityV1,
    OwnedProcessV1,
    RecoveryInventoryV1,
    RegistrationRequirement,
    ResourceKind,
    SourceConfigV1,
    SourceFieldBindingV1,
    SourceKeypairV1,
    SourceResourceV1,
)

_SERVICE_IDS = frozenset(
    {
        "db-gateway",
        "identity",
        "central-bank",
        "task-board",
        "reputation",
        "court",
        "ui",
    }
)
_CONFIG_IDS = _SERVICE_IDS | {"agents"}
_PROTECTED_CONFIG_IDS = frozenset({"db-gateway", "task-board", "ui"})


class _Resources:
    def __init__(self, *, source_root: Path, code_root: Path) -> None:
        self._by_id: dict[str, SourceResourceV1] = {}
        self._id_by_path: dict[Path, str] = {}
        self._capture_root_by_id: dict[str, Path] = {}
        self._source_root = source_root
        self._code_root = code_root

    def add(
        self,
        logical_id: str,
        path: Path,
        *,
        kind: ResourceKind,
        capture_kind: CaptureKind,
    ) -> str:
        capture_root = self._capture_root(logical_id, path)
        resource = SourceResourceV1(
            logical_id=logical_id,
            kind=kind,
            capture_kind=capture_kind,
            capture_root=capture_root,
            captured_path=path,
        )
        current = self._by_id.get(logical_id)
        if current is not None:
            if current != resource:
                raise ValueError(f"Recovery resource {logical_id} has inconsistent source paths")
            return logical_id
        path_owner = self._id_by_path.get(path)
        if path_owner is not None:
            raise ValueError(
                f"Recovery resources {path_owner} and {logical_id} unexpectedly share {path}"
            )
        self._by_id[logical_id] = resource
        self._id_by_path[path] = logical_id
        self._capture_root_by_id[logical_id] = capture_root
        return logical_id

    def values(self) -> tuple[SourceResourceV1, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def validate_capture_path(self, logical_id: str, path: Path) -> None:
        self._capture_root(logical_id, path)

    def _capture_root(self, logical_id: str, path: Path) -> Path:
        if _is_within(path, self._source_root):
            return self._source_root
        if logical_id in {"database.schema", "frontend.web"} and _is_within(
            path,
            self._code_root,
        ):
            return self._code_root
        raise ValueError(
            f"Recovery resource {logical_id} must stay within source_root"
            + (" or code_root" if logical_id in {"database.schema", "frontend.web"} else "")
        )


def _canonical_absolute(path: Path, *, label: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError(f"{label} must be an absolute pathlib.Path")
    normalized = Path(os.path.normpath(path))
    if normalized != path:
        raise ValueError(f"{label} must be lexically normalized")
    return path


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_real_ancestors(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for component in path.parent.parts[1:]:
        current /= component
        try:
            current_stat = current.lstat()
        except FileNotFoundError as exc:
            raise ValueError(f"{label} parent does not exist: {current}") from exc
        if stat.S_ISLNK(current_stat.st_mode):
            raise ValueError(f"{label} contains a symlink ancestor: {current}")
        if not stat.S_ISDIR(current_stat.st_mode):
            raise ValueError(f"{label} ancestor is not a directory: {current}")


def _close_descriptors(descriptors: list[int]) -> OSError | None:
    first_error: OSError | None = None
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError as exc:
            if first_error is None:
                first_error = exc
            else:
                first_error.add_note(f"Additional descriptor close failure: {exc}")
    return first_error


def _read_pinned_source_file(path: Path, *, label: str, descriptors: list[int]) -> bytes:
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_descriptor = os.open(path.anchor, directory_flags)
    except OSError as exc:
        raise ValueError(f"{label} root directory is unavailable") from exc
    descriptors.append(root_descriptor)
    parent_descriptor = root_descriptor
    for component in path.parent.relative_to(path.anchor).parts:
        try:
            descriptor = os.open(component, directory_flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise ValueError(
                f"{label} contains a symlink, missing, or non-directory ancestor: {component}"
            ) from exc
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError(f"{label} ancestor is not a directory: {component}")
        descriptors.append(descriptor)
        parent_descriptor = descriptor

    try:
        before = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise ValueError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular file: {path}")

    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path.name, file_flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise ValueError(f"{label} changed while it was opened: {path}") from exc
    descriptors.append(descriptor)
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
        raise ValueError(f"{label} changed while it was opened: {path}")
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    after_open = os.fstat(descriptor)
    try:
        after_path = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} disappeared while it was read: {path}") from exc
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(getattr(before, field) != getattr(after_open, field) for field in stable_fields) or any(
        getattr(after_open, field) != getattr(after_path, field) for field in stable_fields
    ):
        raise ValueError(f"{label} changed while it was read: {path}")
    return b"".join(chunks)


def read_recovery_source_file(path: Path, *, label: str) -> bytes:
    """Read one regular source file through a descriptor-pinned no-follow path."""
    _canonical_absolute(path, label=label)
    if not path.name:
        raise ValueError(f"{label} must identify a regular file")
    descriptors: list[int] = []
    try:
        content = _read_pinned_source_file(path, label=label, descriptors=descriptors)
    except BaseException as primary:
        cleanup_error = _close_descriptors(descriptors)
        if cleanup_error is not None:
            primary.add_note(f"Descriptor cleanup also failed: {cleanup_error}")
        raise
    cleanup_error = _close_descriptors(descriptors)
    if cleanup_error is not None:
        raise ValueError(f"{label} descriptor cleanup failed") from cleanup_error
    return content


def _parse_yaml_mapping(content: bytes, *, label: str) -> dict[str, object]:
    try:
        value: object = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"{label} is not valid YAML") from exc
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise ValueError(f"{label} must be a string-keyed YAML mapping")
    return {cast("str", key): item for key, item in value.items()}


def _mapping(value: Mapping[str, object], key: str, *, label: str) -> Mapping[str, object]:
    nested = value.get(key)
    if not isinstance(nested, Mapping) or any(type(item) is not str for item in nested):
        raise ValueError(f"{label}.{key} must be a string-keyed mapping")
    return cast("Mapping[str, object]", nested)


def _required_string(value: Mapping[str, object], key: str, *, label: str) -> str:
    item = value.get(key)
    if type(item) is not str or not item or item.strip() != item:
        raise ValueError(f"{label}.{key} must be a nonempty string")
    return item


def _resource_capture_kind(
    path: Path,
    *,
    kind: ResourceKind,
    required: bool,
    capture_kind: CaptureKind,
    label: str,
) -> CaptureKind:
    _canonical_absolute(path, label=label)
    _validate_real_ancestors(path, label=label)
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        if not required and kind is ResourceKind.FILE:
            return CaptureKind.ABSENT_FILE
        raise ValueError(f"Required recovery resource does not exist: {label}") from None
    if stat.S_ISLNK(path_stat.st_mode):
        raise ValueError(f"Recovery resource must not be a symlink: {label}")
    expected = stat.S_ISREG if kind is ResourceKind.FILE else stat.S_ISDIR
    if not expected(path_stat.st_mode):
        shape = "file" if kind is ResourceKind.FILE else "directory"
        raise ValueError(f"Recovery resource must be a {shape}: {label}")
    return capture_kind


def _field_key(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)


def _key_id(handle: str, suffix: str) -> str:
    return f"key.{handle}.{suffix}"


def _gateway_private_handle(config: Mapping[str, object]) -> str:
    db_gateway = _mapping(config, "db_gateway", label="service config")
    signing = _mapping(db_gateway, "signing", label="service config.db_gateway")
    return _required_string(signing, "kid", label="service config.db_gateway.signing")


def _binding_resource(  # noqa: PLR0911, PLR0912
    config_id: str,
    field: CapturedField,
    config: Mapping[str, object],
    logging_resource_ids: Mapping[str, str],
) -> tuple[str, ResourceKind, CaptureKind, bool]:
    path = field.field_path
    if path == ("logging", "directory"):
        return (
            logging_resource_ids[config_id],
            ResourceKind.DIRECTORY,
            CaptureKind.DIRECTORY_ONLY,
            True,
        )
    if path == ("recovery", "maintenance_lock_path"):
        return "runtime.maintenance_lock", ResourceKind.FILE, CaptureKind.RUNTIME_LOCK, True
    if path == ("database", "path"):
        if config_id in {"db-gateway", "ui"}:
            return "database.primary", ResourceKind.FILE, CaptureKind.COPY, True
        if config_id == "task-board":
            return "database.legacy.task-board", ResourceKind.FILE, CaptureKind.COPY, False
    if config_id == "db-gateway" and path == ("database", "schema_path"):
        return "database.schema", ResourceKind.FILE, CaptureKind.COPY, True
    if config_id == "db-gateway" and path == ("recovery", "backup_root"):
        return "recovery.backups", ResourceKind.DIRECTORY, CaptureKind.DIRECTORY_ONLY, True
    if (
        config_id == "db-gateway"
        and len(path) == 4
        and path[:2] == ("auth", "trusted_callers")
        and path[3] == "public_key_path"
    ):
        handle = cast("str", path[2])
        return _key_id(handle, "public"), ResourceKind.FILE, CaptureKind.KEYPAIR, True
    if path == ("db_gateway", "signing", "private_key_path"):
        handle = _gateway_private_handle(config)
        return _key_id(handle, "private"), ResourceKind.FILE, CaptureKind.KEYPAIR, True
    if path == ("platform", "agent_config_path") or (
        config_id == "ui" and path == ("user_agent", "agent_config_path")
    ):
        return "config.agents", ResourceKind.FILE, CaptureKind.CONFIG, True
    if config_id == "task-board" and path == ("assets", "storage_path"):
        return "assets.root", ResourceKind.DIRECTORY, CaptureKind.COPY, True
    if config_id == "ui" and path == ("frontend", "web_root"):
        return "frontend.web", ResourceKind.DIRECTORY, CaptureKind.COPY, True
    if config_id == "agents" and path == ("data", "keys_dir"):
        return "keys.root", ResourceKind.DIRECTORY, CaptureKind.DIRECTORY_ONLY, True
    if config_id == "agents" and path == ("data", "roster_path"):
        return "agents.roster", ResourceKind.FILE, CaptureKind.COPY, True
    if config_id == "agents" and path == ("task_feeder", "tasks_file"):
        return "agents.tasks", ResourceKind.FILE, CaptureKind.COPY, False
    raise ValueError(
        f"Filesystem field has no recovery resource policy: {config_id}.{_field_key(path)}"
    )


def _canonical_registry(registry_bindings: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(registry_bindings, Mapping):
        raise TypeError("registry_bindings must be a mapping")
    registry: dict[str, str] = {}
    agent_ids: set[str] = set()
    for public_key, agent_id in registry_bindings.items():
        if type(public_key) is not str or not public_key.startswith("ed25519:"):
            raise ValueError("registry keys must use canonical Ed25519 public keys")
        payload = public_key.removeprefix("ed25519:")
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("registry keys must use canonical Ed25519 public keys") from exc
        if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode("ascii") != payload:
            raise ValueError("registry keys must use canonical Ed25519 public keys")
        try:
            Ed25519PublicKey.from_public_bytes(raw)
        except ValueError as exc:
            raise ValueError("registry keys must use canonical Ed25519 public keys") from exc
        if (
            type(agent_id) is not str
            or not agent_id
            or agent_id in {".", ".."}
            or any(
                character.isspace() or ord(character) < 32 or character in "/\\"
                for character in agent_id
            )
        ):
            raise ValueError("registry agent IDs must be canonical path-safe identifiers")
        if agent_id in agent_ids:
            raise ValueError("registry agent IDs must be unique")
        agent_ids.add(agent_id)
        registry[public_key] = agent_id
    return registry


def _snapshot_public_key(public_pem: bytes) -> str:
    public_key = serialization.load_pem_public_key(public_pem)
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("Captured keypair public key is not Ed25519")
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return f"ed25519:{base64.b64encode(raw).decode('ascii')}"


def _economic_handles(
    agent_config: Mapping[str, object],
    ui_config: Mapping[str, object],
    roster: Mapping[str, object],
) -> tuple[str, ...]:
    agents = _mapping(roster, "agents", label="agent roster")
    handles = set(agents)
    referenced = {
        _required_string(
            _mapping(agent_config, "treasury", label="agents config"),
            "handle",
            label="agents config.treasury",
        ),
        _required_string(
            _mapping(agent_config, "task_feeder", label="agents config"),
            "handle",
            label="agents config.task_feeder",
        ),
        _required_string(
            _mapping(ui_config, "user_agent", label="ui config"),
            "handle",
            label="ui config.user_agent",
        ),
    }
    workers = _mapping(agent_config, "workers", label="agents config")
    for profile, raw_worker in workers.items():
        if not isinstance(raw_worker, Mapping):
            raise ValueError(f"agents config worker {profile} must be a mapping")
        referenced.add(
            _required_string(
                cast("Mapping[str, object]", raw_worker),
                "handle",
                label=f"agents config workers.{profile}",
            )
        )
    missing = referenced - handles
    if missing:
        raise ValueError(f"Configured economic handles are absent from roster: {sorted(missing)}")
    return tuple(sorted(handles))


def _validate_config_selection(
    config_paths: Mapping[str, Path], process_cwds: Mapping[str, Path]
) -> tuple[dict[str, Path], dict[str, Path]]:
    if frozenset(config_paths) != _CONFIG_IDS or frozenset(process_cwds) != _CONFIG_IDS:
        raise ValueError(
            "Recovery inventory currently requires one full seven-service runtime plus agents"
        )
    paths = {
        config_id: _canonical_absolute(path, label=f"config_paths[{config_id}]")
        for config_id, path in config_paths.items()
    }
    cwds = {
        config_id: _canonical_absolute(path, label=f"process_cwds[{config_id}]")
        for config_id, path in process_cwds.items()
    }
    if len(set(paths.values())) != len(paths):
        raise ValueError("Every selected recovery config must have a distinct source path")
    return paths, cwds


def _logging_resource_ids(
    fields_by_config: Mapping[str, tuple[CapturedField, ...]],
) -> dict[str, str]:
    """Name distinct log roots deterministically while preserving path deduplication."""
    resource_id_by_path: dict[Path, str] = {}
    resource_id_by_config: dict[str, str] = {}
    for config_id in sorted(_SERVICE_IDS):
        logging_fields = [
            field
            for field in fields_by_config[config_id]
            if field.field_path == ("logging", "directory")
        ]
        if len(logging_fields) != 1:
            raise ValueError(f"Config {config_id} must expose exactly one logging.directory")
        logging_path = cast("Path", logging_fields[0].resolved_source_path)
        resource_id = resource_id_by_path.get(logging_path)
        if resource_id is None:
            resource_id = "logs.root" if not resource_id_by_path else f"logs.{config_id}"
            resource_id_by_path[logging_path] = resource_id
        resource_id_by_config[config_id] = resource_id
    return resource_id_by_config


def _validate_process_ownership(
    process_ownership: tuple[OwnedProcessV1, ...],
    process_cwds: Mapping[str, Path],
) -> None:
    records_by_service: dict[str, list[OwnedProcessV1]] = {service: [] for service in _SERVICE_IDS}
    for record in process_ownership:
        if record.config_id in _SERVICE_IDS:
            records_by_service[record.config_id].append(record)
            continue
        if record.config_id == "agents":
            if record.process_cwd != process_cwds["agents"]:
                raise ValueError("agents process_ownership process_cwd must match its config")
            continue
        if record.config_id is not None:
            raise ValueError(f"Unknown process_ownership config_id: {record.config_id}")

    for service in sorted(_SERVICE_IDS):
        records = records_by_service[service]
        if len(records) != 1:
            raise ValueError(
                f"process_ownership requires exactly one record for service config {service}"
            )
        if records[0].process_cwd != process_cwds[service]:
            raise ValueError(f"{service} process_ownership process_cwd must match its config")


def build_recovery_inventory(  # noqa: PLR0912, PLR0915
    *,
    runtime_id: str,
    source_root: Path,
    config_paths: Mapping[str, Path],
    process_cwds: Mapping[str, Path],
    agents_package_root: Path,
    code_root: Path,
    maintenance_lock_identity: MaintenanceLockIdentityV1,
    registry_bindings: Mapping[str, str],
    required_registered_handles: frozenset[str],
    process_ownership: tuple[OwnedProcessV1, ...],
) -> RecoveryInventoryV1:
    """Capture recovery facts without opening the database or contacting services."""
    source_root = _canonical_absolute(source_root, label="source_root")
    agents_package_root = _canonical_absolute(
        agents_package_root,
        label="agents_package_root",
    )
    code_root = _canonical_absolute(code_root, label="code_root")
    if not _is_within(agents_package_root, code_root):
        raise ValueError("agents_package_root must be within code_root")
    paths, cwds = _validate_config_selection(config_paths, process_cwds)
    registry = _canonical_registry(registry_bindings)
    if not isinstance(required_registered_handles, frozenset) or any(
        type(handle) is not str or not handle for handle in required_registered_handles
    ):
        raise TypeError("required_registered_handles must be a frozenset of nonempty strings")
    if not isinstance(process_ownership, tuple) or any(
        not isinstance(process, OwnedProcessV1) for process in process_ownership
    ):
        raise TypeError("process_ownership must be a tuple of OwnedProcessV1 records")
    _validate_process_ownership(process_ownership, cwds)

    resources = _Resources(source_root=source_root, code_root=code_root)
    configs: list[SourceConfigV1] = []
    parsed_configs: dict[str, dict[str, object]] = {}
    for config_id in sorted(_CONFIG_IDS):
        config_path = paths[config_id]
        if not _is_within(config_path, source_root):
            raise ValueError(f"config.{config_id} must stay within source_root")
        content = read_recovery_source_file(config_path, label=f"config {config_id}")
        parsed_configs[config_id] = _parse_yaml_mapping(
            content,
            label=f"config {config_id}",
        )
        configs.append(
            SourceConfigV1(
                logical_id=config_id,
                capture_root=source_root,
                captured_path=config_path,
                process_cwd=cwds[config_id],
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
        resources.add(
            f"config.{config_id}",
            config_path,
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
        )

    bindings: list[SourceFieldBindingV1] = []
    gateway_public_paths: dict[str, Path] = {}
    gateway_private_paths: dict[str, Path] = {}
    fields_by_config = {
        config_id: configuration_fields(
            config_id,
            parsed_configs[config_id],
            config_path=paths[config_id],
            process_cwd=cwds[config_id],
            agents_package_root=agents_package_root,
        )
        for config_id in sorted(_CONFIG_IDS)
    }
    logging_resource_ids = _logging_resource_ids(fields_by_config)
    for config_id in sorted(_CONFIG_IDS):
        config = parsed_configs[config_id]
        for field in fields_by_config[config_id]:
            resource_id: str | None = None
            if field.binding_kind is BindingKind.FILESYSTEM:
                resolved_path = cast("Path", field.resolved_source_path)
                logical_id, kind, capture_kind, required = _binding_resource(
                    config_id,
                    field,
                    config,
                    logging_resource_ids,
                )
                resources.validate_capture_path(logical_id, resolved_path)
                selected_capture = _resource_capture_kind(
                    resolved_path,
                    kind=kind,
                    required=required,
                    capture_kind=capture_kind,
                    label=logical_id,
                )
                resource_id = resources.add(
                    logical_id,
                    resolved_path,
                    kind=kind,
                    capture_kind=selected_capture,
                )
                if logical_id.startswith("key."):
                    _record_gateway_key_path(
                        logical_id,
                        resolved_path,
                        gateway_public_paths=gateway_public_paths,
                        gateway_private_paths=gateway_private_paths,
                    )
            bindings.append(
                SourceFieldBindingV1(
                    binding_id=f"{config_id}:{_field_key(field.field_path)}",
                    config_id=config_id,
                    field_path=field.field_path,
                    binding_kind=field.binding_kind,
                    captured_value=field.captured_value,
                    resolution_base=field.resolution_base,
                    resolved_source_path=field.resolved_source_path,
                    resource_id=resource_id,
                )
            )

    maintenance_path = _single_shared_path(
        resources,
        bindings,
        logical_id="runtime.maintenance_lock",
        expected_configs=_PROTECTED_CONFIG_IDS,
    )
    lock_stat = maintenance_path.lstat()
    if (lock_stat.st_dev, lock_stat.st_ino) != (
        maintenance_lock_identity.device,
        maintenance_lock_identity.inode,
    ):
        raise ValueError("runtime.maintenance_lock identity changed before inventory capture")
    _single_shared_path(
        resources,
        bindings,
        logical_id="database.primary",
        expected_configs=frozenset({"db-gateway", "ui"}),
    )

    roster_path = _resource_path(resources, "agents.roster")
    roster_content = read_recovery_source_file(roster_path, label="agent roster")
    roster = _parse_yaml_mapping(roster_content, label="agent roster")
    economic_handles = _economic_handles(
        parsed_configs["agents"],
        parsed_configs["ui"],
        roster,
    )
    if not required_registered_handles <= set(economic_handles):
        unknown = sorted(required_registered_handles - set(economic_handles))
        raise ValueError(f"Required registered handles are absent from roster: {unknown}")

    if set(gateway_public_paths) != set(gateway_private_paths):
        missing_public = sorted(set(gateway_private_paths) - set(gateway_public_paths))
        missing_private = sorted(set(gateway_public_paths) - set(gateway_private_paths))
        raise ValueError(
            "Gateway trust pairs are incomplete: "
            f"missing_public={missing_public} missing_private={missing_private}"
        )
    overlap = set(economic_handles) & set(gateway_public_paths)
    if overlap:
        raise ValueError(f"Gateway handles collide with economic roster handles: {sorted(overlap)}")

    keys_root = _resource_path(resources, "keys.root")
    economic_public_paths = {handle: keys_root / f"{handle}.pub" for handle in economic_handles}
    for handle, public_path in economic_public_paths.items():
        resources.validate_capture_path(_key_id(handle, "public"), public_path)
        _resource_capture_kind(
            public_path,
            kind=ResourceKind.FILE,
            required=True,
            capture_kind=CaptureKind.KEYPAIR,
            label=_key_id(handle, "public"),
        )
        private_path = keys_root / f"{handle}.key"
        resources.validate_capture_path(_key_id(handle, "private"), private_path)
        _resource_capture_kind(
            private_path,
            kind=ResourceKind.FILE,
            required=True,
            capture_kind=CaptureKind.KEYPAIR,
            label=_key_id(handle, "private"),
        )
        resources.add(
            _key_id(handle, "private"),
            private_path,
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
        )
        resources.add(
            _key_id(handle, "public"),
            public_path,
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
        )

    for handle, public_path in gateway_public_paths.items():
        if public_path.name != f"{handle}.pub":
            raise ValueError(f"Gateway public-key path does not match KID {handle}")
        expected_private = public_path.parent / f"{handle}.key"
        if gateway_private_paths[handle] != expected_private:
            raise ValueError(f"Gateway private-key path does not match public pair for {handle}")

    all_public_paths = {**economic_public_paths, **gateway_public_paths}
    snapshots = snapshot_keypairs(
        tuple(sorted(all_public_paths.items())),
        blocking=True,
    )
    snapshot_by_handle = {snapshot.handle: snapshot for snapshot in snapshots}
    keypairs: list[SourceKeypairV1] = []
    for handle in sorted(snapshot_by_handle):
        snapshot = snapshot_by_handle[handle]
        canonical_public = _snapshot_public_key(snapshot.public_pem)
        registered_agent_id = registry.get(canonical_public)
        is_gateway = handle in gateway_public_paths
        if is_gateway and registered_agent_id is not None:
            raise ValueError(f"Gateway key {handle} must not be registered as an economic identity")
        if handle in required_registered_handles and registered_agent_id is None:
            raise ValueError(f"Required economic handle is not registered: {handle}")
        state = (
            CapturedRegistrationState.REGISTERED
            if registered_agent_id is not None
            else CapturedRegistrationState.UNREGISTERED
        )
        keypairs.append(
            SourceKeypairV1(
                handle=handle,
                role=KeypairRole.GATEWAY if is_gateway else KeypairRole.ECONOMIC,
                private_resource_id=_key_id(handle, "private"),
                public_resource_id=_key_id(handle, "public"),
                expected_public_fingerprint_sha256=snapshot.public_fingerprint_sha256,
                registration_requirement=(
                    RegistrationRequirement.FORBIDDEN
                    if is_gateway
                    else RegistrationRequirement.REQUIRED
                    if handle in required_registered_handles
                    else RegistrationRequirement.OPTIONAL
                ),
                captured_registration_state=state,
                expected_registered_agent_id=registered_agent_id,
            )
        )

    return RecoveryInventoryV1(
        contract_version=1,
        runtime_id=runtime_id,
        source_root=source_root,
        agents_package_root=agents_package_root,
        maintenance_lock_path=maintenance_path,
        maintenance_lock_identity=maintenance_lock_identity,
        resources=resources.values(),
        configs=tuple(configs),
        field_bindings=tuple(bindings),
        keypairs=tuple(keypairs),
        process_ownership=process_ownership,
    )


def _record_gateway_key_path(
    logical_id: str,
    path: Path,
    *,
    gateway_public_paths: dict[str, Path],
    gateway_private_paths: dict[str, Path],
) -> None:
    prefix, handle, suffix = logical_id.split(".", maxsplit=2)
    if prefix != "key" or suffix not in {"public", "private"}:
        raise ValueError(f"Invalid Gateway key resource ID: {logical_id}")
    target = gateway_public_paths if suffix == "public" else gateway_private_paths
    current = target.setdefault(handle, path)
    if current != path:
        raise ValueError(f"Gateway key {handle} has inconsistent {suffix} paths")


def _resource_path(resources: _Resources, logical_id: str) -> Path:
    for resource in resources.values():
        if resource.logical_id == logical_id:
            return resource.captured_path
    raise ValueError(f"Missing recovery resource: {logical_id}")


def _single_shared_path(
    resources: _Resources,
    bindings: list[SourceFieldBindingV1],
    *,
    logical_id: str,
    expected_configs: frozenset[str],
) -> Path:
    selected = [binding for binding in bindings if binding.resource_id == logical_id]
    actual_configs = {binding.config_id for binding in selected}
    if frozenset(actual_configs) != expected_configs:
        raise ValueError(
            f"Recovery resource {logical_id} must be shared by {sorted(expected_configs)}"
        )
    paths = {binding.resolved_source_path for binding in selected}
    if len(paths) != 1:
        raise ValueError(f"Recovery resource {logical_id} has inconsistent source paths")
    resource_path = _resource_path(resources, logical_id)
    if paths != {resource_path}:
        raise ValueError(f"Recovery resource {logical_id} bindings do not match its source path")
    return resource_path
