"""Identity guarantees for a held maintenance lock."""

from __future__ import annotations

import dataclasses
import stat
from typing import TYPE_CHECKING

import pytest

from service_commons.maintenance_lock import (
    HeldMaintenanceLock,
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_maintenance_lock,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
def test_held_lock_exposes_immutable_verified_path_identity(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"

    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.SHARED,
        blocking=False,
    ) as held_lock:
        lock_stat = lock_path.lstat()

        assert isinstance(held_lock, HeldMaintenanceLock)
        assert held_lock.path == lock_path
        assert held_lock.device == lock_stat.st_dev
        assert held_lock.inode == lock_stat.st_ino
        held_lock.verify_identity()
        with hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.SHARED,
            blocking=False,
        ) as equal_held_lock:
            assert held_lock == equal_held_lock
        with pytest.raises(dataclasses.FrozenInstanceError):
            held_lock.path = tmp_path / "other.lock"  # type: ignore[misc]


@pytest.mark.unit
def test_replacing_held_lock_path_is_detected_before_unlock(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"
    displaced_path = tmp_path / "displaced.lock"

    with (
        pytest.raises(MaintenanceLockError, match="changed before unlock") as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        lock_path.replace(displaced_path)
        lock_path.touch(mode=0o600)

    assert exc_info.value.reason == "unsafe_path"


@pytest.mark.unit
def test_retargeting_held_lock_ancestor_is_detected_before_unlock(tmp_path: Path) -> None:
    guarded_parent = tmp_path / "guarded"
    guarded_parent.mkdir()
    lock_path = guarded_parent / "maintenance.lock"
    displaced_parent = tmp_path / "displaced-guarded"

    with (
        pytest.raises(MaintenanceLockError, match="ancestor changed before unlock") as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        guarded_parent.replace(displaced_parent)
        guarded_parent.mkdir()
        (guarded_parent / "maintenance.lock").touch(mode=0o600)

    assert exc_info.value.reason == "unsafe_path"


@pytest.mark.unit
def test_changing_held_lock_mode_is_detected_before_unlock(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"

    with (
        pytest.raises(MaintenanceLockError, match="expected 0600") as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        lock_path.chmod(0o640)

    assert exc_info.value.reason == "unsafe_mode"
    assert stat.S_IMODE(lock_path.lstat().st_mode) == 0o640


@pytest.mark.unit
def test_released_handle_rejects_identity_verification(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"

    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ) as held_lock:
        held_lock.verify_identity()

    with pytest.raises(MaintenanceLockError, match="no longer active") as exc_info:
        held_lock.verify_identity()

    assert exc_info.value.reason == "inactive"


@pytest.mark.unit
def test_identity_failure_during_exception_preserves_primary_error(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"
    displaced_path = tmp_path / "displaced.lock"

    with (
        pytest.raises(RuntimeError, match="primary failure") as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        lock_path.replace(displaced_path)
        lock_path.touch(mode=0o600)
        raise RuntimeError("primary failure")

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("identity verification also failed" in note for note in notes)
