"""Acceptance coverage for the cross-service maintenance lock."""

from __future__ import annotations

import fcntl
import multiprocessing
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

import service_commons.maintenance_lock as maintenance_lock_module
from service_commons.maintenance_lock import (
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_maintenance_lock,
)

if TYPE_CHECKING:
    from multiprocessing.connection import Connection


def _hold_lock_in_process(
    path_text: str,
    mode_text: str,
    blocking: bool,
    connection: Connection,
) -> None:
    try:
        mode = MaintenanceLockMode(mode_text)
        with hold_maintenance_lock(Path(path_text), mode=mode, blocking=blocking):
            connection.send(("acquired", ""))
            connection.recv()
    except MaintenanceLockError as exc:
        connection.send(("error", exc.reason))
    finally:
        connection.close()


def _start_lock_process(
    path: Path,
    *,
    mode: MaintenanceLockMode,
    blocking: bool,
) -> tuple[multiprocessing.Process, Connection]:
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe()
    process = context.Process(
        target=_hold_lock_in_process,
        args=(str(path), mode.value, blocking, child_connection),
    )
    process.start()
    child_connection.close()
    return process, parent_connection


def _finish_lock_process(
    process: multiprocessing.Process,
    connection: Connection,
    *,
    acquired: bool,
) -> None:
    try:
        if acquired and process.is_alive():
            connection.send("release")
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            pytest.fail("maintenance-lock child process did not exit")
        assert process.exitcode == 0
    finally:
        connection.close()


def _receive_result(connection: Connection) -> tuple[str, str]:
    assert connection.poll(5), "maintenance-lock child process did not report a result"
    result = connection.recv()
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert all(isinstance(item, str) for item in result)
    return cast("tuple[str, str]", result)


