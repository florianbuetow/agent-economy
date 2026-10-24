"""T-126 acceptance proofs for safe artifact-root initialization."""

from __future__ import annotations

import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from task_board_service.services import asset_manager as asset_manager_module
from task_board_service.services.asset_manager import AssetManager

_BARRIER_TIMEOUT_SECONDS = 5


def _manager(storage_root: Path) -> AssetManager:
    return AssetManager(
        store=cast("Any", object()),
        token_validator=AsyncMock(),
        deadline_evaluator=AsyncMock(),
        asset_storage_path=str(storage_root),
        max_file_size=32,
        max_files_per_task=2,
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int, bytes | None]]:
    return {
        path.relative_to(root).as_posix(): (
            "directory" if path.is_dir() else "file",
            stat.S_IMODE(path.lstat().st_mode),
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(root.rglob("*"))
    }


def _construct(storage_root: Path) -> tuple[AssetManager | None, BaseException | None]:
    try:
        return _manager(storage_root), None
    except BaseException as exc:
        return None, exc


@pytest.mark.unit
def test_root_creation_cannot_follow_ancestor_retarget_at_mkdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap either rejects or creates under the parent pinned before retarget."""
    intended_parent = tmp_path / "owned-intended-parent"
    replacement_parent = tmp_path / "owned-replacement-parent"
    intended_parent.mkdir(mode=0o700)
    replacement_parent.mkdir(mode=0o700)
    intended_parent.chmod(0o700)
    replacement_parent.chmod(0o700)
    configured_ancestor = tmp_path / "configured-ancestor"
    configured_ancestor.symlink_to(intended_parent, target_is_directory=True)
    storage_root = configured_ancestor / "artifact-root"

    pinned_parent = os.open(
        intended_parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    pinned_identity = os.fstat(pinned_parent)
    intended_identity = intended_parent.stat()
    assert (pinned_identity.st_dev, pinned_identity.st_ino) == (
        intended_identity.st_dev,
        intended_identity.st_ino,
    )
    replacement_before = _tree_snapshot(replacement_parent)
    mkdir_reached = threading.Barrier(2)
    retarget_complete = threading.Barrier(2)
    original_mkdir = asset_manager_module.os.mkdir
    intercepted = 0

    def barrier_mkdir(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal intercepted
        path_text = os.fsdecode(path)
        path_based_target = dir_fd is None and path_text == str(storage_root)
        descriptor_target = False
        if dir_fd is not None and Path(path_text).name == storage_root.name:
            observed = os.fstat(dir_fd)
            descriptor_target = (observed.st_dev, observed.st_ino) == (
                pinned_identity.st_dev,
                pinned_identity.st_ino,
            )
        if path_based_target or descriptor_target:
            intercepted += 1
            mkdir_reached.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            retarget_complete.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(asset_manager_module.os, "mkdir", barrier_mkdir)
    manager: AssetManager | None = None
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_construct, storage_root)
            mkdir_reached.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            configured_ancestor.unlink()
            configured_ancestor.symlink_to(replacement_parent, target_is_directory=True)
            retarget_complete.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            manager, error = future.result(timeout=_BARRIER_TIMEOUT_SECONDS)

        diagnostics = {
            "intercepted_mkdir_calls": intercepted,
            "constructor_error": None if error is None else repr(error),
            "intended_tree": _tree_snapshot(intended_parent),
            "replacement_tree": _tree_snapshot(replacement_parent),
        }
        assert intercepted == 1, "SETUP FAILURE: artifact-root mkdir was not intercepted"
        assert _tree_snapshot(replacement_parent) == replacement_before, diagnostics
        if manager is not None:
            assert error is None, diagnostics
            assert (intended_parent / storage_root.name).is_dir(), diagnostics
        else:
            assert error is not None, diagnostics
            assert not (replacement_parent / storage_root.name).exists(), diagnostics
    finally:
        if manager is not None:
            manager.close()
        os.close(pinned_parent)


@pytest.mark.unit
def test_existing_root_with_wrong_mode_is_rejected_without_mutation(tmp_path: Path) -> None:
    """An existing non-private root is rejected without chmod or content changes."""
    storage_root = tmp_path / "wrong-mode-root"
    storage_root.mkdir(mode=0o755)
    storage_root.chmod(0o755)
    sentinel = storage_root / "sentinel.txt"
    sentinel.write_bytes(b"owned sentinel")
    mode_before = stat.S_IMODE(storage_root.stat().st_mode)
    tree_before = _tree_snapshot(storage_root)

    manager, error = _construct(storage_root)
    if manager is not None:
        manager.close()

    assert (
        type(error),
        stat.S_IMODE(storage_root.stat().st_mode),
        _tree_snapshot(storage_root),
    ) == (ValueError, mode_before, tree_before)


@pytest.mark.unit
def test_existing_root_with_injected_wrong_owner_is_rejected_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected root UID metadata proves owner rejection without calling chown."""
    storage_root = tmp_path / "wrong-owner-root"
    storage_root.mkdir(mode=0o700)
    storage_root.chmod(0o700)
    sentinel = storage_root / "sentinel.txt"
    sentinel.write_bytes(b"owned sentinel")
    root_identity = storage_root.stat()
    tree_before = _tree_snapshot(storage_root)
    mode_before = stat.S_IMODE(root_identity.st_mode)
    original_fstat = asset_manager_module.os.fstat
    injected_calls = 0

    def wrong_owner_fstat(descriptor: int) -> Any:
        nonlocal injected_calls
        observed = original_fstat(descriptor)
        if (observed.st_dev, observed.st_ino) != (
            root_identity.st_dev,
            root_identity.st_ino,
        ):
            return observed
        injected_calls += 1
        return SimpleNamespace(
            st_mode=observed.st_mode,
            st_ino=observed.st_ino,
            st_dev=observed.st_dev,
            st_nlink=observed.st_nlink,
            st_uid=os.getuid() + 1,
            st_gid=observed.st_gid,
            st_size=observed.st_size,
            st_atime=observed.st_atime,
            st_mtime=observed.st_mtime,
            st_ctime=observed.st_ctime,
        )

    monkeypatch.setattr(asset_manager_module.os, "fstat", wrong_owner_fstat)
    manager, error = _construct(storage_root)
    if manager is not None:
        manager.close()

    diagnostics = {
        "injected_fstat_calls": injected_calls,
        "constructor_error": None if error is None else repr(error),
    }
    assert type(error) is ValueError, diagnostics
    assert stat.S_IMODE(storage_root.stat().st_mode) == mode_before, diagnostics
    assert _tree_snapshot(storage_root) == tree_before, diagnostics
