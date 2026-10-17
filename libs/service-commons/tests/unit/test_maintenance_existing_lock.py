"""Acceptance coverage for opening a recorded maintenance-lock identity."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
from typing import TYPE_CHECKING, Any, cast

import pytest

import service_commons.maintenance_lock as maintenance_lock_module
from service_commons.maintenance_lock import (
    HeldMaintenanceLock,
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_existing_maintenance_lock,
)

if TYPE_CHECKING:
    from pathlib import Path


def _identity(path: Path) -> tuple[int, int]:
    path_stat = path.lstat()
    return path_stat.st_dev, path_stat.st_ino


@pytest.mark.unit
def test_holds_only_the_recorded_existing_lock_identity(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"
    lock_path.touch(mode=0o600)
    expected_device, expected_inode = _identity(lock_path)

    with hold_existing_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
        expected_device=expected_device,
        expected_inode=expected_inode,
    ) as held_lock:
        assert isinstance(held_lock, HeldMaintenanceLock)
        assert (held_lock.device, held_lock.inode) == (expected_device, expected_inode)
        held_lock.verify_identity()


@pytest.mark.unit
def test_missing_recorded_lock_is_not_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    real_open = maintenance_lock_module.os.open
    observed_flags: list[int] = []

    def recording_open(
        open_path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        observed_flags.append(flags)
        return real_open(cast("Any", open_path), flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(maintenance_lock_module.os, "open", recording_open)

    with (
        pytest.raises(MaintenanceLockError, match="does not exist") as exc_info,
        hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=0,
            expected_inode=1,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"
    assert observed_flags
    assert all(flags & os.O_CREAT == 0 for flags in observed_flags)
    assert not lock_path.exists()


@pytest.mark.unit
def test_missing_recorded_lock_parent_is_not_created(tmp_path: Path) -> None:
    missing_parent = tmp_path / "absent"
    lock_path = missing_parent / "maintenance.lock"

    with (
        pytest.raises(MaintenanceLockError) as exc_info,
        hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=0,
            expected_inode=1,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"
    assert not missing_parent.exists()


@pytest.mark.unit
def test_unsafe_existing_mode_is_rejected_without_chmod(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"
    lock_path.touch(mode=0o640)
    lock_path.chmod(0o640)
    expected_device, expected_inode = _identity(lock_path)

    with (
        pytest.raises(MaintenanceLockError, match="expected 0600") as exc_info,
        hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=expected_device,
            expected_inode=expected_inode,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_mode"
    assert stat.S_IMODE(lock_path.lstat().st_mode) == 0o640


@pytest.mark.unit
@pytest.mark.parametrize(
    ("expected_device", "expected_inode", "exception_type"),
    [
        (True, 1, TypeError),
        (0.0, 1, TypeError),
        (0, True, TypeError),
        (0, 1.0, TypeError),
        (-1, 1, ValueError),
        (0, 0, ValueError),
        (0, -1, ValueError),
    ],
)
def test_expected_identity_requires_strict_kernel_identifiers(
    tmp_path: Path,
    expected_device: object,
    expected_inode: object,
    exception_type: type[Exception],
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    lock_path.touch(mode=0o600)

    with (
        pytest.raises(exception_type),
        hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=cast("Any", expected_device),
            expected_inode=cast("Any", expected_inode),
        ),
    ):
        pass


@pytest.mark.unit
def test_wrong_recorded_inode_is_rejected_before_any_flock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    lock_path.touch(mode=0o600)
    expected_device, actual_inode = _identity(lock_path)
    flock_operations: list[int] = []

    def recording_flock(file_descriptor: int, operation: int) -> None:
        del file_descriptor
        flock_operations.append(operation)

    monkeypatch.setattr(maintenance_lock_module.fcntl, "flock", recording_flock)

    with (
        pytest.raises(MaintenanceLockError, match="recorded identity") as exc_info,
        hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=expected_device,
            expected_inode=actual_inode + 1,
        ),
    ):
        pass

    assert exc_info.value.reason == "identity_mismatch"
    assert flock_operations == []


@pytest.mark.unit
@pytest.mark.parametrize("replace_before_real_flock", [True, False])
def test_recorded_lock_swap_during_flock_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_before_real_flock: bool,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    lock_path.touch(mode=0o600)
    expected_device, expected_inode = _identity(lock_path)
    displaced_path = tmp_path / "displaced.lock"
    real_flock = maintenance_lock_module.fcntl.flock
    replaced = False

    def replacing_flock(file_descriptor: int, operation: int) -> None:
        nonlocal replaced
        is_acquire = operation & (fcntl.LOCK_SH | fcntl.LOCK_EX)
        if is_acquire and not replaced:
            replaced = True
            if replace_before_real_flock:
                lock_path.replace(displaced_path)
                lock_path.touch(mode=0o600)
            real_flock(file_descriptor, operation)
            if not replace_before_real_flock:
                lock_path.replace(displaced_path)
                lock_path.touch(mode=0o600)
            return
        real_flock(file_descriptor, operation)

    monkeypatch.setattr(maintenance_lock_module.fcntl, "flock", replacing_flock)

    with (
        pytest.raises(MaintenanceLockError, match="changed after flock") as exc_info,
        hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=expected_device,
            expected_inode=expected_inode,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"


@pytest.mark.unit
def test_exception_path_unlocks_closes_and_deactivates_existing_handle(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    lock_path.touch(mode=0o600)
    expected_device, expected_inode = _identity(lock_path)
    held_lock: HeldMaintenanceLock | None = None

    with (
        pytest.raises(RuntimeError, match="primary failure"),
        hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=expected_device,
            expected_inode=expected_inode,
        ) as active_lock,
    ):
        held_lock = active_lock
        raise RuntimeError("primary failure")

    assert held_lock is not None
    with pytest.raises(MaintenanceLockError, match="no longer active") as exc_info:
        held_lock.verify_identity()
    assert exc_info.value.reason == "inactive"
    with pytest.raises(OSError) as closed_error:
        os.fstat(held_lock._file_descriptor)
    assert closed_error.value.errno == errno.EBADF

    descriptor = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
