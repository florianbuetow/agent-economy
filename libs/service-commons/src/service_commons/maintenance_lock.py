"""Process-wide maintenance locking for shared runtime resources."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


__all__ = [
    "HeldMaintenanceLock",
    "MaintenanceLockError",
    "MaintenanceLockMode",
    "hold_existing_maintenance_lock",
    "hold_maintenance_lock",
]


class MaintenanceLockMode(StrEnum):
    """The advisory access mode for a maintenance lock."""

    SHARED = "shared"
    EXCLUSIVE = "exclusive"


class MaintenanceLockError(RuntimeError):
    """Raised when the maintenance-lock contract cannot be satisfied."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class HeldMaintenanceLock:
    """Immutable identity of a maintenance lock while its descriptor is held."""

    path: Path
    device: int
    inode: int
    _file_descriptor: int = field(repr=False, compare=False)
    _ancestor_identities: tuple[tuple[int, int], ...] = field(repr=False, compare=False)
    _active: bool = field(default=True, repr=False, compare=False)

    def verify_identity(self) -> None:
        """Verify that this active handle still owns the configured lock identity."""

        self._verify_identity(stage="while held")

    def _verify_identity(self, *, stage: str) -> None:
        if not self._active:
            msg = f"Maintenance lock handle is no longer active: {self.path}"
            raise MaintenanceLockError("inactive", msg)
        _verify_held_lock_identity(self, stage=stage)

    def _deactivate(self) -> None:
        object.__setattr__(self, "_active", False)


def _validate_arguments(
    path: Path,
    *,
    mode: MaintenanceLockMode,
    blocking: bool,
) -> None:
    if not isinstance(path, Path):
        msg = "Maintenance lock path must be a pathlib.Path"
        raise TypeError(msg)
    if not isinstance(mode, MaintenanceLockMode):
        msg = "Maintenance lock mode must be a MaintenanceLockMode"
        raise TypeError(msg)
    if not isinstance(blocking, bool):
        msg = "Maintenance lock blocking flag must be a bool"
        raise TypeError(msg)
    if not path.is_absolute() or path.anchor != os.sep or ".." in path.parts or not path.name:
        msg = f"Maintenance lock path must be absolute and lexically canonical: {path}"
        raise MaintenanceLockError("unsafe_path", msg)


def _validate_expected_identity(*, expected_device: int, expected_inode: int) -> None:
    if type(expected_device) is not int or type(expected_inode) is not int:
        msg = "Expected maintenance lock device and inode must be strict integers"
        raise TypeError(msg)
    if expected_device < 0:
        msg = "Expected maintenance lock device must be non-negative"
        raise ValueError(msg)
    if expected_inode <= 0:
        msg = "Expected maintenance lock inode must be positive"
        raise ValueError(msg)


def _path_kind(parent_descriptor: int, name: str, *, path: Path) -> str:
    try:
        path_stat = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        msg = f"Maintenance lock path cannot be inspected safely: {path}"
        raise MaintenanceLockError("unsafe_path", msg) from exc
    if stat.S_ISLNK(path_stat.st_mode):
        return "symlink"
    if stat.S_ISREG(path_stat.st_mode):
        return "regular"
    return "other"


def _close_descriptors(
    file_descriptors: tuple[int, ...],
    *,
    primary_error: BaseException | None,
) -> None:
    close_errors: list[BaseException] = []
    for file_descriptor in reversed(file_descriptors):
        try:
            os.close(file_descriptor)
        except BaseException as close_error:
            close_errors.append(close_error)
    if not close_errors:
        return
    if primary_error is not None:
        for recorded_error in close_errors:
            primary_error.add_note(f"Maintenance lock close also failed: {recorded_error}")
        return
    msg = "Maintenance lock descriptor release failed"
    raise MaintenanceLockError("release_failed", msg) from close_errors[0]


