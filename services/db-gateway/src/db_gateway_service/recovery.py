"""Offline immutable backup and fresh-target restore primitives.

This module consumes only the neutral recovery contracts from service-commons.  It
does not discover processes, signal recorded PIDs, import another service package,
or infer resources from a backup identifier.
"""

from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import uuid
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

from service_auth import (
    KeyMaterialError,
    load_private_key,
    load_public_key,
    public_key_to_b64,
    snapshot_keypairs,
)
from service_commons.config import ConfigurationError, parse_yaml_config_bytes
from service_commons.maintenance_lock import (
    HeldMaintenanceLock,
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_existing_maintenance_lock,
)
from service_commons.recovery_contract import (
    BindingKind,
    CapturedRegistrationState,
    CaptureKind,
    KeypairRole,
    PathResolutionBase,
    RecoveryInventoryV1,
    RegistrationRequirement,
    ResourceKind,
    SourceFieldBindingV1,
    SourceResourceV1,
    TargetBindingsV1,
    canonical_json_bytes,
)

BUNDLE_FORMAT_VERSION = 1
_DATABASE_RESOURCE_ID = "database.primary"
_ASSET_RESOURCE_ID = "assets.root"
_AGENT_RUNTIME_RESOURCE_ID = "agents.runtime_state"
_AGENT_IDENTITY_NAME = re.compile(r"^[0-9a-f]{64}$")
_AGENT_OWNER_FIELDS = frozenset(
    {
        "schema_version",
        "environment_id",
        "role",
        "profile",
        "handle",
        "registered_agent_id",
        "key_fingerprint",
        "service_urls",
    }
)
_AGENT_OWNER_STRING_FIELDS = frozenset(
    {
        "environment_id",
        "role",
        "profile",
        "handle",
        "registered_agent_id",
    }
)
_AGENT_SERVICE_URL_FIELDS = frozenset(
    {
        "identity_url",
        "bank_url",
        "task_board_url",
        "reputation_url",
        "court_url",
    }
)
_MONEY_STORAGE_QUERIES = (
    (
        "bank_accounts",
        "balance",
        "SELECT COUNT(*) FROM bank_accounts "
        "WHERE balance IS NOT NULL AND typeof(balance) != 'integer'",
    ),
    (
        "bank_transactions",
        "amount",
        "SELECT COUNT(*) FROM bank_transactions "
        "WHERE amount IS NOT NULL AND typeof(amount) != 'integer'",
    ),
    (
        "bank_transactions",
        "balance_after",
        "SELECT COUNT(*) FROM bank_transactions "
        "WHERE balance_after IS NOT NULL AND typeof(balance_after) != 'integer'",
    ),
    (
        "bank_escrow",
        "amount",
        "SELECT COUNT(*) FROM bank_escrow WHERE amount IS NOT NULL AND typeof(amount) != 'integer'",
    ),
    (
        "board_tasks",
        "reward",
        "SELECT COUNT(*) FROM board_tasks WHERE reward IS NOT NULL AND typeof(reward) != 'integer'",
    ),
    (
        "board_bids",
        "amount",
        "SELECT COUNT(*) FROM board_bids WHERE amount IS NOT NULL AND typeof(amount) != 'integer'",
    ),
)


class RecoveryError(RuntimeError):
    """A fail-closed recovery protocol error with a stable reason code."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class PublicationState(StrEnum):
    """Durability outcome after a completion marker publication attempt."""

    COMPLETE = "complete"
    PUBLICATION_UNCERTAIN = "publication_uncertain"


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """Result of publishing a backup or restored target."""

    state: PublicationState
    location: Path
    manifest_sha256: str

    def model_dump(self, *, mode: str) -> dict[str, object]:
        """Return the stable JSON-ready result shape consumed by the recovery CLI."""

        if mode != "json":
            raise ValueError("PublicationResult supports only mode='json'")
        return {
            "state": self.state.value,
            "location": str(self.location),
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Independent recovery-state results recorded by a verified manifest."""

    artifact_verified: bool
    snapshot_preserved: bool
    identity_complete: bool
    migration_eligible: bool
    operational_restore_verified: bool
    finding_codes: tuple[str, ...]
    manifest_sha256: str

    def model_dump(self, *, mode: str) -> dict[str, object]:
        """Return distinct preservation, identity, and migration state for the CLI."""

        if mode != "json":
            raise ValueError("VerificationResult supports only mode='json'")
        return {
            "artifact_verified": self.artifact_verified,
            "snapshot_preserved": self.snapshot_preserved,
            "identity_complete": self.identity_complete,
            "migration_eligible": self.migration_eligible,
            "operational_restore_verified": self.operational_restore_verified,
            "finding_codes": list(self.finding_codes),
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(slots=True)
class _BackupBoundSourceState:
    verified: _VerifiedBackup
    held_lock: HeldMaintenanceLock
    source_database_path: Path
    main_descriptor: int
    main_identity: dict[str, object]
    wal_path: Path
    wal_descriptor: int | None
    wal_identity: dict[str, object]
    workspace: _RecoveryWorkspace
    work_output: _PinnedDirectory
    scratch: _OwnedScratch | None = None
    active: bool = True
    write_phase: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedBoundSource:
    inventory: RecoveryInventoryV1
    verified: _VerifiedBackup
    database_resource: SourceResourceV1
    record: dict[str, Any]
    main_identity: dict[str, object]
    wal_identity: dict[str, object]
    wal_path: Path
    backup_id: str
    work_output: _PinnedDirectory


@dataclass(frozen=True, slots=True)
class BackupBoundSource:
    """Read-only view of a verified backup bound to its exact live source."""

    backup_id: str
    manifest_sha256: str
    artifact_verification_result: VerificationResult
    source_database_path: Path
    _scratch: _OwnedScratch = dataclass_field(repr=False, compare=False)
    _wal_present: bool = dataclass_field(repr=False, compare=False)
    _state: _BackupBoundSourceState = dataclass_field(repr=False, compare=False)

    @property
    def database_path(self) -> Path:
        """Return the descriptor-bound disposable database clone path."""

        _require_active_bound_source(self._state)
        return self._scratch.access_path / "economy.db"

    @property
    def wal_path(self) -> Path | None:
        """Return the descriptor-bound disposable WAL clone path when present."""

        _require_active_bound_source(self._state)
        if not self._wal_present:
            return None
        return self._scratch.access_path / "economy.db-wal"

    def verify_before_write(self) -> None:
        """Recheck raw source bytes, lock identity, and immutable backup."""

        _require_active_bound_source(self._state)
        if self._state.write_phase:
            raise RecoveryError("invalid_phase", "Backup-bound source write phase already began")
        _verify_bound_source(self._state, allow_source_content_changes=False)

    def begin_write_phase(self) -> None:
        """Perform the last exact check and enter the explicit write phase."""

        _require_active_bound_source(self._state)
        if self._state.write_phase:
            raise RecoveryError("invalid_phase", "Backup-bound source write phase already began")
        _verify_bound_source(self._state, allow_source_content_changes=False)
        self._state.write_phase = True

    def verify_during_write(self) -> None:
        """Recheck retained main-file identity, lock, and immutable backup."""

        _require_active_bound_source(self._state)
        if not self._state.write_phase:
            raise RecoveryError("invalid_phase", "Backup-bound source write phase has not begun")
        _verify_bound_source(self._state, allow_source_content_changes=True)

    def write_evidence(self, name: str, payload: bytes) -> Path:
        """Durably retain one private evidence file in the bound scratch directory."""

        _require_active_bound_source(self._state)
        if not self._state.write_phase:
            raise RecoveryError("invalid_phase", "Backup-bound source write phase has not begun")
        if not isinstance(name, str):
            raise TypeError("name must be str")
        _validate_identifier(name, name="evidence name")
        if name.startswith(".") or name in {"economy.db", "economy.db-wal"}:
            raise RecoveryError("invalid_argument", "evidence name is reserved")
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")

        _verify_bound_evidence_authority(self._state, self._scratch)
        evidence_path = _write_new_file_under(
            self._scratch.directory,
            Path(name),
            payload,
            mode=0o600,
        )
        try:
            os.fsync(self._scratch.directory.descriptor)
        except OSError as exc:
            raise RecoveryError("write_failed", "Cannot sync retained evidence directory") from exc
        evidence_bytes, evidence_mode = _read_regular_under(
            self._scratch.directory,
            name,
            label="migration evidence",
        )
        if evidence_bytes != payload or evidence_mode != 0o600:
            raise RecoveryError("write_failed", "Retained migration evidence differs after write")
        _verify_bound_evidence_authority(self._state, self._scratch)
        return evidence_path


@dataclass(slots=True)
class _OpenCopy:
    source_path: Path
    file_descriptor: int
    device: int
    inode: int
    size: int
    mode: int
    sha256: str
    destination: Path


@dataclass(slots=True)
class _CaptureState:
    resources: dict[str, dict[str, object]]
    files: list[dict[str, object]]
    open_copies: list[_OpenCopy]
    absent_resources: list[tuple[str, Path]]
    maintenance_device: int
    maintenance_inode: int
    database_source: Path | None = None
    database_wal_present: bool | None = None
    database_wal_payload: Path | None = None


@dataclass(frozen=True, slots=True)
class _AgentRuntimeIdentity:
    name: str
    directory_device: int
    directory_inode: int
    lock_path: Path
    lock_device: int
    lock_inode: int


@dataclass(frozen=True, slots=True)
class _VerifiedBackup:
    result: VerificationResult
    manifest: dict[str, Any]
    root: Path
    payload: Path
    files: dict[str, dict[str, Any]]
    root_identity: tuple[int, int]
    payload_identity: tuple[int, int]
    payload_sha256: str
    marker_bytes: bytes
    marker_mode: int
    marker_identity: tuple[int, int]
    marker_alias: str
    marker_alias_present: bool


@dataclass(slots=True)
class _RestoreEvidence:
    files: dict[str, dict[str, object]]
    directories: dict[str, int]
    absent_paths: set[str]

    def record_directory(self, target_root: Path, path: Path, *, mode: int) -> None:
        if path == target_root:
            return
        relative = path.relative_to(target_root).as_posix()
        prior = self.directories.setdefault(relative, mode)
        if prior != mode:
            raise RecoveryError("target_mismatch", f"Directory mode conflict: {relative}")

    def record_file(
        self,
        target_root: Path,
        path: Path,
        data: bytes,
        *,
        mode: int,
        role: str,
    ) -> None:
        current = path.parent
        while current != target_root:
            self.record_directory(target_root, current, mode=0o700)
            current = current.parent
        relative = path.relative_to(target_root).as_posix()
        if relative in self.files:
            raise RecoveryError("target_mismatch", f"Duplicate restored file: {relative}")
        self.files[relative] = {
            "path": relative,
            "size": len(data),
            "mode": mode,
            "sha256": _sha256_bytes(data),
            "role": role,
        }

    def record_absent(self, target_root: Path, path: Path) -> None:
        self.absent_paths.add(path.relative_to(target_root).as_posix())

    def serialized_files(self) -> list[dict[str, object]]:
        return [self.files[path] for path in sorted(self.files)]

    def serialized_directories(self) -> list[dict[str, object]]:
        return [{"path": path, "mode": self.directories[path]} for path in sorted(self.directories)]


@dataclass(frozen=True, slots=True)
class _VerifiedRestoreManifest:
    digest: str
    marker_alias: str
    inventory: RecoveryInventoryV1
    target_bindings: TargetBindingsV1
    evidence: _RestoreEvidence
    target_lock: dict[str, object]
    source_keypairs: dict[str, dict[str, object]]
    target_keypairs: dict[str, dict[str, object]]
    target_audit: dict[str, object]
    asset_source_root: Path | None


@dataclass(slots=True)
class _PinnedDirectory:
    path: Path
    descriptor: int
    device: int
    inode: int

    def verify(self) -> None:
        try:
            opened = os.fstat(self.descriptor)
            current = self.path.lstat()
        except OSError as exc:
            raise RecoveryError(
                "source_changed", f"Pinned directory is no longer accessible: {self.path}"
            ) from exc
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or opened.st_dev != self.device
            or opened.st_ino != self.inode
            or current.st_dev != self.device
            or current.st_ino != self.inode
        ):
            raise RecoveryError("source_changed", f"Pinned directory changed: {self.path}")

    def close(self, *, primary_error: BaseException | None) -> None:
        try:
            os.close(self.descriptor)
        except OSError as exc:
            if primary_error is not None:
                primary_error.add_note(f"Pinned directory close also failed: {exc}")
            else:
                raise RecoveryError(
                    "write_failed", f"Cannot close pinned directory: {self.path}"
                ) from exc


@dataclass(slots=True)
class _OwnedScratch:
    parent: _PinnedDirectory
    directory: _PinnedDirectory
    name: str
    owner_marker_bytes: bytes

    @property
    def access_path(self) -> Path:
        return _directory_descriptor_path(self.directory.descriptor)


@dataclass(slots=True)
class _RecoveryWorkspace:
    root: _PinnedDirectory
    lock_descriptor: int
    lock_device: int
    lock_inode: int


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_identifier(value: str, *, name: str) -> None:
    if not value or value in {".", ".."} or "\x00" in value or "/" in value or "\\" in value:
        raise RecoveryError("invalid_argument", f"{name} must be one safe path component")


def _validate_backup_id(value: str) -> None:
    _validate_identifier(value, name="backup_id")
    if value.startswith("."):
        raise RecoveryError("invalid_argument", "backup_id may not use an internal namespace")


def _require_canonical_operation_path(path: Path, *, name: str) -> None:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.anchor != os.sep
        or Path(os.path.normpath(str(path))) != path
        or ".." in path.parts
    ):
        raise RecoveryError("invalid_argument", f"{name} must be a normalized absolute path")


