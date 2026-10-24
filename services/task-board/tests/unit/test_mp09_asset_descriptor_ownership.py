"""Post-implementation regression for pinned root descriptor ownership."""

from __future__ import annotations

import asyncio
import fcntl
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.asset_manager import AssetManager
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

_TASK_ID = "t-00000000-0000-4000-8000-000000000903"
_TERMS = TaskTerms(100, 3600, 3600, 3600, 3600)


class _PausedTokenValidator:
    def __init__(self, started: asyncio.Event, release: asyncio.Event, worker_id: str) -> None:
        self._started = started
        self._release = release
        self._worker_id = worker_id

    async def validate_jws_token(
        self,
        token: str,
        action: str,
        *,
        expected_resource: str,
    ) -> dict[str, Any]:
        assert token == "token"
        assert action == "upload_asset"
        assert expected_resource == f"POST /tasks/{_TASK_ID}/assets"
        self._started.set()
        await self._release.wait()
        return {
            "action": action,
            "task_id": _TASK_ID,
            "_signer_id": self._worker_id,
        }


def _directory_descriptors_for(path: Path) -> list[int]:
    expected = path.stat()
    matches: list[int] = []
    for entry in Path("/dev/fd").iterdir():
        if not entry.name.isdecimal():
            continue
        descriptor = int(entry.name)
        try:
            observed = os.fstat(descriptor)
        except OSError:
            continue
        if (
            stat.S_ISDIR(observed.st_mode)
            and observed.st_dev == expected.st_dev
            and observed.st_ino == expected.st_ino
        ):
            matches.append(descriptor)
    return sorted(matches)


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _reuse_descriptor_for_owned_directory(descriptor: int, directory: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    opened = os.open(directory, flags)
    if opened == descriptor:
        return opened
    try:
        duplicated = fcntl.fcntl(opened, fcntl.F_DUPFD_CLOEXEC, descriptor)
    finally:
        os.close(opened)
    if duplicated != descriptor:
        os.close(duplicated)
        pytest.fail(
            "SETUP FAILURE: could not reuse the released root descriptor with the "
            "test-owned replacement directory"
        )
    return duplicated


@pytest.mark.unit
async def test_close_during_token_validation_cannot_reuse_stale_root_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing before authorization prevents storage and metadata effects."""
    storage_root = tmp_path / "storage-root"
    replacement_root = tmp_path / "replacement-root"
    storage_root.mkdir(mode=0o700)
    replacement_root.mkdir(mode=0o700)
    storage_root.chmod(0o700)
    replacement_root.chmod(0o700)
    assert stat.S_IMODE(storage_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(replacement_root.stat().st_mode) == 0o700

    validation_started = asyncio.Event()
    release_validation = asyncio.Event()
    manager: AssetManager | None = None
    upload: asyncio.Task[dict[str, Any]] | None = None
    replacement_descriptor: int | None = None

    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, _TERMS)
        scenario.bid(_TASK_ID, 90)
        scenario.accept(_TASK_ID, 90)
        validator = _PausedTokenValidator(
            validation_started,
            release_validation,
            scenario.worker_id,
        )
        deadline_evaluator = AsyncMock()
        deadline_evaluator.evaluate_deadline = AsyncMock(side_effect=lambda task: task)
        manager = AssetManager(
            store=scenario.store,
            token_validator=validator,
            deadline_evaluator=deadline_evaluator,
            asset_storage_path=str(storage_root),
            max_file_size=32,
            max_files_per_task=2,
        )

        try:
            matching_descriptors = _directory_descriptors_for(storage_root)
            assert len(matching_descriptors) == 1, (
                "SETUP FAILURE: expected exactly one manager-owned descriptor for the root"
            )
            root_descriptor = matching_descriptors[0]
            root_identity = os.fstat(root_descriptor)
            expected_root_identity = storage_root.stat()
            assert (root_identity.st_dev, root_identity.st_ino) == (
                expected_root_identity.st_dev,
                expected_root_identity.st_ino,
            )

            upload = asyncio.create_task(
                manager.upload_asset(
                    _TASK_ID,
                    "token",
                    b"safe text",
                    "answer.txt",
                    "text/plain",
                )
            )
            await asyncio.wait_for(validation_started.wait(), timeout=2)
            assert not upload.done()

            manager.close()
            with pytest.raises(OSError):
                os.fstat(root_descriptor)
            replacement_descriptor = _reuse_descriptor_for_owned_directory(
                root_descriptor,
                replacement_root,
            )
            replacement_identity = os.fstat(replacement_descriptor)
            expected_replacement_identity = replacement_root.stat()
            assert (replacement_identity.st_dev, replacement_identity.st_ino) == (
                expected_replacement_identity.st_dev,
                expected_replacement_identity.st_ino,
            )

            release_validation.set()
            error: ServiceError | None = None
            result: dict[str, Any] | None = None
            try:
                result = await upload
            except ServiceError as caught:
                error = caught

            assert (
                result,
                None if error is None else error.error,
                scenario.store.count_assets(_TASK_ID),
                _file_snapshot(storage_root),
                _file_snapshot(replacement_root),
            ) == (
                None,
                "service_not_ready",
                0,
                {},
                {},
            ), "closed storage descriptor was reused after authorization resumed"
        finally:
            release_validation.set()
            if upload is not None and not upload.done():
                upload.cancel()
                with suppress(asyncio.CancelledError):
                    await upload
            if manager is not None:
                manager.close()
            if replacement_descriptor is not None:
                os.close(replacement_descriptor)