def _assert_current_directory_identity(
    parent_descriptor: int,
    name: str,
    file_descriptor: int,
    *,
    path: Path,
) -> None:
    try:
        opened_stat = os.fstat(file_descriptor)
        current_stat = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        msg = f"Maintenance lock ancestor changed while opening: {path}"
        raise MaintenanceLockError("unsafe_path", msg) from exc
    if (
        not stat.S_ISDIR(opened_stat.st_mode)
        or not stat.S_ISDIR(current_stat.st_mode)
        or opened_stat.st_dev != current_stat.st_dev
        or opened_stat.st_ino != current_stat.st_ino
    ):
        msg = f"Maintenance lock ancestor changed while opening: {path}"
        raise MaintenanceLockError("unsafe_path", msg)


def _open_verified_parent(path: Path) -> tuple[int, ...]:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        root_descriptor = os.open(os.sep, directory_flags)
    except OSError as exc:
        msg = f"Maintenance lock root directory cannot be opened safely: {path}"
        raise MaintenanceLockError("unsafe_path", msg) from exc

    descriptors = [root_descriptor]
    try:
        root_stat = os.fstat(root_descriptor)
        if not stat.S_ISDIR(root_stat.st_mode):
            msg = f"Maintenance lock root is not a directory: {path}"
            raise MaintenanceLockError("unsafe_path", msg)
        for component in path.parent.parts[1:]:
            parent_descriptor = descriptors[-1]
            try:
                component_descriptor = os.open(
                    component,
                    directory_flags,
                    dir_fd=parent_descriptor,
                )
            except OSError as exc:
                msg = f"Maintenance lock ancestor cannot be opened safely: {path.parent}"
                raise MaintenanceLockError("unsafe_path", msg) from exc
            try:
                _assert_current_directory_identity(
                    parent_descriptor,
                    component,
                    component_descriptor,
                    path=path,
                )
            except BaseException as primary_error:
                _close_descriptors(
                    (component_descriptor,),
                    primary_error=primary_error,
                )
                raise
            descriptors.append(component_descriptor)
    except BaseException as primary_error:
        _close_descriptors(tuple(descriptors), primary_error=primary_error)
        raise
    return tuple(descriptors)


def _assert_current_lock_identity(
    parent_descriptor: int,
    name: str,
    file_descriptor: int,
    *,
    path: Path,
    stage: str,
    expected_identity: tuple[int, int] | None = None,
) -> os.stat_result:
    try:
        opened_stat = os.fstat(file_descriptor)
        current_stat = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        msg = f"Maintenance lock path changed {stage}: {path}"
        raise MaintenanceLockError("unsafe_path", msg) from exc
    opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
    if (
        not stat.S_ISREG(opened_stat.st_mode)
        or not stat.S_ISREG(current_stat.st_mode)
        or opened_identity != (current_stat.st_dev, current_stat.st_ino)
    ):
        msg = f"Maintenance lock path changed {stage}: {path}"
        raise MaintenanceLockError("unsafe_path", msg)
    if expected_identity is not None and opened_identity != expected_identity:
        msg = (
            f"Maintenance lock differs from recorded identity {stage}: {path}; "
            f"expected device={expected_identity[0]}, inode={expected_identity[1]}, "
            f"got device={opened_identity[0]}, inode={opened_identity[1]}"
        )
        raise MaintenanceLockError("identity_mismatch", msg)
    opened_mode = stat.S_IMODE(opened_stat.st_mode)
    current_mode = stat.S_IMODE(current_stat.st_mode)
    if opened_mode != 0o600 or current_mode != 0o600:
        msg = (
            f"Unsafe maintenance lock mode for {path}: expected 0600, "
            f"got opened={opened_mode:04o}, current={current_mode:04o}"
        )
        raise MaintenanceLockError("unsafe_mode", msg)
    return opened_stat


def _descriptor_identities(
    directory_descriptors: tuple[int, ...],
    *,
    path: Path,
) -> tuple[tuple[int, int], ...]:
    try:
        return tuple(
            (directory_stat.st_dev, directory_stat.st_ino)
            for directory_stat in map(os.fstat, directory_descriptors)
        )
    except OSError as exc:
        msg = f"Maintenance lock ancestor cannot be inspected safely: {path.parent}"
        raise MaintenanceLockError("unsafe_path", msg) from exc