def _require_real_directory(path: Path, *, name: str) -> None:
    if not path.is_absolute():
        raise RecoveryError("invalid_argument", f"{name} must be absolute: {path}")
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise RecoveryError("missing_directory", f"{name} does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RecoveryError("unsafe_path", f"{name} must be a real directory: {path}")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        current_metadata = current.lstat()
        if stat.S_ISLNK(current_metadata.st_mode):
            raise RecoveryError("unsafe_path", f"{name} has a symlink ancestor: {current}")


def _require_directory_mode(path: Path, *, mode: int, name: str) -> None:
    actual = stat.S_IMODE(path.lstat().st_mode)
    if actual != mode:
        raise RecoveryError("unsafe_mode", f"{name} must have mode {mode:04o}, got {actual:04o}")


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _captured_boundaries(inventory: RecoveryInventoryV1) -> tuple[tuple[str, Path], ...]:
    boundaries: list[tuple[str, Path]] = [
        (f"resource {resource.logical_id}", resource.captured_path)
        for resource in inventory.resources
        if resource.capture_kind is not CaptureKind.DIRECTORY_ONLY
    ]
    boundaries.extend(
        (f"config {config.logical_id}", config.captured_path) for config in inventory.configs
    )
    boundaries.append(("maintenance lock", inventory.maintenance_lock_path))
    for keypair in inventory.keypairs:
        public_resource = next(
            resource
            for resource in inventory.resources
            if resource.logical_id == keypair.public_resource_id
        )
        boundaries.append((f"key parent {keypair.handle}", public_resource.captured_path.parent))
    database = next(
        (
            resource.captured_path
            for resource in inventory.resources
            if resource.logical_id == _DATABASE_RESOURCE_ID
        ),
        None,
    )
    if database is not None:
        boundaries.append(("database WAL", Path(f"{database}-wal")))
    return tuple(boundaries)


def _reject_overlap(path: Path, *, name: str, boundaries: tuple[tuple[str, Path], ...]) -> None:
    for boundary_name, boundary in boundaries:
        if _paths_overlap(path, boundary):
            raise RecoveryError("path_overlap", f"{name} overlaps {boundary_name}: {boundary}")


def _validate_root_authorization(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    work_root: Path,
) -> None:
    _require_canonical_operation_path(backup_root, name="backup_root")
    _require_canonical_operation_path(work_root, name="work_root")
    boundaries = _captured_boundaries(inventory)
    _reject_overlap(backup_root, name="backup_root", boundaries=boundaries)
    _reject_overlap(work_root, name="work_root", boundaries=boundaries)
    if _paths_overlap(work_root, inventory.source_root):
        raise RecoveryError("path_overlap", "work_root overlaps source_root provenance")
    if _paths_overlap(backup_root, work_root):
        raise RecoveryError("path_overlap", "backup_root overlaps work_root")


def _validate_roots(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    work_root: Path,
) -> None:
    _validate_root_authorization(
        inventory,
        backup_root=backup_root,
        work_root=work_root,
    )
    _require_real_directory(backup_root, name="backup_root")
    _require_real_directory(work_root, name="work_root")


def _validate_process_quiescence(inventory: RecoveryInventoryV1) -> None:
    for process in inventory.process_ownership:
        known_group = process.pid is not None
        if process.running or process.cleanup_error is not None:
            raise RecoveryError(
                "runtime_not_quiescent",
                f"Recorded process is not cleanly stopped: {process.logical_id}",
            )
        if known_group and not process.group_drained:
            raise RecoveryError(
                "runtime_not_quiescent",
                f"Recorded process group is not drained: {process.logical_id}",
            )


def _validate_inventory_completeness(inventory: RecoveryInventoryV1) -> None:
    runtime_locks = tuple(
        resource
        for resource in inventory.resources
        if resource.capture_kind is CaptureKind.RUNTIME_LOCK
    )
    if len(runtime_locks) != 1:
        raise RecoveryError(
            "incomplete_inventory", "Inventory requires exactly one runtime lock resource"
        )
    runtime_lock_id = runtime_locks[0].logical_id
    lock_bindings = tuple(
        binding
        for binding in inventory.field_bindings
        if tuple(binding.field_path) == ("recovery", "maintenance_lock_path")
    )
    if not lock_bindings:
        raise RecoveryError(
            "incomplete_inventory", "Inventory requires maintenance-lock config bindings"
        )
    if any(binding.resource_id != runtime_lock_id for binding in lock_bindings):
        raise RecoveryError(
            "incomplete_inventory", "Maintenance-lock bindings must use runtime lock resource"
        )


def _require_selected_artifact(root: Path) -> None:
    try:
        _require_real_directory(root, name="selected artifact")
    except RecoveryError as exc:
        if exc.reason == "missing_directory":
            raise RecoveryError("incomplete_bundle", f"Backup is not reserved: {root}") from exc
        raise
    _require_directory_mode(root, mode=0o700, name="selected backup artifact")


def _validate_verify_roots(*, backup_root: Path, backup_id: str, work_root: Path) -> Path:
    _validate_backup_id(backup_id)
    _require_canonical_operation_path(backup_root, name="backup_root")
    _require_canonical_operation_path(work_root, name="work_root")
    _require_real_directory(backup_root, name="backup_root")
    _require_real_directory(work_root, name="work_root")
    selected = backup_root / backup_id
    _require_selected_artifact(selected)
    if _paths_overlap(backup_root, work_root):
        raise RecoveryError("path_overlap", "work_root overlaps backup storage")
    if _paths_overlap(selected, work_root):
        raise RecoveryError("path_overlap", "work_root overlaps selected backup artifact")
    return selected


def _validate_restore_roots(
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    target_root: Path,
) -> None:
    _require_canonical_operation_path(target_root, name="target_root")
    selected = _validate_verify_roots(
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    if _paths_overlap(work_root, backup_root):
        raise RecoveryError("path_overlap", "work_root overlaps backup storage")
    if _paths_overlap(target_root, backup_root) or _paths_overlap(target_root, selected):
        raise RecoveryError("path_overlap", "target_root overlaps backup storage")
    if _paths_overlap(target_root, work_root):
        raise RecoveryError("path_overlap", "target_root overlaps work_root")


def _validate_source_provenance(
    inventory: RecoveryInventoryV1,
) -> dict[Path, tuple[int, int]]:
    _require_real_directory(inventory.source_root, name="source_root")
    roots = {
        *(resource.capture_root for resource in inventory.resources),
        *(config.capture_root for config in inventory.configs),
    }
    identities: dict[Path, tuple[int, int]] = {}
    for root in roots:
        _require_real_directory(root, name="capture_root")
        metadata = root.lstat()
        identities[root] = (metadata.st_dev, metadata.st_ino)
    return identities


def _verify_capture_roots(identities: dict[Path, tuple[int, int]]) -> None:
    for root, expected in identities.items():
        _require_real_directory(root, name="capture_root")
        current = root.lstat()
        if (current.st_dev, current.st_ino) != expected:
            raise RecoveryError("source_changed", f"Capture root changed: {root}")


def _agent_runtime_resource(inventory: RecoveryInventoryV1) -> SourceResourceV1 | None:
    matches = tuple(
        resource
        for resource in inventory.resources
        if resource.logical_id == _AGENT_RUNTIME_RESOURCE_ID
    )
    if len(matches) > 1:
        raise RecoveryError("incomplete_inventory", "Agent runtime resource is duplicated")
    return matches[0] if matches else None


def _inspect_agent_identity(
    identities: _PinnedDirectory,
    name: str,
) -> _AgentRuntimeIdentity:
    if _AGENT_IDENTITY_NAME.fullmatch(name) is None:
        raise RecoveryError("unsafe_resource", f"Invalid agent runtime identity directory: {name}")
    identity = _open_pinned_child(identities, name)
    primary_error: BaseException | None = None
    try:
        identity.verify()
        metadata = os.fstat(identity.descriptor)
        try:
            lock_metadata = os.stat(
                "runtime.lock",
                dir_fd=identity.descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise RecoveryError(
                "unsafe_resource", f"Agent runtime identity lacks a safe lock: {name}"
            ) from exc
        if not stat.S_ISREG(lock_metadata.st_mode) or stat.S_IMODE(lock_metadata.st_mode) != 0o600:
            raise RecoveryError(
                "unsafe_resource", f"Agent runtime identity lock is invalid: {name}"
            )
        identity.verify()
        return _AgentRuntimeIdentity(
            name=name,
            directory_device=metadata.st_dev,
            directory_inode=metadata.st_ino,
            lock_path=identity.path / "runtime.lock",
            lock_device=lock_metadata.st_dev,
            lock_inode=lock_metadata.st_ino,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        identity.close(primary_error=primary_error)


def _snapshot_agent_runtime_identities(
    inventory: RecoveryInventoryV1,
) -> tuple[_AgentRuntimeIdentity, ...]:
    resource = _agent_runtime_resource(inventory)
    if resource is None or resource.capture_kind is not CaptureKind.COPY:
        return ()
    journal = _open_pinned_directory(
        resource.captured_path,
        label="agent runtime journal",
    )
    identities: _PinnedDirectory | None = None
    primary_error: BaseException | None = None
    try:
        journal.verify()
        try:
            identities_metadata = os.stat(
                "identities",
                dir_fd=journal.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            journal.verify()
            return ()
        except OSError as exc:
            raise RecoveryError(
                "unsafe_resource", "Agent runtime identities cannot be inspected"
            ) from exc
        if not stat.S_ISDIR(identities_metadata.st_mode):
            raise RecoveryError("unsafe_resource", "Agent runtime identities is not a directory")
        identities = _open_pinned_child(journal, "identities")
        with os.scandir(identities.descriptor) as entries:
            names = sorted(entry.name for entry in entries)
        snapshot = tuple(_inspect_agent_identity(identities, name) for name in names)
        identities.verify()
        journal.verify()
        return snapshot
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if identities is not None:
            identities.close(primary_error=primary_error)
        journal.close(primary_error=primary_error)


def _hold_agent_runtime_identity_locks(
    inventory: RecoveryInventoryV1,
    stack: ExitStack,
) -> tuple[tuple[_AgentRuntimeIdentity, ...], tuple[HeldMaintenanceLock, ...]]:
    snapshot = _snapshot_agent_runtime_identities(inventory)
    held: list[HeldMaintenanceLock] = []
    for identity in snapshot:
        try:
            lock = stack.enter_context(
                hold_existing_maintenance_lock(
                    identity.lock_path,
                    mode=MaintenanceLockMode.EXCLUSIVE,
                    blocking=False,
                    expected_device=identity.lock_device,
                    expected_inode=identity.lock_inode,
                )
            )
        except MaintenanceLockError as exc:
            reason = "runtime_active" if exc.reason == "busy" else "source_changed"
            raise RecoveryError(
                reason,
                f"Cannot fence agent runtime identity {identity.name}: {exc}",
            ) from exc
        held.append(lock)
    return snapshot, tuple(held)


def _verify_agent_runtime_identity_locks(
    inventory: RecoveryInventoryV1,
    expected: tuple[_AgentRuntimeIdentity, ...],
    held: tuple[HeldMaintenanceLock, ...],
) -> None:
    try:
        for lock in held:
            lock.verify_identity()
    except MaintenanceLockError as exc:
        raise RecoveryError("source_changed", f"Agent runtime lock changed: {exc}") from exc
    if _snapshot_agent_runtime_identities(inventory) != expected:
        raise RecoveryError("source_changed", "Agent runtime identity set changed during snapshot")


def _assert_no_symlink_components(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            raise RecoveryError(
                "missing_resource", f"Missing declared resource {label}: {path}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RecoveryError("unsafe_resource", f"Symlinked declared resource {label}: {path}")


def _close_descriptors(descriptors: list[int], *, primary_error: BaseException | None) -> None:
    close_errors: list[OSError] = []
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError as error:
            close_errors.append(error)
    if not close_errors:
        return
    if primary_error is not None:
        for close_error in close_errors:
            primary_error.add_note(f"Descriptor close also failed: {close_error}")
        return
    raise RecoveryError("close_failed", "Descriptor cleanup failed") from close_errors[0]


def _close_pinned_directories(
    directories: list[_PinnedDirectory], *, primary_error: BaseException | None
) -> None:
    _close_descriptors(
        [directory.descriptor for directory in directories],
        primary_error=primary_error,
    )


def _open_verified_parent(path: Path, *, label: str) -> list[int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(path.anchor, flags))
        for component in path.parent.parts[1:]:
            parent = descriptors[-1]
            child: int | None = None
            try:
                child = os.open(component, flags, dir_fd=parent)
                opened = os.fstat(child)
                current = os.stat(component, dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or not stat.S_ISDIR(current.st_mode)
                    or opened.st_dev != current.st_dev
                    or opened.st_ino != current.st_ino
                ):
                    raise RecoveryError(
                        "source_changed", f"Source ancestor changed while opening {label}"
                    )
                descriptors.append(child)
                child = None
            finally:
                if child is not None:
                    _close_descriptors([child], primary_error=sys.exception())
    except OSError as exc:
        error = RecoveryError("unsafe_resource", f"Cannot safely traverse {label}: {path}")
        _close_descriptors(descriptors, primary_error=error)
        raise error from exc
    except BaseException as error:
        _close_descriptors(descriptors, primary_error=error)
        raise
    return descriptors


def _open_pinned_directory(path: Path, *, label: str) -> _PinnedDirectory:
    parents = _open_verified_parent(path, label=label)
    descriptor: int | None = None
    primary_error: BaseException | None = None
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parents[-1],
        )
        opened = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parents[-1], follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
        ):
            raise RecoveryError("source_changed", f"Directory changed while opening: {path}")
        pinned = _PinnedDirectory(
            path=path,
            descriptor=descriptor,
            device=opened.st_dev,
            inode=opened.st_ino,
        )
        pinned.verify()
        descriptor = None
        return pinned
    except BaseException as error:
        primary_error = error
        raise
    finally:
        owned = parents if descriptor is None else [*parents, descriptor]
        _close_descriptors(owned, primary_error=primary_error)


def _create_pinned_child(
    parent: _PinnedDirectory,
    name: str,
    *,
    mode: int,
) -> _PinnedDirectory:
    _validate_identifier(name, name="directory name")
    parent.verify()
    try:
        os.mkdir(name, mode, dir_fd=parent.descriptor)
    except FileExistsError as exc:
        raise RecoveryError(
            "already_exists", f"Directory already exists: {parent.path / name}"
        ) from exc
    descriptor: int | None = None
    primary_error: BaseException | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent.descriptor,
        )
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.fsync(parent.descriptor)
        metadata = os.fstat(descriptor)
        child = _PinnedDirectory(
            path=parent.path / name,
            descriptor=descriptor,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
        parent.verify()
        child.verify()
        descriptor = None
        return child
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if descriptor is not None:
            _close_descriptors([descriptor], primary_error=primary_error)


def _open_pinned_child(parent: _PinnedDirectory, name: str) -> _PinnedDirectory:
    _validate_identifier(name, name="directory name")
    descriptor: int | None = None
    primary_error: BaseException | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent.descriptor,
        )
        metadata = os.fstat(descriptor)
        child = _PinnedDirectory(
            path=parent.path / name,
            descriptor=descriptor,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
        parent.verify()
        child.verify()
        descriptor = None
        return child
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if descriptor is not None:
            _close_descriptors([descriptor], primary_error=primary_error)


def _directory_descriptor_path(descriptor: int) -> Path:
    proc_root = Path("/proc/self/fd")
    if proc_root.is_dir():
        proc_path = proc_root / str(descriptor)
        return proc_path
    get_path_command = getattr(fcntl, "F_GETPATH", None)
    if isinstance(get_path_command, int):
        raw_path = fcntl.fcntl(descriptor, get_path_command, bytes(1024))
        return Path(os.fsdecode(raw_path.split(b"\0", 1)[0]))
    raise RecoveryError("unsafe_path", "Platform has no descriptor-backed directory path")


def _create_owned_scratch(parent: _PinnedDirectory, *, prefix: str) -> _OwnedScratch:
    operation_id = uuid.uuid4().hex
    name = f".{prefix}{operation_id}"
    parent.verify()
    os.mkdir(name, 0o700, dir_fd=parent.descriptor)
    created = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(created.st_mode) or created.st_uid != os.geteuid():
        raise RecoveryError("source_changed", "Scratch creation identity is invalid")
    owner_marker_bytes = _canonical_json(
        {
            "device": created.st_dev,
            "format_version": 1,
            "inode": created.st_ino,
            "operation_id": operation_id,
            "scratch_name": name,
        }
    )
    descriptor: int | None = None
    scratch: _OwnedScratch | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent.descriptor,
        )
        metadata = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or metadata.st_dev != created.st_dev
            or metadata.st_ino != created.st_ino
            or current.st_dev != created.st_dev
            or current.st_ino != created.st_ino
        ):
            raise RecoveryError("source_changed", "Scratch entry changed between creation and open")
        directory = _PinnedDirectory(
            path=parent.path / name,
            descriptor=descriptor,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
        scratch = _OwnedScratch(
            parent=parent,
            directory=directory,
            name=name,
            owner_marker_bytes=owner_marker_bytes,
        )
        os.fchmod(descriptor, 0o700)
        _write_new_file_under(
            directory,
            Path(".owner.json"),
            owner_marker_bytes,
            mode=0o600,
        )
        os.fsync(descriptor)
        os.fsync(parent.descriptor)
        parent.verify()
        directory.verify()
        descriptor = None
        return scratch
    except BaseException as error:
        if scratch is not None:
            _cleanup_owned_scratch(scratch, primary_error=error)
            descriptor = None
        else:
            if descriptor is not None:
                _close_descriptors([descriptor], primary_error=error)
            if isinstance(error, RecoveryError):
                raise
            raise RecoveryError(
                "cleanup_failed",
                "Scratch entry ownership was not established; the entry was left untouched",
            ) from error
        raise


def _scratch_entry_matches(scratch: _OwnedScratch) -> bool:
    try:
        opened = os.fstat(scratch.directory.descriptor)
        entry = os.stat(
            scratch.name,
            dir_fd=scratch.parent.descriptor,
            follow_symlinks=False,
        )
    except OSError:
        return False
    return (
        stat.S_ISDIR(opened.st_mode)
        and stat.S_ISDIR(entry.st_mode)
        and opened.st_dev == scratch.directory.device
        and opened.st_ino == scratch.directory.inode
        and entry.st_dev == scratch.directory.device
        and entry.st_ino == scratch.directory.inode
    )


def _merge_cleanup_error(
    current: BaseException | None, error: BaseException, *, note: str
) -> BaseException:
    if current is not None:
        current.add_note(f"{note}: {error}")
        return current
    return error


def _verify_retained_scratch(scratch: _OwnedScratch) -> None:
    scratch.parent.verify()
    if not _scratch_entry_matches(scratch):
        raise RecoveryError(
            "cleanup_failed", "Scratch entry identity changed; all visible entries were retained"
        )
    metadata = os.fstat(scratch.directory.descriptor)
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RecoveryError("unsafe_mode", "Retained scratch directory ownership or mode changed")
    marker_bytes, marker_mode = _read_regular_under(
        scratch.directory, ".owner.json", label="scratch owner marker"
    )
    if marker_bytes != scratch.owner_marker_bytes or marker_mode != 0o600:
        raise RecoveryError("cleanup_failed", "Scratch owner marker changed")


def _cleanup_owned_scratch(
    scratch: _OwnedScratch, *, primary_error: BaseException | None
) -> BaseException | None:
    cleanup_error = primary_error
    try:
        _verify_retained_scratch(scratch)
    except BaseException as error:
        cleanup_error = _merge_cleanup_error(
            cleanup_error, error, note="Retained scratch verification failed"
        )
    try:
        os.close(scratch.directory.descriptor)
    except OSError as error:
        close_error = RecoveryError("close_failed", f"Scratch descriptor cleanup failed: {error}")
        close_error.__cause__ = error
        cleanup_error = _merge_cleanup_error(
            cleanup_error, close_error, note="Scratch descriptor close failed"
        )
    return cleanup_error


@contextmanager
def _owned_scratch_directory(parent: _PinnedDirectory, *, prefix: str) -> Iterator[_OwnedScratch]:
    scratch = _create_owned_scratch(parent, prefix=prefix)
    primary_error: BaseException | None = None
    try:
        yield scratch
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_error = _cleanup_owned_scratch(scratch, primary_error=primary_error)
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error


def _verify_workspace(workspace: _RecoveryWorkspace) -> None:
    workspace.root.verify()
    root_metadata = os.fstat(workspace.root.descriptor)
    if root_metadata.st_uid != os.geteuid() or stat.S_IMODE(root_metadata.st_mode) != 0o700:
        raise RecoveryError("unsafe_mode", "work_root must be owned by euid with mode 0700")
    opened = os.fstat(workspace.lock_descriptor)
    visible = os.stat(
        ".recovery-owner.lock",
        dir_fd=workspace.root.descriptor,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(visible.st_mode)
        or opened.st_dev != workspace.lock_device
        or opened.st_ino != workspace.lock_inode
        or visible.st_dev != workspace.lock_device
        or visible.st_ino != workspace.lock_inode
        or opened.st_uid != os.geteuid()
        or visible.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) != 0o600
        or stat.S_IMODE(visible.st_mode) != 0o600
    ):
        raise RecoveryError("workspace_changed", "Recovery workspace lease identity changed")


def _open_workspace(work_root: Path) -> _RecoveryWorkspace:
    _require_canonical_operation_path(work_root, name="work_root")
    _require_real_directory(work_root, name="work_root")
    metadata = work_root.lstat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RecoveryError("unsafe_mode", "work_root must be owned by euid with mode 0700")
    root = _open_pinned_directory(work_root, label="work root")
    lock_descriptor: int | None = None
    primary_error: BaseException | None = None
    try:
        try:
            lock_descriptor = os.open(
                ".recovery-owner.lock",
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=root.descriptor,
            )
            os.fchmod(lock_descriptor, 0o600)
            os.fsync(lock_descriptor)
            os.fsync(root.descriptor)
        except FileExistsError:
            lock_descriptor = os.open(
                ".recovery-owner.lock",
                os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=root.descriptor,
            )
        lock_metadata = os.fstat(lock_descriptor)
        workspace = _RecoveryWorkspace(
            root=root,
            lock_descriptor=lock_descriptor,
            lock_device=lock_metadata.st_dev,
            lock_inode=lock_metadata.st_ino,
        )
        _verify_workspace(workspace)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryError("work_root_busy", "Recovery workspace is already in use") from exc
        _verify_workspace(workspace)
        lock_descriptor = None
        return workspace
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if lock_descriptor is not None:
            _close_descriptors([lock_descriptor], primary_error=primary_error)
        if primary_error is not None:
            root.close(primary_error=primary_error)


def _close_workspace(
    workspace: _RecoveryWorkspace, *, primary_error: BaseException | None
) -> BaseException | None:
    cleanup_error = primary_error
    try:
        _verify_workspace(workspace)
    except BaseException as error:
        cleanup_error = _merge_cleanup_error(
            cleanup_error, error, note="Recovery workspace verification failed"
        )
    try:
        fcntl.flock(workspace.lock_descriptor, fcntl.LOCK_UN)
    except OSError as error:
        cleanup_error = _merge_cleanup_error(
            cleanup_error, error, note="Recovery workspace unlock failed"
        )
    try:
        _verify_workspace(workspace)
    except BaseException as error:
        cleanup_error = _merge_cleanup_error(
            cleanup_error, error, note="Recovery workspace post-unlock verification failed"
        )
    try:
        _close_descriptors([workspace.lock_descriptor], primary_error=cleanup_error)
    except BaseException as error:
        cleanup_error = error
    try:
        workspace.root.close(primary_error=cleanup_error)
    except BaseException as error:
        cleanup_error = error
    return cleanup_error


@contextmanager
def _hold_recovery_workspace(work_root: Path) -> Iterator[_RecoveryWorkspace]:
    """Hold the private cooperative lease required of every recovery operation.

    Mode and ownership exclude other users.  Processes under the same effective
    user must honor this lease; portable POSIX APIs cannot exclude a deliberately
    uncooperative same-user process from renaming directory entries.
    """

    workspace = _open_workspace(work_root)
    primary_error: BaseException | None = None
    try:
        yield workspace
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_error = _close_workspace(workspace, primary_error=primary_error)
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error


def _relative_output_parts(value: Path) -> tuple[str, ...]:
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise RecoveryError("unsafe_path", f"Output-relative path is unsafe: {value}")
    return value.parts


def _open_or_create_relative_parent(
    root: _PinnedDirectory, relative: Path
) -> list[_PinnedDirectory]:
    parts = _relative_output_parts(relative)
    opened: list[_PinnedDirectory] = []
    parent = root
    try:
        for component in parts[:-1]:
            try:
                child = _open_pinned_child(parent, component)
            except FileNotFoundError:
                child = _create_pinned_child(parent, component, mode=0o700)
            opened.append(child)
            parent = child
        return opened
    except BaseException as error:
        _close_pinned_directories(opened, primary_error=error)
        raise


def _write_new_file_under(
    root: _PinnedDirectory,
    relative: Path,
    data: bytes,
    *,
    mode: int,
) -> Path:
    parts = _relative_output_parts(relative)
    parents = _open_or_create_relative_parent(root, relative)
    parent = parents[-1] if parents else root
    descriptor: int | None = None
    primary_error: BaseException | None = None
    try:
        descriptor = os.open(
            parts[-1],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            mode,
            dir_fd=parent.descriptor,
        )
        os.fchmod(descriptor, mode)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        current = os.stat(parts[-1], dir_fd=parent.descriptor, follow_symlinks=False)
        if (
            opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
            or stat.S_IMODE(opened.st_mode) != mode
        ):
            raise RecoveryError("source_changed", "Output file identity changed")
        root.verify()
        return root.path / relative
    except OSError as exc:
        primary_error = RecoveryError("write_failed", f"Cannot write output: {relative}")
        raise primary_error from exc
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as error:
                if primary_error is not None:
                    primary_error.add_note(f"Output file close also failed: {error}")
                else:
                    raise RecoveryError("write_failed", "Cannot close output file") from error
        _close_pinned_directories(parents, primary_error=primary_error)


def _read_regular_under(parent: _PinnedDirectory, name: str, *, label: str) -> tuple[bytes, int]:
    _validate_identifier(name, name=label)
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        dir_fd=parent.descriptor,
    )
    primary_error: BaseException | None = None
    try:
        before = os.fstat(descriptor)
        data = _read_descriptor(descriptor)
        after = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(after.st_mode)
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or after.st_dev != visible.st_dev
            or after.st_ino != visible.st_ino
            or before.st_size != after.st_size
        ):
            raise RecoveryError("source_changed", f"{label} changed while reading")
        return data, stat.S_IMODE(after.st_mode)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        _close_descriptors([descriptor], primary_error=primary_error)


def _ensure_directory_under(root: _PinnedDirectory, relative: Path) -> Path:
    anchor = relative / ".directory-anchor"
    directories = _open_or_create_relative_parent(root, anchor)
    primary_error: BaseException | None = None
    try:
        root.verify()
        return root.path / relative
    except BaseException as error:
        primary_error = error
        raise
    finally:
        _close_pinned_directories(directories, primary_error=primary_error)


def _link_between(
    source: _PinnedDirectory,
    source_name: str,
    target: _PinnedDirectory,
    target_name: str,
) -> None:
    os.link(
        source_name,
        target_name,
        src_dir_fd=source.descriptor,
        dst_dir_fd=target.descriptor,
        follow_symlinks=False,
    )
    os.fsync(target.descriptor)
    source.verify()
    target.verify()


def _open_source_file(path: Path, *, label: str) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    parents = _open_verified_parent(path, label=label)
    descriptor: int | None = None
    primary_error: BaseException | None = None
    try:
        try:
            descriptor = os.open(path.name, flags, dir_fd=parents[-1])
        except FileNotFoundError as exc:
            raise RecoveryError(
                "missing_resource", f"Missing declared resource {label}: {path}"
            ) from exc
        except OSError as exc:
            raise RecoveryError("unsafe_resource", f"Cannot safely open {label}: {path}") from exc
        opened = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parents[-1], follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
        ):
            raise RecoveryError("source_changed", f"Source identity changed while opening {label}")
        owned_parents = parents
        parents = []
        _close_descriptors(owned_parents, primary_error=None)
        result = descriptor
        descriptor = None
        return result
    except BaseException as error:
        primary_error = error
        raise
    finally:
        owned = parents if descriptor is None else [*parents, descriptor]
        _close_descriptors(owned, primary_error=primary_error)


def _read_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        chunk = os.pread(descriptor, 1024 * 1024, offset)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        offset += len(chunk)