@pytest.mark.unit
def test_creates_persistent_regular_lock_with_exact_permissions(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"

    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        opened_stat = lock_path.lstat()
        assert stat.S_ISREG(opened_stat.st_mode)
        assert stat.S_IMODE(opened_stat.st_mode) == 0o600

    assert lock_path.is_file()
    assert stat.S_IMODE(lock_path.lstat().st_mode) == 0o600


@pytest.mark.unit
@pytest.mark.parametrize(
    ("invalid_mode", "invalid_blocking"),
    [
        ("shared", False),
        (MaintenanceLockMode.SHARED, 1),
    ],
)
def test_rejects_invalid_runtime_argument_types(
    tmp_path: Path,
    invalid_mode: object,
    invalid_blocking: object,
) -> None:
    with (
        pytest.raises(TypeError),
        hold_maintenance_lock(
            tmp_path / "maintenance.lock",
            mode=cast("Any", invalid_mode),
            blocking=cast("Any", invalid_blocking),
        ),
    ):
        pass


@pytest.mark.unit
def test_rejects_existing_lock_with_unsafe_permissions_without_changing_it(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    lock_path.touch(mode=0o644)
    lock_path.chmod(0o644)

    with (
        pytest.raises(MaintenanceLockError, match="expected 0600") as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.SHARED,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_mode"
    assert stat.S_IMODE(lock_path.lstat().st_mode) == 0o644


@pytest.mark.unit
@pytest.mark.parametrize("unsafe_kind", ["symlink", "directory"])
def test_rejects_nonregular_lock_paths(tmp_path: Path, unsafe_kind: str) -> None:
    lock_path = tmp_path / "maintenance.lock"
    if unsafe_kind == "symlink":
        target = tmp_path / "target.lock"
        target.touch(mode=0o600)
        lock_path.symlink_to(target)
    else:
        lock_path.mkdir()

    with (
        pytest.raises(MaintenanceLockError) as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"


@pytest.mark.unit
def test_rejects_missing_parent_without_creating_directories(tmp_path: Path) -> None:
    parent = tmp_path / "absent"
    lock_path = parent / "maintenance.lock"

    with (
        pytest.raises(MaintenanceLockError) as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"
    assert not parent.exists()


@pytest.mark.unit
def test_rejects_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "relative").mkdir()
    monkeypatch.chdir(tmp_path)

    with (
        pytest.raises(MaintenanceLockError) as exc_info,
        hold_maintenance_lock(
            Path("relative/maintenance.lock"),
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"


@pytest.mark.unit
def test_rejects_lexically_noncanonical_absolute_path(tmp_path: Path) -> None:
    (tmp_path / "child").mkdir()
    unsafe_path = tmp_path / "child" / ".." / "maintenance.lock"

    with (
        pytest.raises(MaintenanceLockError) as exc_info,
        hold_maintenance_lock(
            unsafe_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"


@pytest.mark.unit
def test_rejects_symlinked_ancestor_without_touching_target(tmp_path: Path) -> None:
    target_directory = tmp_path / "target"
    target_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(target_directory, target_is_directory=True)
    lock_path = linked_directory / "nested" / "maintenance.lock"
    (target_directory / "nested").mkdir()

    with (
        pytest.raises(MaintenanceLockError) as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"
    assert not (target_directory / "nested" / "maintenance.lock").exists()


@pytest.mark.unit
def test_releases_lock_when_context_body_raises(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"

    with (
        pytest.raises(RuntimeError, match="primary failure"),
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        raise RuntimeError("primary failure")

    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass


@pytest.mark.unit
def test_shared_locks_coexist_across_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"

    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.SHARED,
        blocking=False,
    ):
        process, connection = _start_lock_process(
            lock_path,
            mode=MaintenanceLockMode.SHARED,
            blocking=False,
        )
        try:
            assert _receive_result(connection) == ("acquired", "")
        finally:
            _finish_lock_process(process, connection, acquired=True)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("held_mode", "attempted_mode"),
    [
        (MaintenanceLockMode.SHARED, MaintenanceLockMode.EXCLUSIVE),
        (MaintenanceLockMode.EXCLUSIVE, MaintenanceLockMode.SHARED),
        (MaintenanceLockMode.EXCLUSIVE, MaintenanceLockMode.EXCLUSIVE),
    ],
)
def test_incompatible_nonblocking_lock_fails_meaningfully_across_processes(
    tmp_path: Path,
    held_mode: MaintenanceLockMode,
    attempted_mode: MaintenanceLockMode,
) -> None:
    lock_path = tmp_path / "maintenance.lock"

    with hold_maintenance_lock(lock_path, mode=held_mode, blocking=False):
        process, connection = _start_lock_process(
            lock_path,
            mode=attempted_mode,
            blocking=False,
        )
        try:
            assert _receive_result(connection) == ("error", "busy")
        finally:
            _finish_lock_process(process, connection, acquired=False)


@pytest.mark.unit
def test_concurrent_first_creators_share_one_persistent_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "maintenance.lock"
    processes_and_connections = [
        _start_lock_process(
            lock_path,
            mode=MaintenanceLockMode.SHARED,
            blocking=True,
        )
        for _ in range(8)
    ]

    try:
        results = [
            _receive_result(connection) for _process, connection in processes_and_connections
        ]
        assert results == [("acquired", "")] * 8
    finally:
        for process, connection in processes_and_connections:
            _finish_lock_process(process, connection, acquired=True)

    lock_stat = lock_path.lstat()
    assert stat.S_ISREG(lock_stat.st_mode)
    assert stat.S_IMODE(lock_stat.st_mode) == 0o600


@pytest.mark.unit
def test_lock_path_replacement_immediately_after_open_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    lock_path.touch(mode=0o600)
    original_path = tmp_path / "original.lock"
    real_open = maintenance_lock_module.os.open
    real_close = maintenance_lock_module.os.close
    replaced = False

    def replacing_open(
        open_path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        file_descriptor = real_open(open_path, flags, mode, dir_fd=dir_fd)
        if open_path == lock_path.name and dir_fd is not None and not replaced:
            replaced = True
            lock_path.replace(original_path)
            replacement_descriptor = real_open(
                lock_path,
                os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            real_close(replacement_descriptor)
        return file_descriptor

    monkeypatch.setattr(maintenance_lock_module.os, "open", replacing_open)

    with (
        pytest.raises(MaintenanceLockError, match="changed after open") as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"


@pytest.mark.unit
def test_ancestor_retarget_during_traversal_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guarded_parent = tmp_path / "guarded"
    guarded_parent.mkdir()
    original_parent = tmp_path / "original-guarded"
    replacement_lock = guarded_parent / "maintenance.lock"
    original_lock = original_parent / "maintenance.lock"
    real_open = maintenance_lock_module.os.open
    replaced = False

    def replacing_open(
        open_path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        file_descriptor = real_open(open_path, flags, mode, dir_fd=dir_fd)
        if open_path == guarded_parent.name and dir_fd is not None and not replaced:
            replaced = True
            guarded_parent.replace(original_parent)
            guarded_parent.mkdir()
            replacement_lock.touch(mode=0o600)
        return file_descriptor

    monkeypatch.setattr(maintenance_lock_module.os, "open", replacing_open)

    with (
        pytest.raises(MaintenanceLockError, match="changed") as exc_info,
        hold_maintenance_lock(
            replacement_lock,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"
    assert not original_lock.exists()
    assert replacement_lock.is_file()


@pytest.mark.unit
@pytest.mark.parametrize("replace_before_real_flock", [True, False])
def test_lock_path_replacement_during_acquisition_fails_closed_and_closes_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_before_real_flock: bool,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    lock_path.touch(mode=0o600)
    original_path = tmp_path / "original.lock"
    real_flock = maintenance_lock_module.fcntl.flock
    real_close = maintenance_lock_module.os.close
    closed_descriptors: list[int] = []
    lock_descriptor: int | None = None
    replaced = False

    def replacing_flock(file_descriptor: int, operation: int) -> None:
        nonlocal lock_descriptor, replaced
        is_acquire = operation & (fcntl.LOCK_SH | fcntl.LOCK_EX)
        if is_acquire and not replaced:
            lock_descriptor = file_descriptor
            replaced = True
            if replace_before_real_flock:
                lock_path.replace(original_path)
                lock_path.touch(mode=0o600)
            real_flock(file_descriptor, operation)
            if not replace_before_real_flock:
                lock_path.replace(original_path)
                lock_path.touch(mode=0o600)
            return
        real_flock(file_descriptor, operation)

    def recording_close(file_descriptor: int) -> None:
        closed_descriptors.append(file_descriptor)
        real_close(file_descriptor)

    monkeypatch.setattr(maintenance_lock_module.fcntl, "flock", replacing_flock)
    monkeypatch.setattr(maintenance_lock_module.os, "close", recording_close)

    with (
        pytest.raises(MaintenanceLockError, match="changed") as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "unsafe_path"
    assert lock_descriptor in closed_descriptors

    descriptor = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        real_flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        real_flock(descriptor, fcntl.LOCK_UN)
    finally:
        real_close(descriptor)


@pytest.mark.unit
def test_release_failures_do_not_mask_context_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    real_flock = maintenance_lock_module.fcntl.flock
    real_close = maintenance_lock_module.os.close
    lock_descriptor: int | None = None

    def failing_unlock(file_descriptor: int, operation: int) -> None:
        nonlocal lock_descriptor
        real_flock(file_descriptor, operation)
        if operation != fcntl.LOCK_UN:
            lock_descriptor = file_descriptor
        if operation == fcntl.LOCK_UN:
            raise OSError("injected unlock failure")

    def failing_close(file_descriptor: int) -> None:
        real_close(file_descriptor)
        if file_descriptor == lock_descriptor:
            raise OSError("injected close failure")

    monkeypatch.setattr(maintenance_lock_module.fcntl, "flock", failing_unlock)
    monkeypatch.setattr(maintenance_lock_module.os, "close", failing_close)

    with (
        pytest.raises(RuntimeError, match="primary failure") as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        raise RuntimeError("primary failure")

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("unlock" in note for note in notes)
    assert any("close" in note for note in notes)


@pytest.mark.unit
def test_release_failure_after_clean_body_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "maintenance.lock"
    real_flock = maintenance_lock_module.fcntl.flock

    def failing_unlock(file_descriptor: int, operation: int) -> None:
        real_flock(file_descriptor, operation)
        if operation == fcntl.LOCK_UN:
            raise OSError("injected unlock failure")

    monkeypatch.setattr(maintenance_lock_module.fcntl, "flock", failing_unlock)

    with (
        pytest.raises(MaintenanceLockError, match="release failed") as exc_info,
        hold_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
    ):
        pass

    assert exc_info.value.reason == "release_failed"