def _verify_held_lock_identity(held_lock: HeldMaintenanceLock, *, stage: str) -> None:
    directory_descriptors = _open_verified_parent(held_lock.path)
    primary_error: BaseException | None = None
    try:
        current_ancestor_identities = _descriptor_identities(
            directory_descriptors,
            path=held_lock.path,
        )
        if current_ancestor_identities != held_lock._ancestor_identities:
            msg = f"Maintenance lock ancestor changed {stage}: {held_lock.path}"
            raise MaintenanceLockError("unsafe_path", msg)
        _assert_current_lock_identity(
            directory_descriptors[-1],
            held_lock.path.name,
            held_lock._file_descriptor,
            path=held_lock.path,
            stage=stage,
            expected_identity=(held_lock.device, held_lock.inode),
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        _close_descriptors(
            directory_descriptors,
            primary_error=primary_error,
        )


def _open_lock_file(
    path: Path,
    *,
    create: bool,
    expected_identity: tuple[int, int] | None,
) -> tuple[int, tuple[tuple[int, int], ...], tuple[int, int]]:
    directory_descriptors = _open_verified_parent(path)
    parent_descriptor = directory_descriptors[-1]
    file_descriptor: int | None = None
    try:
        path_kind = _path_kind(parent_descriptor, path.name, path=path)
        if path_kind in {"symlink", "other"}:
            msg = f"Maintenance lock must be a regular file, not a symlink: {path}"
            raise MaintenanceLockError("unsafe_path", msg)

        flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
        created = False
        if path_kind == "missing":
            if not create:
                msg = f"Recorded maintenance lock does not exist: {path}"
                raise MaintenanceLockError("unsafe_path", msg)
            try:
                file_descriptor = os.open(
                    path.name,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent_descriptor,
                )
                created = True
            except FileExistsError:
                file_descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        else:
            file_descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        if created:
            os.fchmod(file_descriptor, 0o600)
        lock_stat = _assert_current_lock_identity(
            parent_descriptor,
            path.name,
            file_descriptor,
            path=path,
            stage="after open",
            expected_identity=expected_identity,
        )
        ancestor_identities = _descriptor_identities(directory_descriptors, path=path)
    except OSError as exc:
        primary_error = MaintenanceLockError(
            "unsafe_path",
            f"Maintenance lock path cannot be opened safely: {path}",
        )
        if file_descriptor is not None:
            _close_descriptors((file_descriptor,), primary_error=primary_error)
        _close_descriptors(directory_descriptors, primary_error=primary_error)
        raise primary_error from exc
    except BaseException as primary_error:
        if file_descriptor is not None:
            _close_descriptors((file_descriptor,), primary_error=primary_error)
        _close_descriptors(directory_descriptors, primary_error=primary_error)
        raise
    try:
        _close_descriptors(directory_descriptors, primary_error=None)
    except BaseException as primary_error:
        _close_descriptors((file_descriptor,), primary_error=primary_error)
        raise
    return (
        file_descriptor,
        ancestor_identities,
        (lock_stat.st_dev, lock_stat.st_ino),
    )


def _lock_operation(mode: MaintenanceLockMode, *, blocking: bool) -> int:
    operation = fcntl.LOCK_SH if mode is MaintenanceLockMode.SHARED else fcntl.LOCK_EX
    if not blocking:
        operation |= fcntl.LOCK_NB
    return operation


def _cleanup_lock(
    file_descriptor: int,
    *,
    locked: bool,
    primary_error: BaseException | None,
) -> None:
    cleanup_errors: list[tuple[str, BaseException]] = []
    if locked:
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        except BaseException as unlock_error:
            cleanup_errors.append(("unlock", unlock_error))
    try:
        os.close(file_descriptor)
    except BaseException as close_error:
        cleanup_errors.append(("close", close_error))

    if not cleanup_errors:
        return
    if primary_error is not None:
        for operation, cleanup_error in cleanup_errors:
            primary_error.add_note(f"Maintenance lock {operation} also failed: {cleanup_error}")
        return

    operations = ", ".join(operation for operation, _error in cleanup_errors)
    msg = f"Maintenance lock release failed during: {operations}"
    raise MaintenanceLockError("release_failed", msg) from cleanup_errors[0][1]


@contextmanager
def _hold_maintenance_lock(
    path: Path,
    *,
    mode: MaintenanceLockMode,
    blocking: bool,
    create: bool,
    expected_identity: tuple[int, int] | None,
) -> Iterator[HeldMaintenanceLock]:
    _validate_arguments(path, mode=mode, blocking=blocking)
    file_descriptor, ancestor_identities, lock_identity = _open_lock_file(
        path,
        create=create,
        expected_identity=expected_identity,
    )
    held_lock = HeldMaintenanceLock(
        path=path,
        device=lock_identity[0],
        inode=lock_identity[1],
        _file_descriptor=file_descriptor,
        _ancestor_identities=ancestor_identities,
    )
    locked = False
    entered = False
    primary_error: BaseException | None = None
    try:
        held_lock._verify_identity(stage="before flock")
        if expected_identity is not None and lock_identity != expected_identity:
            msg = f"Maintenance lock differs from recorded identity before flock: {path}"
            raise MaintenanceLockError("identity_mismatch", msg)
        try:
            fcntl.flock(file_descriptor, _lock_operation(mode, blocking=blocking))
        except OSError as exc:
            if not blocking and exc.errno in {errno.EACCES, errno.EAGAIN}:
                msg = f"Maintenance lock is already held incompatibly: {path}"
                raise MaintenanceLockError("busy", msg) from exc
            msg = f"Maintenance lock acquisition failed: {path}"
            raise MaintenanceLockError("lock_failed", msg) from exc
        locked = True
        held_lock._verify_identity(stage="after flock")
        if expected_identity is not None and lock_identity != expected_identity:
            msg = f"Maintenance lock differs from recorded identity after flock: {path}"
            raise MaintenanceLockError("identity_mismatch", msg)
        entered = True
        yield held_lock
    except BaseException as error:
        primary_error = error
        raise
    finally:
        verification_error: BaseException | None = None
        if locked and entered:
            try:
                held_lock._verify_identity(stage="before unlock")
            except BaseException as error:
                if primary_error is not None:
                    primary_error.add_note(
                        f"Maintenance lock identity verification also failed: {error}"
                    )
                else:
                    verification_error = error
        held_lock._deactivate()
        _cleanup_lock(
            file_descriptor,
            locked=locked,
            primary_error=primary_error or verification_error,
        )
        if verification_error is not None:
            raise verification_error


@contextmanager
def hold_maintenance_lock(
    path: Path,
    *,
    mode: MaintenanceLockMode,
    blocking: bool,
) -> Iterator[HeldMaintenanceLock]:
    """Hold a persistent advisory lock after verifying its path and inode."""

    with _hold_maintenance_lock(
        path,
        mode=mode,
        blocking=blocking,
        create=True,
        expected_identity=None,
    ) as held_lock:
        yield held_lock


@contextmanager
def hold_existing_maintenance_lock(
    path: Path,
    *,
    mode: MaintenanceLockMode,
    blocking: bool,
    expected_device: int,
    expected_inode: int,
) -> Iterator[HeldMaintenanceLock]:
    """Hold only an existing maintenance lock with the recorded kernel identity."""

    _validate_expected_identity(
        expected_device=expected_device,
        expected_inode=expected_inode,
    )
    with _hold_maintenance_lock(
        path,
        mode=mode,
        blocking=blocking,
        create=False,
        expected_identity=(expected_device, expected_inode),
    ) as held_lock:
        yield held_lock