def _open_or_create_output_parent(path: Path) -> list[int]:
    _require_canonical_operation_path(path, name="output path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptors = [os.open(path.anchor, flags)]
    try:
        for component in path.parent.parts[1:]:
            parent = descriptors[-1]
            created = False
            try:
                os.mkdir(component, 0o700, dir_fd=parent)
                os.fsync(parent)
                created = True
            except FileExistsError:
                pass
            child: int | None = None
            try:
                child = os.open(component, flags, dir_fd=parent)
                if created:
                    os.fchmod(child, 0o700)
                    os.fsync(child)
                opened = os.fstat(child)
                current = os.stat(component, dir_fd=parent, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or not stat.S_ISDIR(current.st_mode)
                    or opened.st_dev != current.st_dev
                    or opened.st_ino != current.st_ino
                ):
                    raise RecoveryError("unsafe_path", f"Output ancestor changed: {path}")
                descriptors.append(child)
                child = None
            finally:
                if child is not None:
                    _close_descriptors([child], primary_error=sys.exception())
    except BaseException as error:
        _close_descriptors(descriptors, primary_error=error)
        raise
    return descriptors


def write_new_recovery_file(path: Path, data: bytes, *, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    parents = _open_or_create_output_parent(path)
    descriptor: int | None = None
    primary_error: BaseException | None = None
    body_failed = False
    try:
        descriptor = os.open(path.name, flags, mode, dir_fd=parents[-1])
        os.fchmod(descriptor, mode)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parents[-1], follow_symlinks=False)
        visible = path.lstat()
        if (
            opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
            or opened.st_dev != visible.st_dev
            or opened.st_ino != visible.st_ino
            or stat.S_IMODE(opened.st_mode) != mode
        ):
            raise RecoveryError("source_changed", f"Output path changed while writing: {path}")
    except OSError as exc:
        body_failed = True
        primary_error = RecoveryError("write_failed", f"Cannot write recovery file: {path}")
        raise primary_error from exc
    except BaseException as error:
        body_failed = True
        primary_error = error
        raise
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as error:
                if primary_error is not None:
                    primary_error.add_note(f"Output file close also failed: {error}")
                else:
                    primary_error = RecoveryError(
                        "write_failed", f"Cannot close recovery file: {path}"
                    )
        _close_descriptors(parents, primary_error=primary_error)
        if primary_error is not None and not body_failed:
            raise primary_error


def _create_fresh_output_root(
    path: Path,
) -> tuple[_PinnedDirectory, _PinnedDirectory]:
    ancestor_descriptors = _open_or_create_output_parent(path)
    _close_descriptors(ancestor_descriptors, primary_error=None)
    parent = _open_pinned_directory(path.parent, label="restore target parent")
    try:
        target = _create_pinned_child(parent, path.name, mode=0o700)
    except BaseException as error:
        parent.close(primary_error=error)
        raise
    return target, parent


def _copy_open_file(
    *,
    source_path: Path,
    destination: Path,
    label: str,
    output_root: _PinnedDirectory,
) -> _OpenCopy:
    descriptor = _open_source_file(source_path, label=label)
    try:
        metadata = os.fstat(descriptor)
        data = _read_descriptor(descriptor)
        digest = _sha256_bytes(data)
        _write_new_file_under(
            output_root,
            destination.relative_to(output_root.path),
            data,
            mode=stat.S_IMODE(metadata.st_mode),
        )
        return _OpenCopy(
            source_path=source_path,
            file_descriptor=descriptor,
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size=len(data),
            mode=stat.S_IMODE(metadata.st_mode),
            sha256=digest,
            destination=destination,
        )
    except BaseException as error:
        _close_descriptors([descriptor], primary_error=error)
        raise


def _verify_and_close_copy(copied: _OpenCopy) -> None:
    primary_error: BaseException | None = None
    try:
        current_descriptor = os.fstat(copied.file_descriptor)
        current_path = copied.source_path.lstat()
        data = _read_descriptor(copied.file_descriptor)
        if (
            not stat.S_ISREG(current_descriptor.st_mode)
            or not stat.S_ISREG(current_path.st_mode)
            or current_descriptor.st_dev != copied.device
            or current_descriptor.st_ino != copied.inode
            or current_path.st_dev != copied.device
            or current_path.st_ino != copied.inode
            or current_descriptor.st_size != copied.size
            or stat.S_IMODE(current_descriptor.st_mode) != copied.mode
            or stat.S_IMODE(current_path.st_mode) != copied.mode
            or _sha256_bytes(data) != copied.sha256
        ):
            raise RecoveryError(
                "source_changed", f"Source changed during copy: {copied.source_path}"
            )
        staged_bytes, _staged_mode = _read_pinned_regular(copied.destination, label="staged copy")
        if _sha256_bytes(staged_bytes) != copied.sha256:
            raise RecoveryError("digest_mismatch", f"Staged copy differs: {copied.destination}")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(copied.file_descriptor)
        except OSError as close_error:
            if primary_error is not None:
                primary_error.add_note(f"Source descriptor close also failed: {close_error}")
            else:
                raise RecoveryError(
                    "source_close_failed",
                    f"Cannot close source descriptor: {copied.source_path}",
                ) from close_error


def _file_entry(path: Path, *, payload: Path, role: str) -> dict[str, object]:
    data, mode = _read_pinned_regular(path, label=f"file evidence {role}")
    return {
        "path": path.relative_to(payload).as_posix(),
        "role": role,
        "size": len(data),
        "mode": mode,
        "sha256": _sha256_bytes(data),
    }


def _resource_payload_path(resource_id: str, *, kind: CaptureKind, basename: str) -> Path:
    if resource_id == _DATABASE_RESOURCE_ID:
        return Path("database/economy.db")
    if resource_id == _ASSET_RESOURCE_ID:
        return Path("assets")
    if kind is CaptureKind.CONFIG:
        return Path("configs/source") / f"{resource_id}.yaml"
    return Path("resources") / resource_id / basename


def _capture_directory(
    *,
    source: Path,
    destination: Path,
    label: str,
    payload: Path,
    payload_output: _PinnedDirectory,
) -> tuple[list[dict[str, object]], list[_OpenCopy]]:
    _assert_no_symlink_components(source, label=label)
    if not source.is_dir():
        raise RecoveryError("unsafe_resource", f"Declared directory is not a directory: {label}")
    initial = source.lstat()
    _ensure_directory_under(payload_output, destination.relative_to(payload))
    entries: list[dict[str, object]] = []
    open_copies: list[_OpenCopy] = []
    for child in sorted(source.rglob("*")):
        _assert_no_symlink_components(child, label=label)
        relative = child.relative_to(source)
        child_destination = destination / relative
        metadata = child.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            _ensure_directory_under(payload_output, child_destination.relative_to(payload))
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RecoveryError("unsafe_resource", f"Unsupported resource entry: {child}")
        copied = _copy_open_file(
            source_path=child,
            destination=child_destination,
            label=label,
            output_root=payload_output,
        )
        open_copies.append(copied)
        entries.append(_file_entry(child_destination, payload=payload, role=label))
    current = source.lstat()
    if (
        not stat.S_ISDIR(current.st_mode)
        or initial.st_dev != current.st_dev
        or initial.st_ino != current.st_ino
    ):
        raise RecoveryError("source_changed", f"Source directory changed during copy: {label}")
    return entries, open_copies


def _capture_keypairs(
    inventory: RecoveryInventoryV1,
    *,
    payload: Path,
    payload_output: _PinnedDirectory,
) -> tuple[
    list[dict[str, object]],
    dict[str, dict[str, object]],
    list[dict[str, object]],
]:
    resources = {resource.logical_id: resource for resource in inventory.resources}
    targets = tuple(
        (keypair.handle, resources[keypair.public_resource_id].captured_path)
        for keypair in inventory.keypairs
    )
    try:
        snapshots = snapshot_keypairs(targets, blocking=False)
    except KeyMaterialError as exc:
        code = "missing_key_material" if exc.reason == "absent_pair" else "unsafe_key_material"
        return [], {}, [{"code": code, "reason": exc.reason}]
    by_handle = {snapshot.handle: snapshot for snapshot in snapshots}
    gateway_fingerprints: set[str] = set()
    economic_fingerprints: set[str] = set()
    entries: list[dict[str, object]] = []
    metadata: dict[str, dict[str, object]] = {}
    findings: list[dict[str, object]] = []
    for keypair in inventory.keypairs:
        snapshot = by_handle[keypair.handle]
        fingerprint = snapshot.public_fingerprint_sha256
        if fingerprint != keypair.expected_public_fingerprint_sha256:
            findings.append({"code": "captured_fingerprint_mismatch", "handle": keypair.handle})
        if keypair.role is KeypairRole.GATEWAY:
            if fingerprint in gateway_fingerprints or fingerprint in economic_fingerprints:
                findings.append({"code": "gateway_key_collision", "handle": keypair.handle})
            gateway_fingerprints.add(fingerprint)
        else:
            if fingerprint in gateway_fingerprints:
                findings.append({"code": "gateway_key_collision", "handle": keypair.handle})
            economic_fingerprints.add(fingerprint)
        private_path = payload / "keys" / f"{keypair.handle}.key"
        public_path = payload / "keys" / f"{keypair.handle}.pub"
        _write_new_file_under(
            payload_output,
            private_path.relative_to(payload),
            snapshot.private_pem,
            mode=0o600,
        )
        _write_new_file_under(
            payload_output,
            public_path.relative_to(payload),
            snapshot.public_pem,
            mode=0o600,
        )
        entries.extend(
            (
                _file_entry(private_path, payload=payload, role=keypair.private_resource_id),
                _file_entry(public_path, payload=payload, role=keypair.public_resource_id),
            )
        )
        metadata[keypair.handle] = {
            "role": keypair.role.value,
            "fingerprint": fingerprint,
            "private_resource_id": keypair.private_resource_id,
            "public_resource_id": keypair.public_resource_id,
            "private_payload_path": private_path.relative_to(payload).as_posix(),
            "public_payload_path": public_path.relative_to(payload).as_posix(),
            "private_restore_mode": snapshot.private_mode,
            "public_restore_mode": snapshot.public_mode,
        }
    return entries, metadata, findings


def _registry_public_fingerprint(value: object) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value.startswith("ed25519:"):
        return None, "malformed_registry_key"
    encoded = value.removeprefix("ed25519:")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None, "malformed_registry_key"
    if len(raw) != 32 or base64.b64encode(raw).decode() != encoded:
        return None, "malformed_registry_key"
    if raw == bytes(32):
        return None, "zero_registry_key"
    return _sha256_bytes(raw), None


def _registry_rows(
    connection: sqlite3.Connection,
) -> tuple[tuple[tuple[object, object], ...], bool]:
    try:
        return (
            tuple(connection.execute("SELECT agent_id, public_key FROM identity_agents")),
            False,
        )
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return (), False
        if "no such column" in str(exc):
            return (), True
        raise


def _index_registry_rows(
    rows: tuple[tuple[object, object], ...],
    *,
    findings: list[dict[str, object]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    fingerprints_by_agent: dict[str, str] = {}
    agents_by_fingerprint: dict[str, list[str]] = {}
    agent_id_counts: dict[str, int] = {}
    for agent_id_value, public_key_value in rows:
        if not isinstance(agent_id_value, str):
            findings.append({"code": "malformed_registry_key", "agent_id": "<invalid>"})
            continue
        agent_id_counts[agent_id_value] = agent_id_counts.get(agent_id_value, 0) + 1
        fingerprint, invalid_code = _registry_public_fingerprint(public_key_value)
        if fingerprint is None:
            findings.append({"code": cast("str", invalid_code), "agent_id": agent_id_value})
            continue
        fingerprints_by_agent[agent_id_value] = fingerprint
        agents_by_fingerprint.setdefault(fingerprint, []).append(agent_id_value)
    for agent_id, count in sorted(agent_id_counts.items()):
        if count > 1:
            findings.append(
                {
                    "code": "duplicate_registry_agent_id",
                    "agent_id": agent_id,
                    "count": count,
                }
            )
    for fingerprint, agent_ids in sorted(agents_by_fingerprint.items()):
        if len(agent_ids) > 1:
            findings.append(
                {
                    "code": "duplicate_registry_key",
                    "fingerprint": fingerprint,
                    "agent_ids": sorted(agent_ids),
                }
            )
    return fingerprints_by_agent, agents_by_fingerprint


def _audit_identity_registry(
    connection: sqlite3.Connection,
    *,
    inventory: RecoveryInventoryV1 | None,
    captured_keypairs: dict[str, dict[str, object]] | None,
) -> list[dict[str, object]]:
    if inventory is None or captured_keypairs is None:
        return []
    findings: list[dict[str, object]] = []
    rows, schema_incomplete = _registry_rows(connection)
    if schema_incomplete:
        findings.append({"code": "identity_schema_incomplete"})
    fingerprints_by_agent, agents_by_fingerprint = _index_registry_rows(rows, findings=findings)
    for keypair in inventory.keypairs:
        expected_fingerprint = keypair.expected_public_fingerprint_sha256
        registered_agents = agents_by_fingerprint.get(expected_fingerprint, [])
        expected_agent = keypair.expected_registered_agent_id
        if keypair.registration_requirement is RegistrationRequirement.FORBIDDEN:
            if registered_agents:
                findings.append({"code": "forbidden_registration", "handle": keypair.handle})
            continue
        if keypair.captured_registration_state is CapturedRegistrationState.UNREGISTERED:
            if registered_agents:
                findings.append({"code": "registration_state_mismatch", "handle": keypair.handle})
            continue
        if (
            expected_agent is None
            or fingerprints_by_agent.get(expected_agent) != expected_fingerprint
            or registered_agents != [expected_agent]
        ):
            findings.append({"code": "registered_identity_mismatch", "handle": keypair.handle})
    findings.sort(key=_canonical_json)
    return findings


def _audit_database(
    database: Path,
    wal: Path | None,
    *,
    work_root: _PinnedDirectory,
    asset_root: Path | None,
    asset_source_root: Path | None,
    identity_inventory: RecoveryInventoryV1 | None,
    captured_keypairs: dict[str, dict[str, object]] | None,
) -> dict[str, object]:
    with _owned_scratch_directory(work_root, prefix="mp06-audit-") as scratch:
        clone = scratch.access_path / "economy.db"
        database_bytes, database_mode = _read_pinned_regular(database, label="audit database")
        _write_new_file_under(
            scratch.directory, Path("economy.db"), database_bytes, mode=database_mode
        )
        if wal is not None:
            wal_bytes, wal_mode = _read_pinned_regular(wal, label="audit database WAL")
            _write_new_file_under(
                scratch.directory, Path("economy.db-wal"), wal_bytes, mode=wal_mode
            )
        findings: list[dict[str, object]] = [
            {"code": "unknown_schema", "classification": "unclassified"}
        ]
        identity_findings: list[dict[str, object]] = []
        try:
            connection = sqlite3.connect(f"{clone.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise RecoveryError(
                "database_unreadable", "Preserved database clone cannot open"
            ) from exc
        try:
            integrity = tuple(row[0] for row in connection.execute("PRAGMA integrity_check"))
            if integrity != ("ok",):
                findings.append({"code": "sqlite_integrity", "details": list(integrity)})
            foreign_keys = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
            if foreign_keys:
                findings.append({"code": "foreign_key_violation", "rows": foreign_keys})
            _audit_assets(
                connection,
                findings=findings,
                asset_root=asset_root,
                asset_source_root=asset_source_root,
            )
            identity_findings = _audit_identity_registry(
                connection,
                inventory=identity_inventory,
                captured_keypairs=captured_keypairs,
            )
            for table, column, query in _MONEY_STORAGE_QUERIES:
                try:
                    count = connection.execute(query).fetchone()[0]
                except sqlite3.OperationalError as exc:
                    if "no such table" in str(exc) or "no such column" in str(exc):
                        continue
                    raise
                if count:
                    findings.append(
                        {
                            "code": "legacy_money_storage",
                            "table": table,
                            "column": column,
                            "count": count,
                        }
                    )
        except sqlite3.Error as exc:
            raise RecoveryError(
                "database_unreadable", "Preserved database clone audit failed"
            ) from exc
        finally:
            connection.close()
    findings.sort(key=_canonical_json)
    return {
        "audit_contract_version": 1,
        "schema_classification": "unclassified",
        "findings": findings,
        "identity_findings": identity_findings,
    }


def _validated_asset_row(
    row: tuple[object, object, object, object],
    *,
    findings: list[dict[str, object]],
) -> tuple[str, str, int, str | None] | None:
    asset_id, storage_path, size_bytes, content_hash = row
    if (
        not isinstance(asset_id, str)
        or not isinstance(storage_path, str)
        or type(size_bytes) is not int
        or size_bytes < 0
        or (content_hash is not None and not isinstance(content_hash, str))
    ):
        findings.append(
            {
                "code": "malformed_asset_row",
                "asset_id": asset_id if isinstance(asset_id, str) else "<invalid>",
            }
        )
        return None
    return asset_id, storage_path, size_bytes, content_hash


def _audit_asset_entry(
    row: tuple[str, str, int, str | None],
    *,
    asset_root: Path,
    asset_source_root: Path,
    findings: list[dict[str, object]],
    referenced: set[Path],
) -> None:
    asset_id, storage_path_text, size_bytes, content_hash = row
    source_path = Path(storage_path_text)
    if (
        not source_path.parts
        or ".." in source_path.parts
        or (source_path.is_absolute() and not source_path.is_relative_to(asset_source_root))
    ):
        findings.append({"code": "unsafe_asset_reference", "asset_id": asset_id})
        return
    relative = (
        source_path.relative_to(asset_source_root) if source_path.is_absolute() else source_path
    )
    restored_path = asset_root / relative
    referenced.add(restored_path)
    try:
        metadata = restored_path.lstat()
    except FileNotFoundError:
        findings.append({"code": "missing_asset", "asset_id": asset_id})
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        findings.append({"code": "unsafe_asset", "asset_id": asset_id})
        return
    data, _mode = _read_pinned_regular(restored_path, label=f"audit asset {asset_id}")
    if len(data) != size_bytes:
        findings.append({"code": "asset_size_mismatch", "asset_id": asset_id})
    if content_hash is not None and _sha256_bytes(data) != content_hash:
        findings.append({"code": "asset_hash_mismatch", "asset_id": asset_id})


def _audit_assets(
    connection: sqlite3.Connection,
    *,
    findings: list[dict[str, object]],
    asset_root: Path | None,
    asset_source_root: Path | None,
) -> None:
    if asset_root is None or asset_source_root is None:
        return
    try:
        rows = tuple(
            connection.execute(
                "SELECT asset_id, storage_path, size_bytes, content_hash FROM board_assets"
            )
        )
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return
        if "no such column" in str(exc):
            findings.append({"code": "asset_schema_incomplete"})
            return
        raise
    referenced: set[Path] = set()
    for raw_row in rows:
        row = _validated_asset_row(raw_row, findings=findings)
        if row is None:
            continue
        _audit_asset_entry(
            row,
            asset_root=asset_root,
            asset_source_root=asset_source_root,
            findings=findings,
            referenced=referenced,
        )
    if not asset_root.exists():
        return
    for path in sorted(asset_root.rglob("*")):
        if path.is_file() and path not in referenced:
            findings.append(
                {
                    "code": "orphaned_asset",
                    "path": path.relative_to(asset_root).as_posix(),
                }
            )


def _resource_record(resource: SourceResourceV1, relative: Path) -> dict[str, object]:
    return {
        "kind": resource.kind.value,
        "capture_kind": resource.capture_kind.value,
        "payload_path": relative.as_posix(),
        "source_path": str(resource.captured_path),
        "capture_root": str(resource.capture_root),
    }


def _path_presence(path: Path, *, label: str) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode):
        raise RecoveryError("unsafe_resource", f"{label} must be a regular file: {path}")
    return True


def _assert_expected_absence(path: Path, *, label: str) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptors = [os.open(path.anchor, flags)]
    try:
        components = path.parts[1:]
        for index, component in enumerate(components):
            parent = descriptors[-1]
            try:
                metadata = os.stat(component, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise RecoveryError(
                    "unsafe_resource", f"Cannot inspect expected-absent resource {label}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise RecoveryError(
                    "unsafe_resource",
                    f"Expected-absent resource has symlink component: {label}",
                )
            if index == len(components) - 1:
                raise RecoveryError(
                    "unexpected_resource", f"Expected-absent resource exists: {label}"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise RecoveryError(
                    "unsafe_resource",
                    f"Expected-absent resource ancestor is not a directory: {label}",
                )
            child = os.open(component, flags, dir_fd=parent)
            opened = os.fstat(child)
            if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
                os.close(child)
                raise RecoveryError(
                    "source_changed", f"Expected-absent resource ancestor changed: {label}"
                )
            descriptors.append(child)
    except BaseException as error:
        _close_descriptors(descriptors, primary_error=error)
        raise
    else:
        _close_descriptors(descriptors, primary_error=None)


def _capture_database_wal(
    resource: SourceResourceV1,
    *,
    payload: Path,
    state: _CaptureState,
    record: dict[str, object],
    payload_output: _PinnedDirectory,
) -> None:
    wal_source = Path(f"{resource.captured_path}-wal")
    if not state.database_wal_present:
        return
    wal_payload = payload / "database/economy.db-wal"
    state.database_wal_payload = wal_payload
    try:
        wal_copy = _copy_open_file(
            source_path=wal_source,
            destination=wal_payload,
            label="database.wal",
            output_root=payload_output,
        )
    except RecoveryError as exc:
        if exc.reason == "missing_resource":
            raise RecoveryError(
                "source_changed", "Database WAL disappeared during snapshot"
            ) from exc
        raise
    state.open_copies.append(wal_copy)
    state.files.append(_file_entry(wal_payload, payload=payload, role="database.wal"))
    record["wal_payload_path"] = "database/economy.db-wal"
    record["wal_source_identity"] = _source_identity(wal_copy, present=True)


def _source_identity(copied: _OpenCopy, *, present: bool) -> dict[str, object]:
    return {
        "present": present,
        "device": copied.device,
        "inode": copied.inode,
        "size": copied.size,
        "mode": copied.mode,
        "sha256": copied.sha256,
    }


def _capture_metadata_resource(
    resource: SourceResourceV1,
    *,
    inventory: RecoveryInventoryV1,
    state: _CaptureState,
) -> bool:
    if resource.capture_kind is CaptureKind.KEYPAIR:
        return True
    if resource.capture_kind in {
        CaptureKind.ABSENT_FILE,
        CaptureKind.ABSENT_DIRECTORY,
    }:
        _assert_expected_absence(resource.captured_path, label=resource.logical_id)
        state.absent_resources.append((resource.logical_id, resource.captured_path))
        state.resources[resource.logical_id] = {
            "kind": resource.kind.value,
            "capture_kind": resource.capture_kind.value,
            "source_path": str(resource.captured_path),
            "capture_root": str(resource.capture_root),
            "present": False,
        }
        return True
    if resource.capture_kind is not CaptureKind.RUNTIME_LOCK:
        return False
    if resource.captured_path != inventory.maintenance_lock_path:
        raise RecoveryError(
            "incomplete_inventory", "Runtime lock resource path differs from maintenance lock"
        )
    state.resources[resource.logical_id] = {
        "kind": resource.kind.value,
        "capture_kind": resource.capture_kind.value,
        "source_path": str(resource.captured_path),
        "capture_root": str(resource.capture_root),
        "device": state.maintenance_device,
        "inode": state.maintenance_inode,
    }
    return True


def _capture_file_resource(
    resource: SourceResourceV1,
    *,
    inventory: RecoveryInventoryV1,
    payload: Path,
    destination: Path,
    state: _CaptureState,
    record: dict[str, object],
    payload_output: _PinnedDirectory,
) -> None:
    if resource.logical_id == _DATABASE_RESOURCE_ID:
        state.database_source = resource.captured_path
        state.database_wal_present = _path_presence(
            Path(f"{resource.captured_path}-wal"), label="database.wal"
        )
    copied = _copy_open_file(
        source_path=resource.captured_path,
        destination=destination,
        label=resource.logical_id,
        output_root=payload_output,
    )
    state.open_copies.append(copied)
    state.files.append(_file_entry(destination, payload=payload, role=resource.logical_id))
    if resource.capture_kind is CaptureKind.CONFIG:
        configs = [
            config for config in inventory.configs if config.captured_path == resource.captured_path
        ]
        if len(configs) != 1:
            raise RecoveryError(
                "incomplete_inventory",
                f"Config resource lacks exact evidence: {resource.logical_id}",
            )
        if copied.sha256 != configs[0].sha256:
            raise RecoveryError(
                "config_changed",
                f"Config hash differs from captured evidence: {configs[0].logical_id}",
            )
        record["config_id"] = configs[0].logical_id
    if resource.logical_id == _DATABASE_RESOURCE_ID:
        record["source_identity"] = _source_identity(copied, present=True)
        if not state.database_wal_present:
            record["wal_source_identity"] = {"present": False}
        _capture_database_wal(
            resource,
            payload=payload,
            state=state,
            record=record,
            payload_output=payload_output,
        )


def _capture_resource(
    resource: SourceResourceV1,
    *,
    inventory: RecoveryInventoryV1,
    payload: Path,
    state: _CaptureState,
    payload_output: _PinnedDirectory,
) -> None:
    if _capture_metadata_resource(resource, inventory=inventory, state=state):
        return
    relative = _resource_payload_path(
        resource.logical_id,
        kind=resource.capture_kind,
        basename=resource.captured_path.name,
    )
    destination = payload / relative
    record = _resource_record(resource, relative)
    if resource.capture_kind is CaptureKind.DIRECTORY_ONLY:
        _assert_no_symlink_components(
            resource.captured_path,
            label=resource.logical_id,
        )
        if not resource.captured_path.is_dir():
            raise RecoveryError(
                "unsafe_resource", f"Declared directory is invalid: {resource.logical_id}"
            )
        _ensure_directory_under(payload_output, destination.relative_to(payload))
    elif resource.kind is ResourceKind.DIRECTORY:
        entries, copies = _capture_directory(
            source=resource.captured_path,
            destination=destination,
            label=resource.logical_id,
            payload=payload,
            payload_output=payload_output,
        )
        state.files.extend(entries)
        state.open_copies.extend(copies)
    else:
        _capture_file_resource(
            resource,
            inventory=inventory,
            payload=payload,
            destination=destination,
            state=state,
            record=record,
            payload_output=payload_output,
        )
    state.resources[resource.logical_id] = record


def _add_key_resources(
    inventory: RecoveryInventoryV1,
    *,
    payload: Path,
    state: _CaptureState,
    payload_output: _PinnedDirectory,
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    key_files, keypairs, findings = _capture_keypairs(
        inventory, payload=payload, payload_output=payload_output
    )
    state.files.extend(key_files)
    resources = {resource.logical_id: resource for resource in inventory.resources}
    for keypair in inventory.keypairs:
        metadata = keypairs.get(keypair.handle)
        for resource_id, path_field, mode_field in (
            (
                keypair.private_resource_id,
                "private_payload_path",
                "private_restore_mode",
            ),
            (
                keypair.public_resource_id,
                "public_payload_path",
                "public_restore_mode",
            ),
        ):
            if metadata is None:
                state.resources[resource_id] = {
                    "kind": ResourceKind.FILE.value,
                    "capture_kind": CaptureKind.KEYPAIR.value,
                    "source_path": str(resources[resource_id].captured_path),
                    "capture_root": str(resources[resource_id].capture_root),
                    "captured": False,
                }
                continue
            state.resources[resource_id] = {
                "kind": ResourceKind.FILE.value,
                "capture_kind": CaptureKind.KEYPAIR.value,
                "payload_path": metadata[path_field],
                "source_path": str(resources[resource_id].captured_path),
                "capture_root": str(resources[resource_id].capture_root),
                "restore_mode": metadata[mode_field],
                "captured": True,
            }
    for resource in inventory.resources:
        if (
            resource.capture_kind is CaptureKind.KEYPAIR
            and resource.logical_id not in state.resources
        ):
            state.resources[resource.logical_id] = {
                "kind": ResourceKind.FILE.value,
                "capture_kind": CaptureKind.KEYPAIR.value,
                "source_path": str(resource.captured_path),
                "capture_root": str(resource.capture_root),
                "captured": False,
            }
    return keypairs, findings


def _close_and_verify_sources(state: _CaptureState) -> None:
    for copied in state.open_copies:
        _verify_and_close_copy(copied)
    state.open_copies.clear()
    if state.database_source is None or state.database_wal_present is None:
        return
    current_presence = _path_presence(
        Path(f"{state.database_source}-wal"),
        label="database.wal",
    )
    if current_presence != state.database_wal_present:
        raise RecoveryError("source_changed", "Database WAL presence changed during snapshot")


def _verify_absent_resources(state: _CaptureState) -> None:
    for logical_id, path in state.absent_resources:
        _assert_expected_absence(path, label=logical_id)


def _record_required_handle(
    required: dict[str, KeypairRole],
    findings: list[dict[str, object]],
    handle: object,
    *,
    role: KeypairRole,
) -> None:
    if not isinstance(handle, str) or not handle:
        return
    existing = required.get(handle)
    if existing is not None and existing is not role:
        findings.append({"code": "key_role_conflict", "handle": handle})
        return
    required[handle] = role


def _collect_config_key_handles(
    config: dict[str, Any],
    *,
    required: dict[str, KeypairRole],
    findings: list[dict[str, object]],
) -> None:
    auth = config.get("auth")
    if isinstance(auth, dict):
        trusted = auth.get("trusted_callers")
        if isinstance(trusted, dict):
            for handle in trusted:
                _record_required_handle(required, findings, handle, role=KeypairRole.GATEWAY)
    gateway = config.get("db_gateway")
    if isinstance(gateway, dict):
        signing = gateway.get("signing")
        if isinstance(signing, dict):
            _record_required_handle(
                required,
                findings,
                signing.get("kid"),
                role=KeypairRole.GATEWAY,
            )
    user_agent = config.get("user_agent")
    if isinstance(user_agent, dict):
        _record_required_handle(
            required,
            findings,
            user_agent.get("handle"),
            role=KeypairRole.ECONOMIC,
        )
    for section_name in ("treasury", "task_feeder"):
        section = config.get(section_name)
        if isinstance(section, dict):
            _record_required_handle(
                required,
                findings,
                section.get("handle"),
                role=KeypairRole.ECONOMIC,
            )
    workers = config.get("workers")
    if isinstance(workers, dict):
        for worker in workers.values():
            if isinstance(worker, dict):
                _record_required_handle(
                    required,
                    findings,
                    worker.get("handle"),
                    role=KeypairRole.ECONOMIC,
                )


def _get_config_field(mapping: dict[str, Any], field_path: tuple[str | int, ...]) -> object:
    current: object = mapping
    for segment in field_path:
        if isinstance(segment, int):
            current = _list_item(current, segment, final=False)
        else:
            if not isinstance(current, dict) or segment not in current:
                raise RecoveryError("binding_mismatch", "Captured config field is missing")
            current = current[segment]
    return current


def _require_captured_binding_value(
    binding: SourceFieldBindingV1,
    actual: object,
) -> None:
    expected = binding.captured_value
    if binding.binding_kind is BindingKind.PORT:
        matches = type(actual) is int and actual == expected
    elif binding.binding_kind is BindingKind.ORIGINS:
        matches = (
            isinstance(actual, list)
            and all(type(item) is str for item in actual)
            and tuple(actual) == expected
        )
    else:
        matches = type(actual) is str and actual == expected
    if not matches:
        raise RecoveryError(
            "binding_mismatch",
            f"Captured config value differs for {binding.binding_id}",
        )


def _validate_captured_config_bindings(
    inventory: RecoveryInventoryV1,
    *,
    payload: Path,
    resources: dict[str, dict[str, object]],
) -> None:
    for config in inventory.configs:
        records = [
            record for record in resources.values() if record.get("config_id") == config.logical_id
        ]
        if len(records) != 1 or not isinstance(records[0].get("payload_path"), str):
            raise RecoveryError(
                "incomplete_inventory", f"Config capture is incomplete: {config.logical_id}"
            )
        config_bytes, _mode = _read_pinned_regular(
            payload / str(records[0]["payload_path"]), label=f"captured config {config.logical_id}"
        )
        parsed = parse_yaml_config_bytes(config_bytes, source=config.logical_id)
        for binding in inventory.field_bindings:
            if binding.config_id == config.logical_id:
                actual = _get_config_field(parsed, tuple(binding.field_path))
                _require_captured_binding_value(binding, actual)


def _identity_inventory_findings(
    inventory: RecoveryInventoryV1,
    *,
    payload: Path,
    resources: dict[str, dict[str, object]],
    captured_keypairs: dict[str, dict[str, object]],
    files: dict[str, dict[str, Any]] | None,
) -> list[dict[str, object]]:
    required: dict[str, KeypairRole] = {}
    findings: list[dict[str, object]] = []
    for record in resources.values():
        config_id = record.get("config_id")
        payload_path = record.get("payload_path")
        if not isinstance(config_id, str) or not isinstance(payload_path, str):
            continue
        try:
            if files is None:
                config_bytes, _mode = _read_pinned_regular(
                    payload / payload_path, label=f"captured config {config_id}"
                )
            else:
                config_bytes = _read_verified_payload_file(
                    payload,
                    payload_path,
                    files=files,
                    label=f"config {config_id}",
                )
            parsed = parse_yaml_config_bytes(config_bytes, source=config_id)
        except ConfigurationError as exc:
            raise RecoveryError(
                "invalid_config", f"Captured config is invalid: {config_id}"
            ) from exc
        _collect_config_key_handles(parsed, required=required, findings=findings)
    if not required:
        findings.append({"code": "identity_inventory_unverifiable"})
    declared = {keypair.handle: keypair.role for keypair in inventory.keypairs}
    for handle, role in sorted(required.items()):
        if declared.get(handle) is not role:
            findings.append({"code": "missing_keypair_inventory", "handle": handle})
        elif handle not in captured_keypairs:
            findings.append({"code": "missing_key_material", "handle": handle})
    return findings


def _audit_captured_database(
    *,
    payload: Path,
    wal_path: Path | None,
    work_root: _PinnedDirectory,
    files: list[dict[str, object]],
    asset_source_root: Path | None,
    inventory: RecoveryInventoryV1,
    captured_keypairs: dict[str, dict[str, object]],
    payload_output: _PinnedDirectory,
) -> dict[str, object]:
    database = payload / "database/economy.db"
    if not database.exists():
        raise RecoveryError("incomplete_inventory", "Inventory lacks database.primary")
    immutable_paths = tuple(path for path in (database, wal_path) if path is not None)
    immutable_before = {
        path: _sha256_bytes(_read_pinned_regular(path, label="captured database before audit")[0])
        for path in immutable_paths
    }
    asset_root = payload / "assets" if asset_source_root is not None else None
    audit = _audit_database(
        database,
        wal_path,
        work_root=work_root,
        asset_root=asset_root,
        asset_source_root=asset_source_root,
        identity_inventory=inventory,
        captured_keypairs=captured_keypairs,
    )
    for path, digest in immutable_before.items():
        if (
            _sha256_bytes(_read_pinned_regular(path, label="captured database after audit")[0])
            != digest
        ):
            raise RecoveryError("immutable_changed", f"Database audit changed bundle file: {path}")
    audit_path = payload / "audits/pre-migration.json"
    _write_new_file_under(
        payload_output,
        audit_path.relative_to(payload),
        _canonical_json(audit),
        mode=0o600,
    )
    files.append(_file_entry(audit_path, payload=payload, role="audit.pre_migration"))
    return audit


def _capture_payload(
    inventory: RecoveryInventoryV1,
    *,
    payload: Path,
    work_root: _PinnedDirectory,
    held_lock: HeldMaintenanceLock,
    payload_output: _PinnedDirectory,
) -> dict[str, object]:
    state = _CaptureState(
        resources={},
        files=[],
        open_copies=[],
        absent_resources=[],
        maintenance_device=held_lock.device,
        maintenance_inode=held_lock.inode,
    )
    try:
        for resource in inventory.resources:
            _capture_resource(
                resource,
                inventory=inventory,
                payload=payload,
                state=state,
                payload_output=payload_output,
            )
        keypairs, key_findings = _add_key_resources(
            inventory,
            payload=payload,
            state=state,
            payload_output=payload_output,
        )
        _close_and_verify_sources(state)
        _verify_absent_resources(state)
        _validate_captured_config_bindings(
            inventory,
            payload=payload,
            resources=state.resources,
        )
        identity_findings = [
            *key_findings,
            *_identity_inventory_findings(
                inventory,
                payload=payload,
                resources=state.resources,
                captured_keypairs=keypairs,
                files=None,
            ),
        ]
        audit = _audit_captured_database(
            payload=payload,
            wal_path=state.database_wal_payload,
            work_root=work_root,
            files=state.files,
            asset_source_root=(
                Path(str(state.resources[_ASSET_RESOURCE_ID]["source_path"]))
                if _ASSET_RESOURCE_ID in state.resources
                else None
            ),
            inventory=inventory,
            captured_keypairs=keypairs,
            payload_output=payload_output,
        )
        identity_findings.extend(cast("list[dict[str, object]]", audit["identity_findings"]))
        audit_findings = cast("list[dict[str, object]]", audit["findings"])
        finding_codes = {str(item["code"]) for item in [*audit_findings, *identity_findings]}
        return {
            "resources": state.resources,
            "files": sorted(state.files, key=lambda item: str(item["path"])),
            "keypairs": keypairs,
            "identity_findings": identity_findings,
            "audit": audit,
            "audit_sha256": _sha256_bytes(_canonical_json(audit)),
            "states": {
                "snapshot_preserved": True,
                "identity_complete": not identity_findings,
                "migration_eligible": False,
                "operational_restore_verified": False,
            },
            "finding_codes": sorted(finding_codes),
        }
    finally:
        for copied in state.open_copies:
            with suppress(OSError):
                os.close(copied.file_descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    primary_error: BaseException | None = None
    try:
        os.fsync(descriptor)
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            os.close(descriptor)
        except OSError as error:
            if primary_error is not None:
                primary_error.add_note(f"Directory close also failed: {error}")
            else:
                raise RecoveryError(
                    "close_failed", f"Cannot close directory after fsync: {path}"
                ) from error


def _fsync_tree(root: Path) -> None:
    for directory, _subdirectories, files in os.walk(root, topdown=False, followlinks=False):
        directory_path = Path(directory)
        for filename in files:
            path = directory_path / filename
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            primary_error: BaseException | None = None
            try:
                os.fsync(descriptor)
            except BaseException as error:
                primary_error = error
                raise
            finally:
                _close_descriptors([descriptor], primary_error=primary_error)
        _fsync_directory(directory_path)


def _tree_digest(root: Path, *, exclude: frozenset[str]) -> str:
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RecoveryError("unsafe_bundle", f"Bundle contains a symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            entries.append(
                {
                    "path": relative,
                    "kind": "directory",
                    "mode": stat.S_IMODE(metadata.st_mode),
                }
            )
        elif stat.S_ISREG(metadata.st_mode):
            data, mode = _read_pinned_regular(path, label=f"tree file {relative}")
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "size": len(data),
                    "mode": mode,
                    "sha256": _sha256_bytes(data),
                }
            )
        else:
            raise RecoveryError("unsafe_bundle", f"Bundle contains a special file: {relative}")
    return _sha256_bytes(_canonical_json(entries))


def _remove_empty_attempt_pinned(parent: _PinnedDirectory, name: str) -> None:
    os.rmdir(name, dir_fd=parent.descriptor)
    os.fsync(parent.descriptor)
    parent.verify()


def _attempts_directory(
    backup_output: _PinnedDirectory,
) -> tuple[_PinnedDirectory, bool]:
    try:
        attempts = _create_pinned_child(backup_output, ".attempts", mode=0o700)
        created = True
    except RecoveryError as exc:
        if exc.reason != "already_exists":
            raise
        attempts = _open_pinned_child(backup_output, ".attempts")
        created = False
    if stat.S_IMODE(os.fstat(attempts.descriptor).st_mode) != 0o700:
        error = RecoveryError("unsafe_mode", "Attempt report directory must have mode 0700")
        attempts.close(primary_error=error)
        raise error
    return attempts, created


def _write_attempt_report(
    *,
    backup_output: _PinnedDirectory,
    attempt_id: str,
    backup_id: str,
    stage: str,
    error_code: str,
) -> None:
    attempts, _created = _attempts_directory(backup_output)
    primary_error: BaseException | None = None
    report = {
        "attempt_id": attempt_id,
        "backup_id": backup_id,
        "stage": stage,
        "error_code": error_code,
    }
    try:
        temporary = f".{attempt_id}.tmp"
        final = f"{attempt_id}.json"
        _write_new_file_under(attempts, Path(temporary), _canonical_json(report), mode=0o600)
        _link_between(attempts, temporary, attempts, final)
        os.unlink(temporary, dir_fd=attempts.descriptor)
        os.fsync(attempts.descriptor)
        os.fsync(backup_output.descriptor)
        backup_output.verify()
    except BaseException as error:
        primary_error = error
        raise
    finally:
        attempts.close(primary_error=primary_error)


def _attempt_error_code(error: BaseException) -> str:
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return "cancelled"
    if isinstance(error, RecoveryError):
        return error.reason
    return "unexpected_failure"


def _record_attempt_failure(
    error: BaseException,
    *,
    backup_output: _PinnedDirectory,
    attempt_id: str,
    backup_id: str,
    stage: str,
) -> None:
    try:
        _write_attempt_report(
            backup_output=backup_output,
            attempt_id=attempt_id,
            backup_id=backup_id,
            stage=stage,
            error_code=_attempt_error_code(error),
        )
    except BaseException as report_error:
        error.add_note(f"Attempt report publication also failed: {report_error}")


def _finalize_marker_alias_pinned(root: _PinnedDirectory, marker: dict[str, Any]) -> None:
    alias_name = marker.get("temporary_marker_name")
    if not isinstance(alias_name, str) or not alias_name.startswith(".COMPLETE-"):
        raise RecoveryError("invalid_manifest", "Completion marker alias name is invalid")
    _validate_identifier(alias_name, name="temporary_marker_name")
    try:
        alias = os.stat(alias_name, dir_fd=root.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    complete = os.stat("COMPLETE", dir_fd=root.descriptor, follow_symlinks=False)
    if (
        not stat.S_ISREG(alias.st_mode)
        or alias.st_dev != complete.st_dev
        or alias.st_ino != complete.st_ino
    ):
        raise RecoveryError("unsafe_bundle", "Completion marker alias identity differs")
    try:
        os.unlink(alias_name, dir_fd=root.descriptor)
        os.fsync(root.descriptor)
        root.verify()
    except OSError as exc:
        raise RecoveryError(
            "publication_uncertain", "Completion marker alias cleanup is not durable"
        ) from exc


def _publish_marker_pinned(
    root: _PinnedDirectory,
    parent: _PinnedDirectory,
    *,
    backup_id: str,
    manifest_sha256: str,
    payload_sha256: str,
) -> PublicationResult:
    root.verify()
    _require_directory_mode(root.path, mode=0o700, name="published recovery root")
    temporary_name = f".COMPLETE-{uuid.uuid4().hex}"
    marker = {
        "backup_id": backup_id,
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "manifest_sha256": manifest_sha256,
        "payload_sha256": payload_sha256,
        "temporary_marker_name": temporary_name,
    }
    _write_new_file_under(
        root,
        Path(temporary_name),
        _canonical_json(marker),
        mode=0o600,
    )
    try:
        _link_between(root, temporary_name, root, "COMPLETE")
    except OSError as exc:
        try:
            os.stat("COMPLETE", dir_fd=root.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            raise RecoveryError("publication_failed", "Cannot publish completion marker") from exc
        return PublicationResult(
            state=PublicationState.PUBLICATION_UNCERTAIN,
            location=root.path,
            manifest_sha256=manifest_sha256,
        )
    try:
        os.unlink(temporary_name, dir_fd=root.descriptor)
        os.fsync(root.descriptor)
        os.fsync(parent.descriptor)
        root.verify()
        parent.verify()
    except OSError:
        return PublicationResult(
            state=PublicationState.PUBLICATION_UNCERTAIN,
            location=root.path,
            manifest_sha256=manifest_sha256,
        )
    return PublicationResult(
        state=PublicationState.COMPLETE,
        location=root.path,
        manifest_sha256=manifest_sha256,
    )


def _capture_under_maintenance_lock(
    inventory: RecoveryInventoryV1,
    *,
    payload: Path,
    work_root: _PinnedDirectory,
    capture_root_identities: dict[Path, tuple[int, int]],
    payload_output: _PinnedDirectory,
) -> dict[str, object]:
    try:
        expected_lock = inventory.maintenance_lock_identity
        with hold_existing_maintenance_lock(
            inventory.maintenance_lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=expected_lock.device,
            expected_inode=expected_lock.inode,
        ) as held_lock:
            _verify_capture_roots(capture_root_identities)
            with ExitStack() as identity_locks:
                identity_snapshot, held_identity_locks = _hold_agent_runtime_identity_locks(
                    inventory,
                    identity_locks,
                )
                captured = _capture_payload(
                    inventory,
                    payload=payload,
                    work_root=work_root,
                    held_lock=held_lock,
                    payload_output=payload_output,
                )
                _verify_agent_runtime_identity_locks(
                    inventory,
                    identity_snapshot,
                    held_identity_locks,
                )
            held_lock.verify_identity()
            _verify_capture_roots(capture_root_identities)
            return captured
    except MaintenanceLockError as exc:
        reasons = {
            "busy": "runtime_active",
            "identity_mismatch": "maintenance_lock_identity",
        }
        reason = reasons.get(exc.reason, "maintenance_lock_failed")
        raise RecoveryError(reason, f"Cannot establish offline maintenance: {exc}") from exc


def _reserve_backup_attempt(
    backup_output: _PinnedDirectory,
    *,
    backup_id: str,
    attempt_id: str,
) -> tuple[_PinnedDirectory, _PinnedDirectory, _PinnedDirectory]:
    final = _create_pinned_child(backup_output, backup_id, mode=0o700)
    attempt: _PinnedDirectory | None = None
    payload: _PinnedDirectory | None = None
    try:
        attempt = _create_pinned_child(final, f".attempt-{attempt_id}", mode=0o700)
        payload = _create_pinned_child(final, "payload", mode=0o700)
    except BaseException as error:
        if payload is not None:
            payload.close(primary_error=error)
        if attempt is not None:
            attempt.close(primary_error=error)
        final.close(primary_error=error)
        raise
    return final, attempt, payload


def _write_backup_manifest(
    payload_output: _PinnedDirectory,
    inventory: RecoveryInventoryV1,
    captured: dict[str, object],
    *,
    backup_id: str,
) -> str:
    manifest = {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "backup_id": backup_id,
        "inventory": json.loads(canonical_json_bytes(inventory)),
        **captured,
    }
    manifest_bytes = _canonical_json(manifest)
    digest = _sha256_bytes(manifest_bytes)
    _write_new_file_under(payload_output, Path("manifest.json"), manifest_bytes, mode=0o600)
    _write_new_file_under(
        payload_output,
        Path("manifest.sha256"),
        f"{digest}\n".encode(),
        mode=0o600,
    )
    return digest


def _create_backup_with_workspace(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    work_output: _PinnedDirectory,
) -> PublicationResult:
    """Capture and publish an immutable offline backup under an exclusive lock."""

    if not isinstance(inventory, RecoveryInventoryV1):
        raise TypeError("inventory must be RecoveryInventoryV1")
    _validate_backup_id(backup_id)
    _validate_inventory_completeness(inventory)
    _validate_process_quiescence(inventory)
    _validate_roots(inventory, backup_root=backup_root, work_root=work_root)
    capture_root_identities = _validate_source_provenance(inventory)
    attempt_id = uuid.uuid4().hex
    stage = "reserve"
    backup_output = _open_pinned_directory(backup_root, label="backup root")
    final_output: _PinnedDirectory | None = None
    attempt_output: _PinnedDirectory | None = None
    payload_output: _PinnedDirectory | None = None
    primary_error: BaseException | None = None
    try:
        attempt_name = f".attempt-{attempt_id}"
        final_output, attempt_output, payload_output = _reserve_backup_attempt(
            backup_output, backup_id=backup_id, attempt_id=attempt_id
        )
        payload = payload_output.path
        stage = "capture"
        captured = _capture_under_maintenance_lock(
            inventory,
            payload=payload,
            work_root=work_output,
            capture_root_identities=capture_root_identities,
            payload_output=payload_output,
        )
        stage = "manifest"
        manifest_digest = _write_backup_manifest(
            payload_output, inventory, captured, backup_id=backup_id
        )
        stage = "payload_publication"
        _fsync_tree(payload)
        payload_output.verify()
        if stat.S_IMODE(os.fstat(payload_output.descriptor).st_mode) != 0o700:
            raise RecoveryError("unsafe_mode", "Backup payload directory must have mode 0700")
        os.fsync(final_output.descriptor)
        final_output.verify()
        stage = "attempt_cleanup"
        _remove_empty_attempt_pinned(final_output, attempt_name)
        final_payload = final_output.path / "payload"
        payload_digest = _tree_digest(final_payload, exclude=frozenset())
        stage = "completion_marker"
        return _publish_marker_pinned(
            final_output,
            backup_output,
            backup_id=backup_id,
            manifest_sha256=manifest_digest,
            payload_sha256=payload_digest,
        )
    except OSError as exc:
        error = RecoveryError("publication_failed", f"Backup failed during {stage}")
        primary_error = error
        _record_attempt_failure(
            error,
            backup_output=backup_output,
            attempt_id=attempt_id,
            backup_id=backup_id,
            stage=stage,
        )
        raise error from exc
    except BaseException as error:
        primary_error = error
        _record_attempt_failure(
            error,
            backup_output=backup_output,
            attempt_id=attempt_id,
            backup_id=backup_id,
            stage=stage,
        )
        raise
    finally:
        _close_pinned_directories(
            [
                directory
                for directory in (
                    backup_output,
                    final_output,
                    attempt_output,
                    payload_output,
                )
                if directory is not None
            ],
            primary_error=primary_error,
        )


def create_backup(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
) -> PublicationResult:
    """Capture and publish an immutable offline backup under workspace leases."""

    if not isinstance(inventory, RecoveryInventoryV1):
        raise TypeError("inventory must be RecoveryInventoryV1")
    _validate_backup_id(backup_id)
    _validate_inventory_completeness(inventory)
    _validate_process_quiescence(inventory)
    _validate_root_authorization(inventory, backup_root=backup_root, work_root=work_root)
    with _hold_recovery_workspace(work_root) as workspace:
        return _create_backup_with_workspace(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            work_output=workspace.root,
        )


def _read_json_regular(path: Path, *, missing_reason: str) -> dict[str, Any]:
    try:
        data, mode = _read_pinned_regular(path, label="recovery JSON")
    except RecoveryError as exc:
        if exc.reason == "missing_resource":
            raise RecoveryError(
                missing_reason, f"Required recovery file is missing: {path}"
            ) from exc
        raise RecoveryError("unsafe_bundle", f"Recovery file is unsafe: {path}") from exc
    if mode != 0o600:
        raise RecoveryError("unsafe_mode", f"Recovery JSON mode must be 0600: {path}")
    try:
        value = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RecoveryError("invalid_manifest", f"Recovery JSON is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise RecoveryError("invalid_manifest", f"Recovery JSON must be an object: {path}")
    return cast("dict[str, Any]", value)


def _read_pinned_regular(path: Path, *, label: str) -> tuple[bytes, int]:
    descriptor = _open_source_file(path, label=label)
    primary_error: BaseException | None = None
    try:
        before = os.fstat(descriptor)
        data = _read_descriptor(descriptor)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(after.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mode != after.st_mode
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or after.st_dev != current.st_dev
            or after.st_ino != current.st_ino
            or after.st_mode != current.st_mode
            or after.st_mtime_ns != current.st_mtime_ns
            or after.st_ctime_ns != current.st_ctime_ns
            or len(data) != after.st_size
        ):
            raise RecoveryError("source_changed", f"Pinned file changed while reading: {label}")
        return data, stat.S_IMODE(after.st_mode)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            if primary_error is not None:
                primary_error.add_note(f"Pinned file close also failed: {exc}")
            else:
                raise RecoveryError("read_failed", f"Cannot close pinned file: {label}") from exc


def _verify_manifest_files(payload: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RecoveryError("invalid_manifest", "Manifest files must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise RecoveryError("invalid_manifest", "Manifest file entry is invalid")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise RecoveryError("invalid_manifest", "Manifest file path is unsafe")
        if relative.as_posix() in indexed:
            raise RecoveryError("invalid_manifest", "Manifest file paths must be unique")
        path = payload / relative
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise RecoveryError("missing_file", f"Bundle file is missing: {relative}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise RecoveryError("unsafe_bundle", f"Bundle file is unsafe: {relative}")
        data, mode = _read_pinned_regular(path, label=f"manifest file {relative}")
        if len(data) != entry.get("size") or _sha256_bytes(data) != entry.get("sha256"):
            raise RecoveryError("digest_mismatch", f"Bundle file digest differs: {relative}")
        if mode != entry.get("mode"):
            raise RecoveryError("mode_mismatch", f"Bundle file mode differs: {relative}")
        indexed[relative.as_posix()] = cast("dict[str, Any]", entry)
    return indexed


def _safe_payload_relative(value: object, *, field_name: str) -> Path:
    if not isinstance(value, str):
        raise RecoveryError("invalid_manifest", f"{field_name} must be a string")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RecoveryError("invalid_manifest", f"{field_name} is unsafe")
    return relative


def _validate_metadata_manifest_resource(
    resource: SourceResourceV1,
    record: object,
) -> set[str] | None:
    if not isinstance(record, dict):
        raise RecoveryError("invalid_manifest", "Manifest resource record is invalid")
    if (
        record.get("kind") != resource.kind.value
        or record.get("capture_kind") != resource.capture_kind.value
        or record.get("source_path") != str(resource.captured_path)
        or record.get("capture_root") != str(resource.capture_root)
    ):
        raise RecoveryError("invalid_manifest", f"Resource contract differs: {resource.logical_id}")
    if resource.capture_kind in {
        CaptureKind.ABSENT_FILE,
        CaptureKind.ABSENT_DIRECTORY,
    }:
        if record.get("present") is not False or "payload_path" in record:
            raise RecoveryError("invalid_manifest", "Absent-resource evidence is invalid")
        return set()
    if resource.capture_kind is CaptureKind.RUNTIME_LOCK:
        if "payload_path" in record:
            raise RecoveryError("invalid_manifest", "Runtime-lock evidence contains bytes")
        if type(record.get("device")) is not int or type(record.get("inode")) is not int:
            raise RecoveryError("invalid_manifest", "Runtime-lock identity is invalid")
        return set()
    if record.get("captured") is False:
        if resource.capture_kind is not CaptureKind.KEYPAIR:
            raise RecoveryError("invalid_manifest", "Only key resources may be uncaptured")
        return set()
    return None


def _validate_source_file_identity(
    value: object,
    evidence: dict[str, Any],
    *,
    label: str,
) -> bool:
    if not isinstance(value, dict) or value.get("present") is not True:
        raise RecoveryError("invalid_manifest", f"{label} source identity is invalid")
    for field in ("device", "inode", "size", "mode"):
        if type(value.get(field)) is not int:
            raise RecoveryError("invalid_manifest", f"{label} source identity is invalid")
    if (
        value.get("size") != evidence.get("size")
        or value.get("mode") != evidence.get("mode")
        or value.get("sha256") != evidence.get("sha256")
    ):
        raise RecoveryError("invalid_manifest", f"{label} source identity differs")
    return True


def _validate_database_source_identity(
    record: dict[str, Any],
    main_evidence: dict[str, Any],
    *,
    files: dict[str, dict[str, Any]],
) -> set[str]:
    _validate_source_file_identity(record.get("source_identity"), main_evidence, label="database")
    wal_identity = record.get("wal_source_identity")
    if not isinstance(wal_identity, dict) or type(wal_identity.get("present")) is not bool:
        raise RecoveryError("invalid_manifest", "Database WAL identity is invalid")
    wal_value = record.get("wal_payload_path")
    if wal_identity["present"] is False:
        if wal_value is not None:
            raise RecoveryError("invalid_manifest", "Absent WAL has payload evidence")
        return set()
    wal_relative = _safe_payload_relative(wal_value, field_name="wal_payload_path")
    wal_evidence = files.get(wal_relative.as_posix())
    if wal_evidence is None or wal_evidence.get("role") != "database.wal":
        raise RecoveryError("invalid_manifest", "Database WAL evidence differs")
    _validate_source_file_identity(wal_identity, wal_evidence, label="database WAL")
    return {wal_relative.as_posix()}


def _validate_manifest_resource(
    resource: SourceResourceV1,
    record: object,
    *,
    payload: Path,
    files: dict[str, dict[str, Any]],
) -> set[str]:
    metadata_result = _validate_metadata_manifest_resource(resource, record)
    if metadata_result is not None:
        return metadata_result
    if not isinstance(record, dict):
        raise RecoveryError("invalid_manifest", "Manifest resource record is invalid")
    relative = _safe_payload_relative(
        record.get("payload_path"), field_name="resource payload_path"
    )
    accounted: set[str] = set()
    if resource.kind is ResourceKind.FILE:
        entry = files.get(relative.as_posix())
        if entry is None or entry.get("role") != resource.logical_id:
            raise RecoveryError(
                "invalid_manifest", f"Resource file evidence differs: {resource.logical_id}"
            )
        accounted.add(relative.as_posix())
        if resource.logical_id == _DATABASE_RESOURCE_ID:
            accounted.update(_validate_database_source_identity(record, entry, files=files))
    else:
        resource_path = payload / relative
        _require_real_directory(resource_path, name=f"resource {resource.logical_id}")
        descendants = {
            path.relative_to(payload).as_posix()
            for path in resource_path.rglob("*")
            if path.is_file()
        }
        if resource.capture_kind is CaptureKind.DIRECTORY_ONLY and descendants:
            raise RecoveryError("invalid_manifest", "Directory-only payload is not empty")
        if any(files.get(path, {}).get("role") != resource.logical_id for path in descendants):
            raise RecoveryError("invalid_manifest", "Directory resource file evidence differs")
        accounted.update(descendants)
    if resource.logical_id != _DATABASE_RESOURCE_ID and any(
        field in record for field in ("source_identity", "wal_source_identity", "wal_payload_path")
    ):
        raise RecoveryError("invalid_manifest", "Non-database resource has DB identity")
    return accounted


def _validate_manifest_configs(
    inventory: RecoveryInventoryV1,
    records: dict[str, object],
    *,
    payload: Path,
    files: dict[str, dict[str, Any]],
) -> None:
    config_ids: set[str] = set()
    resources_by_id = {resource.logical_id: resource for resource in inventory.resources}
    for resource_id, record_value in records.items():
        resource = resources_by_id[resource_id]
        if resource.capture_kind is not CaptureKind.CONFIG:
            continue
        if not isinstance(record_value, dict):
            raise RecoveryError("invalid_manifest", "Config resource record is invalid")
        config_id = record_value.get("config_id")
        if not isinstance(config_id, str) or config_id in config_ids:
            raise RecoveryError("invalid_manifest", "Config identity mapping is invalid")
        config_ids.add(config_id)
        matching = tuple(config for config in inventory.configs if config.logical_id == config_id)
        if (
            len(matching) != 1
            or matching[0].captured_path != resource.captured_path
            or matching[0].capture_root != resource.capture_root
        ):
            raise RecoveryError("invalid_manifest", "Config source mapping differs")
        config_bytes = _read_verified_payload_file(
            payload,
            record_value.get("payload_path"),
            files=files,
            label=f"config {config_id}",
        )
        if _sha256_bytes(config_bytes) != matching[0].sha256:
            raise RecoveryError("config_changed", f"Config digest differs: {config_id}")
    if config_ids != {config.logical_id for config in inventory.configs}:
        raise RecoveryError("invalid_manifest", "Config inventory is incomplete")


def _validate_manifest_resources(
    payload: Path,
    manifest: dict[str, Any],
    *,
    files: dict[str, dict[str, Any]],
) -> RecoveryInventoryV1:
    try:
        inventory = RecoveryInventoryV1.model_validate_json(_canonical_json(manifest["inventory"]))
    except (KeyError, ValueError) as exc:
        raise RecoveryError("invalid_manifest", "Embedded recovery inventory is invalid") from exc
    _validate_inventory_completeness(inventory)
    records = manifest.get("resources")
    if not isinstance(records, dict):
        raise RecoveryError("invalid_manifest", "Manifest resources must be an object")
    expected_ids = {resource.logical_id for resource in inventory.resources}
    if set(records) != expected_ids:
        raise RecoveryError("invalid_manifest", "Manifest resource inventory differs")
    accounted = {"audits/pre-migration.json"}
    for resource in inventory.resources:
        accounted.update(
            _validate_manifest_resource(
                resource,
                records[resource.logical_id],
                payload=payload,
                files=files,
            )
        )
        if resource.capture_kind is CaptureKind.RUNTIME_LOCK:
            record = cast("dict[str, Any]", records[resource.logical_id])
            expected_lock = inventory.maintenance_lock_identity
            if (
                record.get("device") != expected_lock.device
                or record.get("inode") != expected_lock.inode
            ):
                raise RecoveryError(
                    "invalid_manifest", "Runtime-lock identity differs from inventory"
                )
    _validate_manifest_configs(inventory, records, payload=payload, files=files)
    if set(files) != accounted:
        raise RecoveryError("invalid_manifest", "Manifest contains uninventoried files")
    return inventory


def _read_verified_payload_file(
    payload: Path,
    relative_value: object,
    *,
    files: dict[str, dict[str, Any]],
    label: str,
) -> bytes:
    relative = _safe_payload_relative(relative_value, field_name=f"{label} payload path")
    evidence = files.get(relative.as_posix())
    if evidence is None:
        raise RecoveryError("invalid_manifest", f"Missing file evidence for {label}")
    data, mode = _read_pinned_regular(payload / relative, label=label)
    if len(data) != evidence.get("size") or _sha256_bytes(data) != evidence.get("sha256"):
        raise RecoveryError("digest_mismatch", f"Verified file changed: {label}")
    if mode != evidence.get("mode"):
        raise RecoveryError("mode_mismatch", f"Verified file mode changed: {label}")
    return data


def _verify_keypair_evidence(
    inventory: RecoveryInventoryV1,
    *,
    manifest: dict[str, Any],
    payload: Path,
    files: dict[str, dict[str, Any]],
    work_root: _PinnedDirectory,
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    metadata_value = manifest.get("keypairs")
    resources_value = manifest.get("resources")
    if not isinstance(metadata_value, dict) or not isinstance(resources_value, dict):
        raise RecoveryError("invalid_manifest", "Keypair evidence is invalid")
    metadata = cast("dict[str, dict[str, object]]", metadata_value)
    resources = cast("dict[str, dict[str, object]]", resources_value)
    findings: list[dict[str, object]] = []
    for keypair in inventory.keypairs:
        pair = metadata.get(keypair.handle)
        if pair is None:
            findings.append({"code": "missing_key_material", "handle": keypair.handle})
            continue
        if (
            pair.get("role") != keypair.role.value
            or pair.get("private_resource_id") != keypair.private_resource_id
            or pair.get("public_resource_id") != keypair.public_resource_id
        ):
            raise RecoveryError("invalid_manifest", "Keypair metadata references differ")
        private_record = resources[keypair.private_resource_id]
        public_record = resources[keypair.public_resource_id]
        if private_record.get("restore_mode") != 0o600:
            raise RecoveryError("unsafe_mode", "Private key restore mode must be 0600")
        public_mode = public_record.get("restore_mode")
        if type(public_mode) is not int or public_mode not in {0o600, 0o644}:
            raise RecoveryError("unsafe_mode", "Public key restore mode is invalid")
        if (
            pair.get("private_restore_mode") != private_record.get("restore_mode")
            or pair.get("public_restore_mode") != public_mode
        ):
            raise RecoveryError("invalid_manifest", "Keypair restore modes differ")
        if pair.get("private_payload_path") != private_record.get("payload_path") or pair.get(
            "public_payload_path"
        ) != public_record.get("payload_path"):
            raise RecoveryError("invalid_manifest", "Keypair payload paths differ")
        private_bytes = _read_verified_payload_file(
            payload,
            private_record.get("payload_path"),
            files=files,
            label=f"private key {keypair.handle}",
        )
        public_bytes = _read_verified_payload_file(
            payload,
            public_record.get("payload_path"),
            files=files,
            label=f"public key {keypair.handle}",
        )
        with _owned_scratch_directory(work_root, prefix="mp06-key-audit-") as scratch:
            private_relative = Path(f"{keypair.handle}.key")
            public_relative = Path(f"{keypair.handle}.pub")
            private_path = scratch.access_path / private_relative
            public_path = scratch.access_path / public_relative
            _write_new_file_under(scratch.directory, private_relative, private_bytes, mode=0o600)
            _write_new_file_under(scratch.directory, public_relative, public_bytes, mode=0o600)
            try:
                private_key = load_private_key(private_path)
                public_key = load_public_key(public_path)
            except KeyMaterialError:
                findings.append({"code": "invalid_key_material", "handle": keypair.handle})
                continue
        derived = public_key_to_b64(private_key.public_key())
        stored = public_key_to_b64(public_key)
        if derived != stored:
            findings.append({"code": "mismatched_keypair", "handle": keypair.handle})
        raw_public = base64.b64decode(stored, validate=True)
        fingerprint = _sha256_bytes(raw_public)
        if (
            fingerprint != keypair.expected_public_fingerprint_sha256
            or pair.get("fingerprint") != fingerprint
        ):
            findings.append({"code": "captured_fingerprint_mismatch", "handle": keypair.handle})
    unexpected = set(metadata) - {keypair.handle for keypair in inventory.keypairs}
    if unexpected:
        raise RecoveryError("invalid_manifest", "Manifest has unexpected keypair metadata")
    return metadata, findings


def _load_verified_backup_manifest(
    *,
    backup_root: Path,
    backup_id: str,
) -> tuple[Path, dict[str, Any], str, dict[str, dict[str, Any]]]:
    _validate_backup_id(backup_id)
    root = backup_root / backup_id
    marker = _read_json_regular(root / "COMPLETE", missing_reason="incomplete_bundle")
    if marker.get("bundle_format_version") != BUNDLE_FORMAT_VERSION:
        raise RecoveryError("unknown_format", "Unsupported backup bundle format")
    if marker.get("backup_id") != backup_id:
        raise RecoveryError("marker_mismatch", "Completion marker backup ID differs")
    marker_alias = marker.get("temporary_marker_name")
    if not isinstance(marker_alias, str) or not marker_alias.startswith(".COMPLETE-"):
        raise RecoveryError("invalid_manifest", "Completion marker alias name is invalid")
    _validate_identifier(marker_alias, name="temporary_marker_name")
    payload = root / "payload"
    _require_real_directory(payload, name="backup payload")
    _require_directory_mode(payload, mode=0o700, name="backup payload")
    if _tree_digest(payload, exclude=frozenset()) != marker.get("payload_sha256"):
        raise RecoveryError("digest_mismatch", "Payload digest differs from completion marker")
    manifest_bytes, _manifest_mode = _read_pinned_regular(
        payload / "manifest.json", label="backup manifest"
    )
    manifest_digest = _sha256_bytes(manifest_bytes)
    if manifest_digest != marker.get("manifest_sha256"):
        raise RecoveryError("digest_mismatch", "Manifest digest differs from completion marker")
    digest_bytes, _digest_mode = _read_pinned_regular(
        payload / "manifest.sha256", label="manifest digest"
    )
    if digest_bytes != f"{manifest_digest}\n".encode():
        raise RecoveryError("digest_mismatch", "Manifest digest file differs")
    try:
        manifest_value = json.loads(manifest_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RecoveryError("invalid_manifest", "Manifest JSON is invalid") from exc
    if not isinstance(manifest_value, dict):
        raise RecoveryError("invalid_manifest", "Manifest must be an object")
    manifest = cast("dict[str, Any]", manifest_value)
    if manifest.get("bundle_format_version") != BUNDLE_FORMAT_VERSION:
        raise RecoveryError("unknown_format", "Unsupported manifest bundle format")
    if manifest.get("backup_id") != backup_id:
        raise RecoveryError("marker_mismatch", "Manifest backup ID differs")
    files = _verify_manifest_files(payload, manifest)
    _validate_manifest_resources(payload, manifest, files=files)
    return payload, manifest, manifest_digest, files


def _verify_audit(
    payload: Path,
    *,
    manifest: dict[str, Any],
    work_root: _PinnedDirectory,
) -> dict[str, object]:
    database = payload / "database/economy.db"
    wal = payload / "database/economy.db-wal"
    immutable_before = {
        path: _sha256_bytes(_read_pinned_regular(path, label="audit source before")[0])
        for path in (database, wal)
        if path.exists()
    }
    resources = cast("dict[str, dict[str, Any]]", manifest["resources"])
    asset_record = resources.get(_ASSET_RESOURCE_ID)
    try:
        inventory = RecoveryInventoryV1.model_validate_json(_canonical_json(manifest["inventory"]))
    except (KeyError, ValueError) as exc:
        raise RecoveryError("invalid_manifest", "Embedded recovery inventory is invalid") from exc
    keypairs = manifest.get("keypairs")
    if not isinstance(keypairs, dict):
        raise RecoveryError("invalid_manifest", "Captured keypair evidence is invalid")
    audit = _audit_database(
        database,
        wal if wal.exists() else None,
        work_root=work_root,
        asset_root=payload / "assets" if asset_record is not None else None,
        asset_source_root=(
            Path(str(asset_record["source_path"])) if asset_record is not None else None
        ),
        identity_inventory=inventory,
        captured_keypairs=cast("dict[str, dict[str, object]]", keypairs),
    )
    for path, digest in immutable_before.items():
        if _sha256_bytes(_read_pinned_regular(path, label="audit source after")[0]) != digest:
            raise RecoveryError(
                "immutable_changed", "Verification changed immutable database bytes"
            )
    audit_bytes = _canonical_json(audit)
    captured_audit, _audit_mode = _read_pinned_regular(
        payload / "audits/pre-migration.json", label="captured audit"
    )
    if audit_bytes != captured_audit:
        raise RecoveryError("audit_mismatch", "Recomputed pre-migration audit differs")
    if manifest.get("audit_sha256") != _sha256_bytes(audit_bytes):
        raise RecoveryError("audit_mismatch", "Manifest audit digest differs")
    return audit


def _capture_marker_evidence(
    root: Path,
    *,
    backup_id: str,
    manifest_digest: str,
    payload_digest: str,
) -> tuple[bytes, int, tuple[int, int], str, bool]:
    marker_path = root / "COMPLETE"
    marker_bytes, marker_mode = _read_pinned_regular(marker_path, label="completion marker")
    marker_metadata = _authority_lstat(marker_path, label="completion marker")
    try:
        marker_value = json.loads(marker_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RecoveryError("invalid_manifest", "Completion marker JSON is invalid") from exc
    if not isinstance(marker_value, dict):
        raise RecoveryError("invalid_manifest", "Completion marker must be an object")
    marker = cast("dict[str, Any]", marker_value)
    if (
        marker_mode != 0o600
        or marker.get("backup_id") != backup_id
        or marker.get("manifest_sha256") != manifest_digest
        or marker.get("payload_sha256") != payload_digest
    ):
        raise RecoveryError("marker_mismatch", "Completion marker evidence differs")
    marker_alias_value = marker.get("temporary_marker_name")
    if not isinstance(marker_alias_value, str) or not marker_alias_value.startswith(".COMPLETE-"):
        raise RecoveryError("invalid_manifest", "Completion marker alias name is invalid")
    _validate_identifier(marker_alias_value, name="temporary_marker_name")
    alias_path = root / marker_alias_value
    try:
        alias_path.lstat()
    except FileNotFoundError:
        marker_alias_present = False
    except OSError as exc:
        raise RecoveryError(
            "source_changed", "Completion marker alias cannot be inspected"
        ) from exc
    else:
        marker_alias_present = True
    marker_identity = (marker_metadata.st_dev, marker_metadata.st_ino)
    if marker_alias_present:
        alias_bytes, alias_mode = _read_pinned_regular(alias_path, label="completion marker alias")
        alias_metadata = _authority_lstat(alias_path, label="completion marker alias")
        if (
            alias_bytes != marker_bytes
            or alias_mode != marker_mode
            or (alias_metadata.st_dev, alias_metadata.st_ino) != marker_identity
        ):
            raise RecoveryError("marker_mismatch", "Completion marker alias differs")
    return (
        marker_bytes,
        marker_mode,
        marker_identity,
        marker_alias_value,
        marker_alias_present,
    )


def _verify_backup_data_with_work_root(
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    work_output: _PinnedDirectory,
) -> _VerifiedBackup:
    _validate_verify_roots(
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    work_output.verify()
    payload, manifest, manifest_digest, files = _load_verified_backup_manifest(
        backup_root=backup_root,
        backup_id=backup_id,
    )
    audit = _verify_audit(payload, manifest=manifest, work_root=work_output)
    try:
        inventory = RecoveryInventoryV1.model_validate_json(_canonical_json(manifest["inventory"]))
    except (KeyError, ValueError) as exc:
        raise RecoveryError("invalid_manifest", "Embedded recovery inventory is invalid") from exc
    keypairs, key_findings = _verify_keypair_evidence(
        inventory,
        manifest=manifest,
        payload=payload,
        files=files,
        work_root=work_output,
    )
    resources = cast("dict[str, dict[str, object]]", manifest["resources"])
    config_findings = _identity_inventory_findings(
        inventory,
        payload=payload,
        resources=resources,
        captured_keypairs=keypairs,
        files=files,
    )
    audit_identity = cast("list[dict[str, object]]", audit["identity_findings"])
    identity_findings = [*key_findings, *config_findings, *audit_identity]
    audit_findings = cast("list[dict[str, object]]", audit["findings"])
    finding_codes = sorted(
        {str(finding["code"]) for finding in [*audit_findings, *identity_findings]}
    )
    root = backup_root / backup_id
    root_metadata = _authority_lstat(root, label="verified backup directory")
    payload_metadata = _authority_lstat(payload, label="verified payload directory")
    result = VerificationResult(
        artifact_verified=True,
        snapshot_preserved=True,
        identity_complete=not identity_findings,
        migration_eligible=False,
        operational_restore_verified=False,
        finding_codes=tuple(finding_codes),
        manifest_sha256=manifest_digest,
    )
    payload_digest = _tree_digest(payload, exclude=frozenset())
    marker_bytes, marker_mode, marker_identity, marker_alias, marker_alias_present = (
        _capture_marker_evidence(
            root,
            backup_id=backup_id,
            manifest_digest=manifest_digest,
            payload_digest=payload_digest,
        )
    )
    work_output.verify()
    return _VerifiedBackup(
        result=result,
        manifest=manifest,
        root=root,
        payload=payload,
        files=files,
        root_identity=(root_metadata.st_dev, root_metadata.st_ino),
        payload_identity=(payload_metadata.st_dev, payload_metadata.st_ino),
        payload_sha256=payload_digest,
        marker_bytes=marker_bytes,
        marker_mode=marker_mode,
        marker_identity=marker_identity,
        marker_alias=marker_alias,
        marker_alias_present=marker_alias_present,
    )


def _verify_backup_data(
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
) -> _VerifiedBackup:
    _validate_backup_id(backup_id)
    _require_canonical_operation_path(backup_root, name="backup_root")
    _require_canonical_operation_path(work_root, name="work_root")
    if _paths_overlap(backup_root, work_root):
        raise RecoveryError("path_overlap", "work_root overlaps backup storage")
    with _hold_recovery_workspace(work_root) as workspace:
        return _verify_backup_data_with_work_root(
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            work_output=workspace.root,
        )


def verify_backup(
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
) -> VerificationResult:
    """Verify marker, digests, manifest, raw files, and disposable DB audit."""

    verified = _verify_backup_data(
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    return verified.result


def _require_active_bound_source(state: _BackupBoundSourceState) -> None:
    if not state.active:
        raise RecoveryError("context_closed", "Backup-bound source context is closed")


def _require_live_file_identity(
    descriptor: int,
    path: Path,
    expected: dict[str, object],
    *,
    label: str,
    compare_content: bool,
) -> None:
    try:
        before = os.fstat(descriptor)
        current = path.lstat()
    except OSError as exc:
        raise RecoveryError("source_changed", f"{label} is no longer accessible") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or before.st_dev != current.st_dev
        or before.st_ino != current.st_ino
        or before.st_dev != expected.get("device")
        or before.st_ino != expected.get("inode")
        or stat.S_IMODE(before.st_mode) != expected.get("mode")
        or stat.S_IMODE(current.st_mode) != expected.get("mode")
    ):
        raise RecoveryError("source_changed", f"{label} identity differs from the backup")
    if not compare_content:
        return
    try:
        data = _read_descriptor(descriptor)
        after = os.fstat(descriptor)
        visible = path.lstat()
    except OSError as exc:
        raise RecoveryError("source_changed", f"{label} changed while reading") from exc
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or after.st_dev != visible.st_dev
        or after.st_ino != visible.st_ino
        or after.st_size != expected.get("size")
        or stat.S_IMODE(after.st_mode) != expected.get("mode")
        or stat.S_IMODE(visible.st_mode) != expected.get("mode")
        or _sha256_bytes(data) != expected.get("sha256")
    ):
        raise RecoveryError("source_changed", f"{label} bytes differ from the backup")


def _verify_bound_source(
    state: _BackupBoundSourceState, *, allow_source_content_changes: bool
) -> None:
    _require_active_bound_source(state)
    state.work_output.verify()
    if state.scratch is not None:
        state.scratch.directory.verify()
    try:
        state.held_lock.verify_identity()
    except MaintenanceLockError as exc:
        raise RecoveryError(
            "maintenance_lock_failed", f"Backup-bound maintenance lock changed: {exc}"
        ) from exc
    _revalidate_verified_backup(state.verified)
    _require_live_file_identity(
        state.main_descriptor,
        state.source_database_path,
        state.main_identity,
        label="Live database",
        compare_content=not allow_source_content_changes,
    )
    if allow_source_content_changes:
        return
    if state.wal_identity.get("present") is True:
        if state.wal_descriptor is None:
            raise RecoveryError("source_changed", "Recorded database WAL is not pinned")
        _require_live_file_identity(
            state.wal_descriptor,
            state.wal_path,
            state.wal_identity,
            label="Live database WAL",
            compare_content=True,
        )
    else:
        _assert_expected_absence(state.wal_path, label="database WAL")


def _verify_bound_evidence_authority(
    state: _BackupBoundSourceState,
    scratch: _OwnedScratch,
) -> None:
    """Revalidate every authority retained by an active migration write phase."""

    _verify_workspace(state.workspace)
    _verify_retained_scratch(scratch)
    _verify_bound_source(state, allow_source_content_changes=True)


def _database_manifest_record(verified: _VerifiedBackup) -> dict[str, Any]:
    resources = verified.manifest.get("resources")
    if not isinstance(resources, dict):
        raise RecoveryError("invalid_manifest", "Backup resource evidence is missing")
    record = resources.get(_DATABASE_RESOURCE_ID)
    if not isinstance(record, dict):
        raise RecoveryError("invalid_manifest", "Database resource evidence is missing")
    source_identity = record.get("source_identity")
    wal_identity = record.get("wal_source_identity")
    if not isinstance(source_identity, dict) or not isinstance(wal_identity, dict):
        raise RecoveryError("invalid_manifest", "Database source identity evidence is missing")
    return cast("dict[str, Any]", record)


def _close_bound_descriptors(
    state: _BackupBoundSourceState | None, *, primary_error: BaseException | None
) -> None:
    if state is None:
        return
    descriptors = [state.main_descriptor]
    if state.wal_descriptor is not None:
        descriptors.append(state.wal_descriptor)
    _close_descriptors(descriptors, primary_error=primary_error)


def _prepare_bound_source(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    work_output: _PinnedDirectory,
) -> _PreparedBoundSource:
    if not isinstance(inventory, RecoveryInventoryV1):
        raise TypeError("inventory must be RecoveryInventoryV1")
    _validate_inventory_completeness(inventory)
    _validate_process_quiescence(inventory)
    _validate_roots(inventory, backup_root=backup_root, work_root=work_root)
    verified = _verify_backup_data_with_work_root(
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        work_output=work_output,
    )
    if not verified.result.artifact_verified or not verified.result.snapshot_preserved:
        raise RecoveryError("artifact_unverified", "Backup artifact is not a preserved snapshot")
    if not verified.result.identity_complete:
        raise RecoveryError("identity_incomplete", "Backup identity evidence is incomplete")
    try:
        embedded_inventory = RecoveryInventoryV1.model_validate_json(
            _canonical_json(verified.manifest["inventory"])
        )
    except (KeyError, ValueError) as exc:
        raise RecoveryError("invalid_manifest", "Embedded recovery inventory is invalid") from exc
    if canonical_json_bytes(embedded_inventory) != canonical_json_bytes(inventory):
        raise RecoveryError("inventory_mismatch", "Current inventory differs from the backup")
    database_resource = next(
        resource for resource in inventory.resources if resource.logical_id == _DATABASE_RESOURCE_ID
    )
    record = _database_manifest_record(verified)
    main_identity = cast("dict[str, object]", record["source_identity"])
    wal_identity = cast("dict[str, object]", record["wal_source_identity"])
    return _PreparedBoundSource(
        inventory=inventory,
        verified=verified,
        database_resource=database_resource,
        record=record,
        main_identity=main_identity,
        wal_identity=wal_identity,
        wal_path=Path(f"{database_resource.captured_path}-wal"),
        backup_id=backup_id,
        work_output=work_output,
    )


def _exit_bound_lock(
    lock_context: Any,
    *,
    primary_error: BaseException | None,
) -> BaseException | None:
    try:
        lock_context.__exit__(
            type(primary_error) if primary_error is not None else None,
            primary_error,
            primary_error.__traceback__ if primary_error is not None else None,
        )
    except BaseException as error:
        if primary_error is not None:
            primary_error.add_note(f"Backup-bound lock cleanup failed: {error}")
            return primary_error
        return error
    return primary_error


def _acquire_bound_state(
    prepared: _PreparedBoundSource,
    workspace: _RecoveryWorkspace,
) -> tuple[_BackupBoundSourceState, Any]:
    inventory = prepared.inventory
    lock_context = hold_existing_maintenance_lock(
        inventory.maintenance_lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
        expected_device=inventory.maintenance_lock_identity.device,
        expected_inode=inventory.maintenance_lock_identity.inode,
    )
    try:
        held_lock = lock_context.__enter__()
    except MaintenanceLockError as exc:
        reason = "runtime_active" if exc.reason == "busy" else "maintenance_lock_failed"
        raise RecoveryError(reason, f"Cannot bind backup source lock: {exc}") from exc
    main_descriptor: int | None = None
    wal_descriptor: int | None = None
    try:
        main_descriptor = _open_source_file(
            prepared.database_resource.captured_path, label="live database"
        )
        if prepared.wal_identity.get("present") is True:
            wal_descriptor = _open_source_file(prepared.wal_path, label="live database WAL")
        else:
            _assert_expected_absence(prepared.wal_path, label="database WAL")
        state = _BackupBoundSourceState(
            verified=prepared.verified,
            held_lock=held_lock,
            source_database_path=prepared.database_resource.captured_path,
            main_descriptor=main_descriptor,
            main_identity=prepared.main_identity,
            wal_path=prepared.wal_path,
            wal_descriptor=wal_descriptor,
            wal_identity=prepared.wal_identity,
            workspace=workspace,
            work_output=prepared.work_output,
        )
        _verify_bound_source(state, allow_source_content_changes=False)
        return state, lock_context
    except BaseException as error:
        descriptors = [value for value in (main_descriptor, wal_descriptor) if value is not None]
        _close_descriptors(descriptors, primary_error=error)
        _exit_bound_lock(lock_context, primary_error=error)
        raise


def _build_bound_view(
    prepared: _PreparedBoundSource, state: _BackupBoundSourceState
) -> tuple[BackupBoundSource, _OwnedScratch]:
    scratch = _create_owned_scratch(prepared.work_output, prefix="mp06-bound-source-")
    state.scratch = scratch
    try:
        clone_database = scratch.access_path / "economy.db"
        database_bytes = _read_verified_payload_file(
            prepared.verified.payload,
            prepared.record.get("payload_path"),
            files=prepared.verified.files,
            label="backup-bound database clone",
        )
        _write_new_file_under(scratch.directory, Path("economy.db"), database_bytes, mode=0o600)
        copied_database, copied_database_mode = _read_regular_under(
            scratch.directory, "economy.db", label="backup-bound database clone"
        )
        if copied_database != database_bytes or copied_database_mode != 0o600:
            raise RecoveryError("clone_mismatch", "Backup-bound database clone differs")
        clone_wal: Path | None = None
        if prepared.wal_identity.get("present") is True:
            wal_bytes = _read_verified_payload_file(
                prepared.verified.payload,
                prepared.record.get("wal_payload_path"),
                files=prepared.verified.files,
                label="backup-bound WAL clone",
            )
            clone_wal = Path(f"{clone_database}-wal")
            _write_new_file_under(scratch.directory, Path("economy.db-wal"), wal_bytes, mode=0o600)
            copied_wal, copied_wal_mode = _read_regular_under(
                scratch.directory, "economy.db-wal", label="backup-bound WAL clone"
            )
            if copied_wal != wal_bytes or copied_wal_mode != 0o600:
                raise RecoveryError("clone_mismatch", "Backup-bound WAL clone differs")
        _verify_bound_source(state, allow_source_content_changes=False)
        return (
            BackupBoundSource(
                backup_id=prepared.backup_id,
                manifest_sha256=prepared.verified.result.manifest_sha256,
                artifact_verification_result=prepared.verified.result,
                source_database_path=prepared.database_resource.captured_path,
                _scratch=scratch,
                _wal_present=clone_wal is not None,
                _state=state,
            ),
            scratch,
        )
    except BaseException as error:
        _cleanup_owned_scratch(scratch, primary_error=error)
        raise


def _verify_bound_source_exit(
    state: _BackupBoundSourceState, *, primary_error: BaseException | None
) -> BaseException | None:
    try:
        _verify_bound_source(state, allow_source_content_changes=state.write_phase)
    except BaseException as error:
        if primary_error is not None:
            primary_error.add_note(f"Backup-bound source exit verification failed: {error}")
            return primary_error
        return error
    return primary_error


def _cleanup_bound_source(
    state: _BackupBoundSourceState,
    scratch: _OwnedScratch | None,
    lock_context: Any,
    *,
    primary_error: BaseException | None,
) -> BaseException | None:
    state.active = False
    cleanup_error = primary_error
    if scratch is not None:
        cleanup_error = _cleanup_owned_scratch(scratch, primary_error=cleanup_error)
    try:
        _close_bound_descriptors(state, primary_error=cleanup_error)
    except BaseException as error:
        cleanup_error = error
    return _exit_bound_lock(lock_context, primary_error=cleanup_error)


@contextmanager
def open_backup_bound_source(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
) -> Iterator[BackupBoundSource]:
    """Bind a verified backup to the exact quiescent live database source.

    This proves backup/source identity only.  It does not admit schema or
    financial migration and intentionally leaves ``migration_eligible`` false.
    """

    if not isinstance(inventory, RecoveryInventoryV1):
        raise TypeError("inventory must be RecoveryInventoryV1")
    _validate_backup_id(backup_id)
    _validate_inventory_completeness(inventory)
    _validate_process_quiescence(inventory)
    _validate_root_authorization(
        inventory,
        backup_root=backup_root,
        work_root=work_root,
    )
    workspace = _open_workspace(work_root)
    work_output = workspace.root
    try:
        prepared = _prepare_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            work_output=work_output,
        )
    except BaseException as error:
        _close_workspace(workspace, primary_error=error)
        raise
    try:
        state, lock_context = _acquire_bound_state(prepared, workspace)
    except BaseException as error:
        _close_workspace(workspace, primary_error=error)
        raise
    try:
        view, scratch = _build_bound_view(prepared, state)
    except BaseException as error:
        cleanup_error = _cleanup_bound_source(
            state,
            None,
            lock_context,
            primary_error=error,
        )
        _close_workspace(workspace, primary_error=cleanup_error)
        if cleanup_error is not error:
            error.add_note(f"Backup-bound setup cleanup failed: {cleanup_error}")
        raise
    primary_error: BaseException | None = None
    try:
        yield view
    except BaseException as error:
        primary_error = error
        raise
    finally:
        body_failed = primary_error is not None
        primary_error = _verify_bound_source_exit(state, primary_error=primary_error)
        cleanup_error = _cleanup_bound_source(
            state,
            scratch,
            lock_context,
            primary_error=primary_error,
        )
        cleanup_error = _close_workspace(workspace, primary_error=cleanup_error)
        if not body_failed and cleanup_error is not None:
            raise cleanup_error


def _finalize_backup_with_workspace(
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    work_output: _PinnedDirectory,
) -> VerificationResult:
    """After exact verification, retry only durability syncs for an uncertain backup."""

    result = _verify_backup_data_with_work_root(
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        work_output=work_output,
    ).result
    backup_output = _open_pinned_directory(backup_root, label="backup root")
    artifact = _open_pinned_child(backup_output, backup_id)
    payload = _open_pinned_child(artifact, "payload")
    primary_error: BaseException | None = None
    try:
        marker = _read_json_regular(artifact.path / "COMPLETE", missing_reason="incomplete_bundle")
        _finalize_marker_alias_pinned(artifact, marker)
        _fsync_tree(payload.path)
        marker_descriptor = os.open(
            "COMPLETE",
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=artifact.descriptor,
        )
        try:
            os.fsync(marker_descriptor)
        finally:
            os.close(marker_descriptor)
        os.fsync(payload.descriptor)
        os.fsync(artifact.descriptor)
        os.fsync(backup_output.descriptor)
        payload.verify()
        artifact.verify()
        backup_output.verify()
    except BaseException as error:
        primary_error = error
        raise
    finally:
        payload.close(primary_error=primary_error)
        artifact.close(primary_error=primary_error)
        backup_output.close(primary_error=primary_error)
    return result


def finalize_backup(
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
) -> VerificationResult:
    """Verify and finalize one backup while holding the recovery workspace lease."""

    _validate_backup_id(backup_id)
    _require_canonical_operation_path(backup_root, name="backup_root")
    _require_canonical_operation_path(work_root, name="work_root")
    if _paths_overlap(backup_root, work_root):
        raise RecoveryError("path_overlap", "work_root overlaps backup storage")
    with _hold_recovery_workspace(work_root) as workspace:
        return _finalize_backup_with_workspace(
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            work_output=workspace.root,
        )


def _list_item(value: object, index: int, *, final: bool) -> object:
    if not isinstance(value, list) or index >= len(value):
        location = "field" if final else "path"
        raise RecoveryError("config_shape_mismatch", f"Captured config list {location} differs")
    return value[index]


def _set_list_item(value: object, index: int, replacement: object) -> None:
    _list_item(value, index, final=True)
    if not isinstance(value, list):
        raise RecoveryError("config_shape_mismatch", "Captured config list field differs")
    value[index] = replacement


def _set_field(mapping: dict[str, Any], field_path: tuple[str | int, ...], value: object) -> None:
    current: object = mapping
    for segment in field_path[:-1]:
        if isinstance(segment, int):
            current = _list_item(current, segment, final=False)
        else:
            if not isinstance(current, dict) or segment not in current:
                raise RecoveryError("config_shape_mismatch", "Captured config mapping path differs")
            current = current[segment]
    final = field_path[-1]
    if isinstance(final, int):
        _set_list_item(current, final, value)
    else:
        if not isinstance(current, dict) or final not in current:
            raise RecoveryError("config_shape_mismatch", "Captured config mapping field differs")
        current[final] = value


def _render_binding_value(
    binding: SourceFieldBindingV1,
    *,
    target_bindings: TargetBindingsV1,
    target_config: Path,
) -> object:
    if binding.binding_kind is not BindingKind.FILESYSTEM:
        try:
            return target_bindings.endpoint_values[binding.binding_id]
        except KeyError as exc:
            raise RecoveryError(
                "incomplete_bindings",
                f"Missing endpoint target for {binding.binding_id}",
            ) from exc
    resource_id = cast("str", binding.resource_id)
    target_path = target_bindings.resource_paths[resource_id]
    base = cast("PathResolutionBase", binding.resolution_base)
    if base is PathResolutionBase.ABSOLUTE:
        return str(target_path)
    if base is PathResolutionBase.CONFIG_PARENT:
        anchor = target_config.parent
    elif base is PathResolutionBase.AGENTS_PACKAGE_ROOT:
        anchor = target_bindings.agents_package_root
    else:
        try:
            anchor = target_bindings.process_cwds[binding.config_id]
        except KeyError as exc:
            raise RecoveryError(
                "incomplete_bindings",
                f"Missing process CWD for {binding.config_id}",
            ) from exc
    return os.path.relpath(target_path, anchor)


def _validate_target_endpoint_types(
    inventory: RecoveryInventoryV1,
    target_bindings: TargetBindingsV1,
) -> None:
    for binding in inventory.field_bindings:
        if binding.binding_kind is BindingKind.FILESYSTEM:
            continue
        value = target_bindings.endpoint_values[binding.binding_id]
        if binding.binding_kind is BindingKind.PORT:
            valid = type(value) is int
        elif binding.binding_kind is BindingKind.ORIGINS:
            valid = isinstance(value, tuple) and all(type(item) is str for item in value)
        else:
            valid = type(value) is str
        if not valid:
            raise RecoveryError(
                "binding_mismatch", f"Target endpoint type differs: {binding.binding_id}"
            )


def _validate_target_bindings(
    inventory: RecoveryInventoryV1,
    target_bindings: TargetBindingsV1,
    *,
    target_root: Path,
) -> None:
    if target_bindings.target_root != target_root:
        raise RecoveryError("binding_mismatch", "target_root differs from target bindings")
    resource_ids = {resource.logical_id for resource in inventory.resources}
    if set(target_bindings.resource_paths) != resource_ids:
        raise RecoveryError("incomplete_bindings", "Target resource inventory must match exactly")
    endpoint_ids = {
        binding.binding_id
        for binding in inventory.field_bindings
        if binding.binding_kind is not BindingKind.FILESYSTEM
    }
    if set(target_bindings.endpoint_values) != endpoint_ids:
        raise RecoveryError("incomplete_bindings", "Target endpoint inventory must match exactly")
    _validate_target_endpoint_types(inventory, target_bindings)
    config_ids = {config.logical_id for config in inventory.configs}
    if set(target_bindings.process_cwds) != config_ids:
        raise RecoveryError(
            "incomplete_bindings", "Target process CWD inventory must match exactly"
        )
    resources = {resource.logical_id: resource for resource in inventory.resources}
    paths = tuple(target_bindings.resource_paths.items())
    for index, (resource_id, path) in enumerate(paths):
        for other_id, other in paths[index + 1 :]:
            if not _paths_overlap(path, other):
                continue
            if path == other:
                raise RecoveryError("binding_overlap", f"Target resource paths are equal: {path}")
            ancestor_id = resource_id if other.is_relative_to(path) else other_id
            ancestor = resources[ancestor_id]
            if ancestor.capture_kind is CaptureKind.DIRECTORY_ONLY:
                continue
            raise RecoveryError(
                "binding_overlap", f"Target resource paths overlap: {path}, {other}"
            )


def _copy_verified_bundle_file(
    verified: _VerifiedBackup,
    target_output: _PinnedDirectory,
    relative_value: object,
    destination: Path,
    *,
    mode: int,
    label: str,
) -> bytes:
    data = _read_verified_payload_file(
        verified.payload,
        relative_value,
        files=verified.files,
        label=label,
    )
    _write_new_file_under(
        target_output,
        destination.relative_to(target_output.path),
        data,
        mode=mode,
    )
    return data


def _authority_lstat(path: Path, *, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise RecoveryError("source_changed", f"{label} is no longer accessible") from exc


def _revalidate_verified_backup(verified: _VerifiedBackup) -> None:
    root = _authority_lstat(verified.root, label="verified backup directory")
    payload = _authority_lstat(verified.payload, label="verified payload directory")
    if (root.st_dev, root.st_ino) != verified.root_identity:
        raise RecoveryError("source_changed", "Verified backup directory changed")
    if (payload.st_dev, payload.st_ino) != verified.payload_identity:
        raise RecoveryError("source_changed", "Verified payload directory changed")
    if stat.S_IMODE(root.st_mode) != 0o700 or stat.S_IMODE(payload.st_mode) != 0o700:
        raise RecoveryError("unsafe_mode", "Verified backup directories must have mode 0700")
    marker_bytes, marker_mode = _read_pinned_regular(
        verified.root / "COMPLETE", label="completion marker revalidation"
    )
    marker_metadata = _authority_lstat(
        verified.root / "COMPLETE", label="verified completion marker"
    )
    if (
        marker_bytes != verified.marker_bytes
        or marker_mode != verified.marker_mode
        or (marker_metadata.st_dev, marker_metadata.st_ino) != verified.marker_identity
    ):
        raise RecoveryError("marker_mismatch", "Verified completion marker changed")
    alias_path = verified.root / verified.marker_alias
    try:
        alias_path.lstat()
    except FileNotFoundError:
        alias_present = False
    except OSError as exc:
        raise RecoveryError(
            "source_changed", "Completion marker alias cannot be inspected"
        ) from exc
    else:
        alias_present = True
    if alias_present != verified.marker_alias_present:
        raise RecoveryError("marker_mismatch", "Completion marker alias presence changed")
    if alias_present:
        alias_bytes, alias_mode = _read_pinned_regular(
            alias_path, label="completion marker alias revalidation"
        )
        alias_metadata = _authority_lstat(alias_path, label="completion marker alias")
        if (
            alias_bytes != verified.marker_bytes
            or alias_mode != verified.marker_mode
            or (alias_metadata.st_dev, alias_metadata.st_ino) != verified.marker_identity
        ):
            raise RecoveryError("marker_mismatch", "Completion marker alias changed")
    if _tree_digest(verified.payload, exclude=frozenset()) != verified.payload_sha256:
        raise RecoveryError("digest_mismatch", "Verified payload changed during restore")


def _restore_evidence_from_manifest(manifest: dict[str, Any]) -> _RestoreEvidence:
    files_value = manifest.get("expected_files")
    directories_value = manifest.get("expected_directories")
    absent_value = manifest.get("absent_paths")
    if (
        not isinstance(files_value, list)
        or not isinstance(directories_value, list)
        or not isinstance(absent_value, list)
    ):
        raise RecoveryError("invalid_manifest", "Restore evidence index is missing")
    files: dict[str, dict[str, object]] = {}
    for value in files_value:
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            raise RecoveryError("invalid_manifest", "Expected file evidence is invalid")
        path = cast("str", value["path"])
        _safe_payload_relative(path, field_name="expected file path")
        if path in files:
            raise RecoveryError("invalid_manifest", "Expected file paths are duplicated")
        files[path] = cast("dict[str, object]", value)
    directories: dict[str, int] = {}
    for value in directories_value:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("path"), str)
            or type(value.get("mode")) is not int
        ):
            raise RecoveryError("invalid_manifest", "Expected directory evidence is invalid")
        path = cast("str", value["path"])
        _safe_payload_relative(path, field_name="expected directory path")
        if path in directories:
            raise RecoveryError("invalid_manifest", "Expected directories are duplicated")
        directories[path] = cast("int", value["mode"])
    absent_paths: set[str] = set()
    for value in absent_value:
        if not isinstance(value, str):
            raise RecoveryError("invalid_manifest", "Absent path evidence is invalid")
        _safe_payload_relative(value, field_name="absent target path")
        absent_paths.add(value)
    return _RestoreEvidence(
        files=files,
        directories=directories,
        absent_paths=absent_paths,
    )


def _target_tree_inventory(
    target_root: Path, *, special_files: frozenset[str]
) -> tuple[set[str], set[str]]:
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for path in sorted(target_root.rglob("*")):
        relative = path.relative_to(target_root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RecoveryError("target_mismatch", f"Restored target has symlink: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            actual_directories.add(relative)
        elif stat.S_ISREG(metadata.st_mode):
            if relative not in special_files:
                actual_files.add(relative)
        else:
            raise RecoveryError("target_mismatch", f"Restored target has special file: {relative}")
    return actual_files, actual_directories


def _validate_target_file_evidence(target_root: Path, evidence: _RestoreEvidence) -> None:
    for relative, expected in evidence.files.items():
        data, mode = _read_pinned_regular(target_root / relative, label=f"restored file {relative}")
        if (
            len(data) != expected.get("size")
            or _sha256_bytes(data) != expected.get("sha256")
            or mode != expected.get("mode")
        ):
            raise RecoveryError("target_mismatch", f"Restored file differs: {relative}")


def _validate_target_tree(
    target_root: Path,
    evidence: _RestoreEvidence,
    *,
    special_files: frozenset[str],
) -> None:
    actual_files, actual_directories = _target_tree_inventory(
        target_root, special_files=special_files
    )
    if actual_files != set(evidence.files):
        raise RecoveryError("target_mismatch", "Restored file inventory differs")
    if actual_directories != set(evidence.directories):
        raise RecoveryError("target_mismatch", "Restored directory inventory differs")
    _validate_target_file_evidence(target_root, evidence)
    for relative, mode in evidence.directories.items():
        metadata = (target_root / relative).lstat()
        if stat.S_IMODE(metadata.st_mode) != mode:
            raise RecoveryError("target_mismatch", f"Restored directory mode differs: {relative}")
    for relative in evidence.absent_paths:
        try:
            (target_root / relative).lstat()
        except FileNotFoundError:
            continue
        raise RecoveryError("target_mismatch", f"Absent target path exists: {relative}")


def _verify_target_keypairs(
    inventory: RecoveryInventoryV1,
    target_bindings: TargetBindingsV1,
    evidence: _RestoreEvidence,
    *,
    source_keypairs: dict[str, dict[str, object]],
    target_root: Path,
    work_root: _PinnedDirectory,
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]]]:
    findings: list[dict[str, object]] = []
    target_metadata: dict[str, dict[str, object]] = {}
    for keypair in inventory.keypairs:
        private_path = target_bindings.resource_paths[keypair.private_resource_id]
        public_path = target_bindings.resource_paths[keypair.public_resource_id]
        private_relative = private_path.relative_to(target_root).as_posix()
        public_relative = public_path.relative_to(target_root).as_posix()
        private_evidence = evidence.files.get(private_relative)
        public_evidence = evidence.files.get(public_relative)
        if private_evidence is None or public_evidence is None:
            findings.append({"code": "missing_key_material", "handle": keypair.handle})
            continue
        private_bytes, private_mode = _read_pinned_regular(
            private_path, label=f"restored private key {keypair.handle}"
        )
        public_bytes, public_mode = _read_pinned_regular(
            public_path, label=f"restored public key {keypair.handle}"
        )
        source_metadata = source_keypairs.get(keypair.handle)
        expected_public_mode = (
            source_metadata.get("public_restore_mode")
            if isinstance(source_metadata, dict)
            else None
        )
        if private_mode != 0o600 or public_mode != expected_public_mode:
            findings.append({"code": "unsafe_key_mode", "handle": keypair.handle})
        with _owned_scratch_directory(work_root, prefix="mp06-target-key-") as scratch:
            private_relative_path = Path(f"{keypair.handle}.key")
            public_relative_path = Path(f"{keypair.handle}.pub")
            private_copy = scratch.access_path / private_relative_path
            public_copy = scratch.access_path / public_relative_path
            _write_new_file_under(
                scratch.directory, private_relative_path, private_bytes, mode=0o600
            )
            _write_new_file_under(scratch.directory, public_relative_path, public_bytes, mode=0o600)
            try:
                private_key = load_private_key(private_copy)
                public_key = load_public_key(public_copy)
            except KeyMaterialError:
                findings.append({"code": "invalid_key_material", "handle": keypair.handle})
                continue
        stored = public_key_to_b64(public_key)
        if public_key_to_b64(private_key.public_key()) != stored:
            findings.append({"code": "mismatched_keypair", "handle": keypair.handle})
        fingerprint = _sha256_bytes(base64.b64decode(stored, validate=True))
        if fingerprint != keypair.expected_public_fingerprint_sha256:
            findings.append({"code": "captured_fingerprint_mismatch", "handle": keypair.handle})
        target_metadata[keypair.handle] = {
            "fingerprint": fingerprint,
            "private_path": private_relative,
            "public_path": public_relative,
            "private_mode": private_mode,
            "public_mode": public_mode,
        }
    findings.sort(key=_canonical_json)
    return target_metadata, findings


def _verify_target_maintenance_lock(
    inventory: RecoveryInventoryV1,
    target_bindings: TargetBindingsV1,
    evidence: _RestoreEvidence,
    *,
    target_root: Path,
    expected_identity: dict[str, object] | None,
) -> dict[str, object]:
    resource = next(
        item for item in inventory.resources if item.capture_kind is CaptureKind.RUNTIME_LOCK
    )
    path = target_bindings.resource_paths[resource.logical_id]
    relative = path.relative_to(target_root).as_posix()
    file_evidence = evidence.files.get(relative)
    if file_evidence is None:
        raise RecoveryError("target_mismatch", "Target maintenance lock is uninventoried")
    data, mode = _read_pinned_regular(path, label="target maintenance lock")
    metadata = path.lstat()
    identity: dict[str, object] = {
        "resource_id": resource.logical_id,
        "path": relative,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": mode,
        "size": len(data),
    }
    if data != b"" or mode != 0o600:
        raise RecoveryError("target_mismatch", "Target maintenance lock is not fresh")
    if expected_identity is not None and identity != expected_identity:
        raise RecoveryError("target_mismatch", "Target maintenance lock identity differs")
    try:
        with hold_existing_maintenance_lock(
            path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=metadata.st_dev,
            expected_inode=metadata.st_ino,
        ) as held:
            held.verify_identity()
    except MaintenanceLockError as exc:
        reason = "runtime_active" if exc.reason == "busy" else "target_mismatch"
        raise RecoveryError(reason, f"Target maintenance lock cannot be verified: {exc}") from exc
    return identity


def _verify_target_configs(
    inventory: RecoveryInventoryV1,
    target_bindings: TargetBindingsV1,
    *,
    target_root: Path,
) -> None:
    for config in inventory.configs:
        source_path = target_root / "source-evidence/configs" / f"{config.logical_id}.yaml"
        source_bytes, _source_mode = _read_pinned_regular(
            source_path, label=f"restored source config {config.logical_id}"
        )
        if _source_mode != 0o600:
            raise RecoveryError(
                "target_mismatch", f"Restored source config mode differs: {config.logical_id}"
            )
        if _sha256_bytes(source_bytes) != config.sha256:
            raise RecoveryError(
                "target_mismatch", f"Restored source config digest differs: {config.logical_id}"
            )
        try:
            rendered = parse_yaml_config_bytes(source_bytes, source=config.logical_id)
        except ConfigurationError as exc:
            raise RecoveryError(
                "target_mismatch", f"Restored source config is invalid: {config.logical_id}"
            ) from exc
        config_resource = next(
            resource
            for resource in inventory.resources
            if resource.capture_kind is CaptureKind.CONFIG
            and resource.captured_path == config.captured_path
        )
        target_config = target_bindings.resource_paths[config_resource.logical_id]
        for binding in inventory.field_bindings:
            if binding.config_id != config.logical_id:
                continue
            actual = _get_config_field(rendered, tuple(binding.field_path))
            _require_captured_binding_value(binding, actual)
            target_value = _render_binding_value(
                binding,
                target_bindings=target_bindings,
                target_config=target_config,
            )
            _set_field(rendered, tuple(binding.field_path), target_value)
        expected = _canonical_json(rendered)
        actual, mode = _read_pinned_regular(
            target_config, label=f"active config {config.logical_id}"
        )
        if actual != expected or mode != 0o600:
            raise RecoveryError("target_mismatch", f"Active config differs: {config.logical_id}")


def _target_agent_service_urls(
    inventory: RecoveryInventoryV1,
    target_bindings: TargetBindingsV1,
) -> dict[str, str]:
    urls: dict[str, str] = {}
    for binding in inventory.field_bindings:
        path = tuple(binding.field_path)
        if (
            binding.config_id != "agents"
            or binding.binding_kind is not BindingKind.NETWORK
            or len(path) != 2
            or path[0] != "platform"
            or not isinstance(path[1], str)
        ):
            continue
        target_value = target_bindings.endpoint_values[binding.binding_id]
        if not isinstance(target_value, str):
            raise RecoveryError(
                "binding_mismatch", f"Agent endpoint is not a string: {binding.binding_id}"
            )
        urls[path[1]] = target_value
    if frozenset(urls) != _AGENT_SERVICE_URL_FIELDS or any(
        not value.strip() for value in urls.values()
    ):
        raise RecoveryError("incomplete_bindings", "Agent service URL targets are incomplete")
    return urls


def _agent_owner_record_code(
    owner: object,
    *,
    identity: str,
    expected_urls: dict[str, str],
) -> str | None:
    if not isinstance(owner, dict):
        return "agent_runtime_owner_invalid"
    record = cast("dict[str, object]", owner)
    key_fingerprint = record.get("key_fingerprint")
    service_urls_value = record.get("service_urls")
    invalid_record = (
        frozenset(record) != _AGENT_OWNER_FIELDS
        or type(record.get("schema_version")) is not int
        or record.get("schema_version") != 1
        or any(
            not isinstance(record.get(field), str) or not cast("str", record[field]).strip()
            for field in _AGENT_OWNER_STRING_FIELDS
        )
        or not isinstance(key_fingerprint, str)
        or _AGENT_IDENTITY_NAME.fullmatch(key_fingerprint) is None
        or key_fingerprint != identity
        or not isinstance(service_urls_value, dict)
    )
    if invalid_record:
        return "agent_runtime_owner_invalid"
    service_urls = cast("dict[object, object]", service_urls_value)
    if frozenset(service_urls) != _AGENT_SERVICE_URL_FIELDS or any(
        not isinstance(value, str) or not value.strip() for value in service_urls.values()
    ):
        return "agent_runtime_owner_invalid"
    if service_urls != expected_urls:
        return "agent_runtime_owner_binding_mismatch"
    return None


def _restored_agent_identity_names(
    evidence: _RestoreEvidence,
    *,
    prefix: Path,
) -> tuple[str, ...]:
    identities_prefix = prefix / "identities"
    names: list[str] = []
    for relative_text in evidence.directories:
        relative = Path(relative_text)
        try:
            identity_relative = relative.relative_to(identities_prefix)
        except ValueError:
            continue
        if len(identity_relative.parts) != 1:
            continue
        identity = identity_relative.parts[0]
        if _AGENT_IDENTITY_NAME.fullmatch(identity) is None:
            raise RecoveryError(
                "target_mismatch", f"Restored agent identity name is invalid: {identity}"
            )
        names.append(identity)
    return tuple(sorted(names))


def _agent_runtime_owner_findings(
    inventory: RecoveryInventoryV1,
    target_bindings: TargetBindingsV1,
    evidence: _RestoreEvidence,
    *,
    target_root: Path,
) -> list[dict[str, object]]:
    resource = _agent_runtime_resource(inventory)
    if resource is None or resource.capture_kind is CaptureKind.ABSENT_DIRECTORY:
        return []
    target = target_bindings.resource_paths[resource.logical_id]
    prefix = target.relative_to(target_root)
    expected_urls = _target_agent_service_urls(inventory, target_bindings)
    findings: list[dict[str, object]] = []
    for identity in _restored_agent_identity_names(evidence, prefix=prefix):
        relative = prefix / "identities" / identity / "owner.json"
        file_evidence = evidence.files.get(relative.as_posix())
        if file_evidence is None or (
            file_evidence.get("role") != resource.logical_id or file_evidence.get("mode") != 0o600
        ):
            findings.append({"code": "agent_runtime_owner_invalid", "identity": identity})
            continue
        try:
            owner_bytes, owner_mode = _read_pinned_regular(
                target_root / relative,
                label=f"agent runtime owner {identity}",
            )
            owner = json.loads(owner_bytes)
        except (RecoveryError, json.JSONDecodeError, UnicodeDecodeError):
            findings.append({"code": "agent_runtime_owner_invalid", "identity": identity})
            continue
        if owner_mode != 0o600:
            findings.append({"code": "agent_runtime_owner_invalid", "identity": identity})
            continue
        code = _agent_owner_record_code(owner, identity=identity, expected_urls=expected_urls)
        if code is not None:
            findings.append({"code": code, "identity": identity})
    return findings


def _render_configs(
    *,
    inventory: RecoveryInventoryV1,
    manifest: dict[str, Any],
    payload: Path,
    files: dict[str, dict[str, Any]],
    target_bindings: TargetBindingsV1,
    target_root: Path,
    restore_evidence: _RestoreEvidence,
    target_output: _PinnedDirectory,
) -> list[dict[str, object]]:
    resources = cast("dict[str, dict[str, Any]]", manifest["resources"])
    changes: list[dict[str, object]] = []
    for config in inventory.configs:
        matching = [
            (resource_id, record)
            for resource_id, record in resources.items()
            if record.get("config_id") == config.logical_id
        ]
        if len(matching) != 1:
            raise RecoveryError(
                "invalid_manifest", f"Config capture is ambiguous: {config.logical_id}"
            )
        resource_id, record = matching[0]
        evidence = target_root / "source-evidence/configs" / f"{config.logical_id}.yaml"
        source_bytes = _read_verified_payload_file(
            payload,
            record["payload_path"],
            files=files,
            label=f"config {config.logical_id}",
        )
        _write_new_file_under(
            target_output,
            evidence.relative_to(target_root),
            source_bytes,
            mode=0o600,
        )
        restore_evidence.record_file(
            target_root,
            evidence,
            source_bytes,
            mode=0o600,
            role=f"source_config:{config.logical_id}",
        )
        try:
            rendered = parse_yaml_config_bytes(source_bytes, source=config.logical_id)
        except ConfigurationError as exc:
            raise RecoveryError(
                "invalid_config", f"Captured config cannot be parsed: {config.logical_id}"
            ) from exc
        target_config = target_bindings.resource_paths[resource_id]
        for binding in inventory.field_bindings:
            if binding.config_id != config.logical_id:
                continue
            actual = _get_config_field(rendered, tuple(binding.field_path))
            _require_captured_binding_value(binding, actual)
            new_value = _render_binding_value(
                binding,
                target_bindings=target_bindings,
                target_config=target_config,
            )
            _set_field(rendered, tuple(binding.field_path), new_value)
            changes.append(
                {
                    "binding_id": binding.binding_id,
                    "config_id": config.logical_id,
                    "field_path": list(binding.field_path),
                    "captured_value": binding.captured_value,
                    "target_value": new_value,
                }
            )
        rendered_bytes = _canonical_json(rendered)
        _write_new_file_under(
            target_output,
            target_config.relative_to(target_root),
            rendered_bytes,
            mode=0o600,
        )
        restore_evidence.record_file(
            target_root,
            target_config,
            rendered_bytes,
            mode=0o600,
            role=f"active_config:{config.logical_id}",
        )
    return changes


def _restore_resources(
    inventory: RecoveryInventoryV1,
    *,
    verified: _VerifiedBackup,
    resources: dict[str, dict[str, Any]],
    target_bindings: TargetBindingsV1,
    target_root: Path,
    restore_evidence: _RestoreEvidence,
    target_output: _PinnedDirectory,
) -> None:
    directory_only = sorted(
        (
            target_bindings.resource_paths[resource.logical_id]
            for resource in inventory.resources
            if resource.capture_kind is CaptureKind.DIRECTORY_ONLY
        ),
        key=lambda path: len(path.parts),
    )
    for directory in directory_only:
        _ensure_directory_under(target_output, directory.relative_to(target_root))
        restore_evidence.record_directory(target_root, directory, mode=0o700)
    for resource in inventory.resources:
        target = target_bindings.resource_paths[resource.logical_id]
        if resource.capture_kind in {
            CaptureKind.ABSENT_FILE,
            CaptureKind.ABSENT_DIRECTORY,
        }:
            restore_evidence.record_absent(target_root, target)
            continue
        if resource.capture_kind in {CaptureKind.CONFIG, CaptureKind.DIRECTORY_ONLY}:
            continue
        record = resources[resource.logical_id]
        if resource.capture_kind is CaptureKind.RUNTIME_LOCK:
            _write_new_file_under(
                target_output,
                target.relative_to(target_root),
                b"",
                mode=0o600,
            )
            restore_evidence.record_file(
                target_root,
                target,
                b"",
                mode=0o600,
                role=resource.logical_id,
            )
            continue
        if resource.kind is ResourceKind.DIRECTORY:
            _ensure_directory_under(target_output, target.relative_to(target_root))
            restore_evidence.record_directory(target_root, target, mode=0o700)
            if resource.capture_kind is CaptureKind.COPY:
                prefix = _safe_payload_relative(
                    record["payload_path"], field_name="directory payload path"
                )
                source_root = verified.payload / prefix
                for source_directory in sorted(
                    (path for path in source_root.rglob("*") if path.is_dir()),
                    key=lambda path: len(path.parts),
                ):
                    metadata = source_directory.lstat()
                    if stat.S_ISLNK(metadata.st_mode):
                        raise RecoveryError("unsafe_bundle", "Source directory is a symlink")
                    target_directory = target / source_directory.relative_to(source_root)
                    _ensure_directory_under(
                        target_output, target_directory.relative_to(target_root)
                    )
                    restore_evidence.record_directory(target_root, target_directory, mode=0o700)
                for relative_text, evidence in sorted(verified.files.items()):
                    relative = Path(relative_text)
                    if evidence.get("role") != resource.logical_id:
                        continue
                    destination = target / relative.relative_to(prefix)
                    data = _copy_verified_bundle_file(
                        verified,
                        target_output,
                        relative_text,
                        destination,
                        mode=int(evidence["mode"]),
                        label=f"resource {resource.logical_id}",
                    )
                    restore_evidence.record_file(
                        target_root,
                        destination,
                        data,
                        mode=int(evidence["mode"]),
                        role=resource.logical_id,
                    )
            continue
        relative_text = str(record["payload_path"])
        evidence = verified.files[relative_text]
        mode = int(record.get("restore_mode", evidence["mode"]))
        data = _copy_verified_bundle_file(
            verified,
            target_output,
            relative_text,
            target,
            mode=mode,
            label=f"resource {resource.logical_id}",
        )
        restore_evidence.record_file(
            target_root,
            target,
            data,
            mode=mode,
            role=resource.logical_id,
        )
        if resource.logical_id == _DATABASE_RESOURCE_ID and record.get("wal_payload_path"):
            wal_target = Path(f"{target}-wal")
            wal_relative = str(record["wal_payload_path"])
            wal_evidence = verified.files[wal_relative]
            wal_data = _copy_verified_bundle_file(
                verified,
                target_output,
                wal_relative,
                wal_target,
                mode=int(wal_evidence["mode"]),
                label="database WAL",
            )
            restore_evidence.record_file(
                target_root,
                wal_target,
                wal_data,
                mode=int(wal_evidence["mode"]),
                role="database.wal",
            )


def _populate_restored_target(
    verified: _VerifiedBackup,
    inventory: RecoveryInventoryV1,
    target_bindings: TargetBindingsV1,
    *,
    backup_id: str,
    target_root: Path,
    work_root: _PinnedDirectory,
    target_output: _PinnedDirectory,
    target_parent: _PinnedDirectory,
) -> PublicationResult:
    manifest = verified.manifest
    resources = cast("dict[str, dict[str, Any]]", manifest["resources"])
    restore_evidence = _RestoreEvidence(files={}, directories={}, absent_paths=set())
    _restore_resources(
        inventory,
        verified=verified,
        resources=resources,
        target_bindings=target_bindings,
        target_root=target_root,
        restore_evidence=restore_evidence,
        target_output=target_output,
    )
    changes = _render_configs(
        inventory=inventory,
        manifest=manifest,
        payload=verified.payload,
        files=verified.files,
        target_bindings=target_bindings,
        target_root=target_root,
        restore_evidence=restore_evidence,
        target_output=target_output,
    )
    _validate_target_tree(target_root, restore_evidence, special_files=frozenset())
    source_keypairs = cast("dict[str, dict[str, object]]", manifest.get("keypairs", {}))
    target_keypairs, key_findings = _verify_target_keypairs(
        inventory,
        target_bindings,
        restore_evidence,
        source_keypairs=source_keypairs,
        target_root=target_root,
        work_root=work_root,
    )
    if key_findings:
        raise RecoveryError("restore_ineligible", "Restored key material differs")
    target_lock = _verify_target_maintenance_lock(
        inventory,
        target_bindings,
        restore_evidence,
        target_root=target_root,
        expected_identity=None,
    )
    _verify_target_configs(inventory, target_bindings, target_root=target_root)
    database_target = target_bindings.resource_paths[_DATABASE_RESOURCE_ID]
    database_wal = Path(f"{database_target}-wal")
    asset_record = resources.get(_ASSET_RESOURCE_ID)
    asset_source_root = Path(str(asset_record["source_path"])) if asset_record is not None else None
    target_audit = _audit_database(
        database_target,
        database_wal if database_wal.exists() else None,
        work_root=work_root,
        asset_root=target_bindings.resource_paths.get(_ASSET_RESOURCE_ID),
        asset_source_root=asset_source_root,
        identity_inventory=inventory,
        captured_keypairs=target_keypairs,
    )
    if cast("list[dict[str, object]]", target_audit["identity_findings"]):
        raise RecoveryError("restore_ineligible", "Restored identity state differs")
    source_audit = manifest.get("audit")
    if not isinstance(source_audit, dict):
        raise RecoveryError("invalid_manifest", "Source audit evidence is invalid")
    restore_manifest = {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "backup_id": backup_id,
        "source_manifest_sha256": verified.result.manifest_sha256,
        "source_inventory": json.loads(canonical_json_bytes(inventory)),
        "source_verification": verified.result.model_dump(mode="json"),
        "source_audit": source_audit,
        "source_audit_sha256": _sha256_bytes(_canonical_json(source_audit)),
        "source_keypairs": source_keypairs,
        "asset_source_root": str(asset_source_root) if asset_source_root is not None else None,
        "target_bindings": json.loads(canonical_json_bytes(target_bindings)),
        "target_root_mode": 0o700,
        "changes": sorted(changes, key=lambda item: str(item["binding_id"])),
        "target_keypairs": target_keypairs,
        "target_maintenance_lock": target_lock,
        "target_audit": target_audit,
        "target_audit_sha256": _sha256_bytes(_canonical_json(target_audit)),
        "expected_files": restore_evidence.serialized_files(),
        "expected_directories": restore_evidence.serialized_directories(),
        "absent_paths": sorted(restore_evidence.absent_paths),
    }
    restore_manifest_bytes = _canonical_json(restore_manifest)
    _write_new_file_under(
        target_output,
        Path("restore-manifest.json"),
        restore_manifest_bytes,
        mode=0o600,
    )
    _validate_target_tree(
        target_root,
        restore_evidence,
        special_files=frozenset({"restore-manifest.json"}),
    )
    manifest_digest = _sha256_bytes(restore_manifest_bytes)
    _fsync_tree(target_root)
    payload_digest = _tree_digest(target_root, exclude=frozenset({"COMPLETE"}))
    _revalidate_verified_backup(verified)
    target_output.verify()
    target_parent.verify()
    return _publish_marker_pinned(
        target_output,
        target_parent,
        backup_id=backup_id,
        manifest_sha256=manifest_digest,
        payload_sha256=payload_digest,
    )


def _restore_backup_with_workspace(
    *,
    backup_root: Path,
    backup_id: str,
    target_bindings: TargetBindingsV1,
    target_root: Path,
    work_root: Path,
    work_output: _PinnedDirectory,
) -> PublicationResult:
    """Restore verified bytes and render captured configs into one fresh target."""

    if not isinstance(target_bindings, TargetBindingsV1):
        raise TypeError("target_bindings must be TargetBindingsV1")
    _validate_restore_roots(
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        target_root=target_root,
    )
    verified = _verify_backup_data_with_work_root(
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        work_output=work_output,
    )
    verification = verified.result
    manifest = verified.manifest
    if not verification.snapshot_preserved or not verification.identity_complete:
        raise RecoveryError(
            "restore_ineligible",
            "Fresh restore requires preserved bytes and complete identity material",
        )
    try:
        inventory = RecoveryInventoryV1.model_validate_json(_canonical_json(manifest["inventory"]))
    except (KeyError, ValueError) as exc:
        raise RecoveryError("invalid_manifest", "Embedded recovery inventory is invalid") from exc
    _validate_target_bindings(inventory, target_bindings, target_root=target_root)
    target_output, target_parent = _create_fresh_output_root(target_root)
    primary_error: BaseException | None = None
    try:
        return _populate_restored_target(
            verified,
            inventory,
            target_bindings,
            backup_id=backup_id,
            target_root=target_root,
            work_root=work_output,
            target_output=target_output,
            target_parent=target_parent,
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        target_output.close(primary_error=primary_error)
        target_parent.close(primary_error=primary_error)


def restore_backup(
    *,
    backup_root: Path,
    backup_id: str,
    target_bindings: TargetBindingsV1,
    target_root: Path,
    work_root: Path,
) -> PublicationResult:
    """Restore one backup while holding the cooperative recovery workspace lease."""

    if not isinstance(target_bindings, TargetBindingsV1):
        raise TypeError("target_bindings must be TargetBindingsV1")
    _validate_backup_id(backup_id)
    _require_canonical_operation_path(backup_root, name="backup_root")
    _require_canonical_operation_path(work_root, name="work_root")
    _require_canonical_operation_path(target_root, name="target_root")
    if _paths_overlap(backup_root, work_root) or _paths_overlap(target_root, work_root):
        raise RecoveryError("path_overlap", "work_root overlaps recovery storage")
    with _hold_recovery_workspace(work_root) as workspace:
        return _restore_backup_with_workspace(
            backup_root=backup_root,
            backup_id=backup_id,
            target_bindings=target_bindings,
            target_root=target_root,
            work_root=work_root,
            work_output=workspace.root,
        )


def _restore_contracts(
    manifest: dict[str, Any], target_root: Path
) -> tuple[RecoveryInventoryV1, TargetBindingsV1]:
    try:
        inventory = RecoveryInventoryV1.model_validate_json(
            _canonical_json(manifest["source_inventory"])
        )
        target_bindings = TargetBindingsV1.model_validate_json(
            _canonical_json(manifest["target_bindings"])
        )
    except (KeyError, ValueError) as exc:
        raise RecoveryError("invalid_manifest", "Restore contracts are invalid") from exc
    if target_bindings.target_root != target_root:
        raise RecoveryError("target_mismatch", "Restore target root differs from manifest")
    _validate_inventory_completeness(inventory)
    _validate_target_bindings(inventory, target_bindings, target_root=target_root)
    return inventory, target_bindings


def _restore_audits(manifest: dict[str, Any]) -> dict[str, object]:
    source_verification = manifest.get("source_verification")
    if not isinstance(source_verification, dict):
        raise RecoveryError("invalid_manifest", "Source verification evidence is invalid")
    if (
        source_verification.get("artifact_verified") is not True
        or source_verification.get("snapshot_preserved") is not True
        or source_verification.get("identity_complete") is not True
        or source_verification.get("migration_eligible") is not False
        or source_verification.get("operational_restore_verified") is not False
        or source_verification.get("manifest_sha256") != manifest.get("source_manifest_sha256")
    ):
        raise RecoveryError("invalid_manifest", "Source verification states are invalid")
    source_audit = manifest.get("source_audit")
    target_audit = manifest.get("target_audit")
    if not isinstance(source_audit, dict) or not isinstance(target_audit, dict):
        raise RecoveryError("invalid_manifest", "Restore audit evidence is invalid")
    if _sha256_bytes(_canonical_json(source_audit)) != manifest.get("source_audit_sha256"):
        raise RecoveryError("audit_mismatch", "Source audit digest differs")
    if _sha256_bytes(_canonical_json(target_audit)) != manifest.get("target_audit_sha256"):
        raise RecoveryError("audit_mismatch", "Target audit digest differs")
    return target_audit


def _load_restore_manifest(target_root: Path) -> _VerifiedRestoreManifest:
    _require_directory_mode(target_root, mode=0o700, name="restored target root")
    marker = _read_json_regular(target_root / "COMPLETE", missing_reason="incomplete_bundle")
    if marker.get("bundle_format_version") != BUNDLE_FORMAT_VERSION:
        raise RecoveryError("unknown_format", "Unsupported restore format")
    marker_alias = marker.get("temporary_marker_name")
    if not isinstance(marker_alias, str) or not marker_alias.startswith(".COMPLETE-"):
        raise RecoveryError("invalid_manifest", "Restore marker alias name is invalid")
    manifest_path = target_root / "restore-manifest.json"
    manifest_bytes, manifest_mode = _read_pinned_regular(manifest_path, label="restore manifest")
    if manifest_mode != 0o600:
        raise RecoveryError("unsafe_mode", "Restore manifest mode must be 0600")
    manifest_digest = _sha256_bytes(manifest_bytes)
    if manifest_digest != marker.get("manifest_sha256"):
        raise RecoveryError("digest_mismatch", "Restore manifest digest differs")
    if _tree_digest(target_root, exclude=frozenset({"COMPLETE", marker_alias})) != marker.get(
        "payload_sha256"
    ):
        raise RecoveryError("digest_mismatch", "Restored target digest differs")
    try:
        manifest_value = json.loads(manifest_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RecoveryError("invalid_manifest", "Restore manifest JSON is invalid") from exc
    if not isinstance(manifest_value, dict):
        raise RecoveryError("invalid_manifest", "Restore manifest must be an object")
    manifest = cast("dict[str, Any]", manifest_value)
    if marker.get("backup_id") != manifest.get("backup_id"):
        raise RecoveryError("marker_mismatch", "Restore backup ID evidence differs")
    if manifest.get("target_root_mode") != 0o700:
        raise RecoveryError("invalid_manifest", "Target root mode evidence is invalid")
    inventory, target_bindings = _restore_contracts(manifest, target_root)
    target_audit_recorded = _restore_audits(manifest)
    evidence = _restore_evidence_from_manifest(manifest)
    target_lock_value = manifest.get("target_maintenance_lock")
    if not isinstance(target_lock_value, dict):
        raise RecoveryError("invalid_manifest", "Target maintenance identity is invalid")
    source_keypairs = manifest.get("source_keypairs")
    target_keypairs_recorded = manifest.get("target_keypairs")
    if not isinstance(source_keypairs, dict) or not isinstance(target_keypairs_recorded, dict):
        raise RecoveryError("invalid_manifest", "Target keypair evidence is invalid")
    asset_source_value = manifest.get("asset_source_root")
    if asset_source_value is not None and not isinstance(asset_source_value, str):
        raise RecoveryError("invalid_manifest", "Restore asset source root is invalid")
    return _VerifiedRestoreManifest(
        digest=manifest_digest,
        marker_alias=marker_alias,
        inventory=inventory,
        target_bindings=target_bindings,
        evidence=evidence,
        target_lock=cast("dict[str, object]", target_lock_value),
        source_keypairs=cast("dict[str, dict[str, object]]", source_keypairs),
        target_keypairs=cast("dict[str, dict[str, object]]", target_keypairs_recorded),
        target_audit=target_audit_recorded,
        asset_source_root=(
            Path(asset_source_value) if isinstance(asset_source_value, str) else None
        ),
    )


def _verify_restored_contents(
    verified: _VerifiedRestoreManifest,
    *,
    target_root: Path,
    work_root: _PinnedDirectory,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    special_files = frozenset({"COMPLETE", verified.marker_alias, "restore-manifest.json"})
    _validate_target_tree(target_root, verified.evidence, special_files=special_files)
    _verify_target_maintenance_lock(
        verified.inventory,
        verified.target_bindings,
        verified.evidence,
        target_root=target_root,
        expected_identity=verified.target_lock,
    )
    _verify_target_configs(verified.inventory, verified.target_bindings, target_root=target_root)
    target_keypairs, key_findings = _verify_target_keypairs(
        verified.inventory,
        verified.target_bindings,
        verified.evidence,
        source_keypairs=verified.source_keypairs,
        target_root=target_root,
        work_root=work_root,
    )
    if target_keypairs != verified.target_keypairs:
        raise RecoveryError("target_mismatch", "Target keypair evidence differs")
    agent_owner_findings = _agent_runtime_owner_findings(
        verified.inventory,
        verified.target_bindings,
        verified.evidence,
        target_root=target_root,
    )
    database = verified.target_bindings.resource_paths[_DATABASE_RESOURCE_ID]
    wal = Path(f"{database}-wal")
    target_audit = _audit_database(
        database,
        wal if wal.exists() else None,
        work_root=work_root,
        asset_root=verified.target_bindings.resource_paths.get(_ASSET_RESOURCE_ID),
        asset_source_root=verified.asset_source_root,
        identity_inventory=verified.inventory,
        captured_keypairs=target_keypairs,
    )
    if _canonical_json(target_audit) != _canonical_json(verified.target_audit):
        raise RecoveryError("audit_mismatch", "Recomputed target audit differs")
    _validate_target_tree(target_root, verified.evidence, special_files=special_files)
    audit_findings = cast("list[dict[str, object]]", target_audit["findings"])
    identity_findings = cast("list[dict[str, object]]", target_audit["identity_findings"])
    return audit_findings, [*key_findings, *identity_findings, *agent_owner_findings]


def _verify_restore_with_workspace(
    *, target_root: Path, work_root: Path, work_output: _PinnedDirectory
) -> VerificationResult:
    """Verify a fresh target without opening or mutating its restored raw database."""

    _require_canonical_operation_path(target_root, name="target_root")
    _require_canonical_operation_path(work_root, name="work_root")
    _require_real_directory(target_root, name="target_root")
    _require_real_directory(work_root, name="work_root")
    if _paths_overlap(target_root, work_root):
        raise RecoveryError("path_overlap", "work_root overlaps restored target")
    verified = _load_restore_manifest(target_root)
    audit_findings, all_identity_findings = _verify_restored_contents(
        verified, target_root=target_root, work_root=work_output
    )
    finding_codes = sorted(
        {str(finding["code"]) for finding in [*audit_findings, *all_identity_findings]}
    )
    return VerificationResult(
        artifact_verified=True,
        snapshot_preserved=True,
        identity_complete=not all_identity_findings,
        migration_eligible=False,
        operational_restore_verified=False,
        finding_codes=tuple(finding_codes),
        manifest_sha256=verified.digest,
    )


def verify_restore(*, target_root: Path, work_root: Path) -> VerificationResult:
    """Verify a restored target while holding the cooperative workspace lease."""

    _require_canonical_operation_path(target_root, name="target_root")
    _require_canonical_operation_path(work_root, name="work_root")
    if _paths_overlap(target_root, work_root):
        raise RecoveryError("path_overlap", "work_root overlaps restored target")
    with _hold_recovery_workspace(work_root) as workspace:
        return _verify_restore_with_workspace(
            target_root=target_root,
            work_root=work_root,
            work_output=workspace.root,
        )


def _finalize_restore_with_workspace(
    *, target_root: Path, work_root: Path, work_output: _PinnedDirectory
) -> VerificationResult:
    """After exact verification, retry only durability syncs for a restored target."""

    result = _verify_restore_with_workspace(
        target_root=target_root,
        work_root=work_root,
        work_output=work_output,
    )
    parent = _open_pinned_directory(target_root.parent, label="restore target parent")
    target = _open_pinned_child(parent, target_root.name)
    primary_error: BaseException | None = None
    try:
        marker = _read_json_regular(target_root / "COMPLETE", missing_reason="incomplete_bundle")
        _finalize_marker_alias_pinned(target, marker)
        _fsync_tree(target_root)
        os.fsync(target.descriptor)
        os.fsync(parent.descriptor)
        target.verify()
        parent.verify()
    except BaseException as error:
        primary_error = error
        raise
    finally:
        target.close(primary_error=primary_error)
        parent.close(primary_error=primary_error)
    return result


def finalize_restore(*, target_root: Path, work_root: Path) -> VerificationResult:
    """Finalize a restored target while holding the cooperative workspace lease."""

    _require_canonical_operation_path(target_root, name="target_root")
    _require_canonical_operation_path(work_root, name="work_root")
    if _paths_overlap(target_root, work_root):
        raise RecoveryError("path_overlap", "work_root overlaps restored target")
    with _hold_recovery_workspace(work_root) as workspace:
        return _finalize_restore_with_workspace(
            target_root=target_root,
            work_root=work_root,
            work_output=workspace.root,
        )
