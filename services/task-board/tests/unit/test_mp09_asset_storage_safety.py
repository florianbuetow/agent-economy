"""T-126 acceptance proofs for generated artifact storage paths."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services import asset_manager as asset_manager_module
from task_board_service.services.asset_manager import AssetManager
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.fakes.native_task_fixture import NativeTaskFixture

_TASK_ID = "t-00000000-0000-4000-8000-000000000901"
_TERMS = TaskTerms(100, 3600, 3600, 3600, 3600)
_MAX_FILE_SIZE_BYTES = 32
_MAX_FILES_PER_TASK = 2


@contextmanager
def _accepted_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[NativeTaskFixture]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, _TERMS)
        scenario.bid(_TASK_ID, 90)
        scenario.accept(_TASK_ID, 90)
        task = scenario.task(_TASK_ID)
        assert task["status"] == "accepted"
        assert task["worker_id"] == scenario.worker_id
        yield scenario


def _manager(scenario: NativeTaskFixture, storage_root: Path) -> AssetManager:
    token_validator = AsyncMock()
    token_validator.validate_jws_token = AsyncMock(
        return_value={
            "action": "upload_asset",
            "task_id": _TASK_ID,
            "_signer_id": scenario.worker_id,
        }
    )
    deadline_evaluator = AsyncMock()
    deadline_evaluator.evaluate_deadline = AsyncMock(side_effect=lambda task: task)
    return AssetManager(
        store=scenario.store,
        token_validator=token_validator,
        deadline_evaluator=deadline_evaluator,
        asset_storage_path=str(storage_root),
        max_file_size=_MAX_FILE_SIZE_BYTES,
        max_files_per_task=_MAX_FILES_PER_TASK,
    )


def _assert_exact_display_filename(
    scenario: NativeTaskFixture,
    result: dict[str, object],
    display_filename: str,
) -> None:
    assert result["filename"] == display_filename
    persisted = scenario.store.get_asset(str(result["asset_id"]), _TASK_ID)
    assert persisted is not None
    assert persisted["filename"] == display_filename


def _fixed_uuid(monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    value = uuid.UUID("00000000-0000-4000-8000-000000000901")
    monkeypatch.setattr(asset_manager_module.uuid, "uuid4", lambda: value)
    return value


def _close_manager_if_available(manager: AssetManager) -> None:
    close = getattr(manager, "close", None)
    if callable(close):
        close()


async def _attempt_upload(
    manager: AssetManager,
    display_filename: str,
    content: bytes,
) -> tuple[dict[str, Any] | None, ServiceError | None]:
    try:
        return (
            await manager.upload_asset(
                _TASK_ID,
                "token",
                content,
                display_filename,
                "text/plain",
            ),
            None,
        )
    except ServiceError as error:
        return None, error


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _generated_files_within(root: Path) -> list[Path]:
    canonical_root = root.resolve()
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path.resolve().is_relative_to(canonical_root)
    ]


async def _assert_safe_success_round_trip(
    manager: AssetManager,
    scenario: NativeTaskFixture,
    storage_root: Path,
    result: dict[str, Any],
    display_filename: str,
    content: bytes,
) -> None:
    _assert_exact_display_filename(scenario, result, display_filename)
    assert result["content_hash"] == hashlib.sha256(content).hexdigest()
    downloaded, content_type, downloaded_filename = await manager.download_asset(
        _TASK_ID,
        str(result["asset_id"]),
    )
    assert downloaded == content
    assert content_type == "text/plain"
    assert downloaded_filename == display_filename

    generated_files = _generated_files_within(storage_root)
    assert len(generated_files) == 1
    assert generated_files[0].name != display_filename
    assert generated_files[0].read_bytes() == content


@pytest.mark.unit
async def test_absolute_display_filename_cannot_write_outside_owned_storage_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absolute display filename never becomes the physical object path."""
    storage_root = tmp_path / "storage"
    owned_outside = tmp_path / "owned-outside"
    owned_outside.mkdir()
    escaped_path = owned_outside / "escaped.txt"
    display_filename = str(escaped_path)

    with _accepted_task(tmp_path, monkeypatch) as scenario:
        manager = _manager(scenario, storage_root)
        _result, error = await _attempt_upload(manager, display_filename, b"safe text")
        stored_files = [path for path in storage_root.rglob("*") if path.is_file()]

        assert (
            escaped_path.exists(),
            scenario.store.count_assets(_TASK_ID),
            stored_files,
            None if error is None else error.error,
        ) == (False, 0, [], "invalid_filename"), (
            "absolute display filename must be rejected before any byte or metadata effect"
        )


@pytest.mark.unit
async def test_parent_traversal_display_filename_cannot_escape_generated_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent traversal is rejected before byte or metadata publication."""
    storage_root = tmp_path / "storage"
    owned_outside = tmp_path / "owned-outside"
    owned_outside.mkdir()
    escaped_path = owned_outside / "traversed.txt"
    display_filename = "../../../owned-outside/traversed.txt"

    with _accepted_task(tmp_path, monkeypatch) as scenario:
        manager = _manager(scenario, storage_root)
        _result, error = await _attempt_upload(manager, display_filename, b"safe text")
        stored_files = [path for path in storage_root.rglob("*") if path.is_file()]

        assert (
            escaped_path.exists(),
            scenario.store.count_assets(_TASK_ID),
            stored_files,
            None if error is None else error.error,
        ) == (False, 0, [], "invalid_filename"), (
            "traversal display filename must be rejected before any byte or metadata effect"
        )


@pytest.mark.unit
async def test_symlinked_asset_directory_cannot_redirect_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generated asset-directory symlink cannot redirect physical bytes."""
    storage_root = tmp_path / "storage"
    owned_outside = tmp_path / "owned-asset-target"
    owned_outside.mkdir()
    display_filename = "answer.txt"

    with _accepted_task(tmp_path, monkeypatch) as scenario:
        fixed_uuid = _fixed_uuid(monkeypatch)
        manager = _manager(scenario, storage_root)
        task_directory = storage_root / _TASK_ID
        task_directory.mkdir()
        generated_asset_directory = task_directory / f"asset-{fixed_uuid}"
        generated_asset_directory.symlink_to(owned_outside, target_is_directory=True)

        before_outside = _file_snapshot(owned_outside)
        result, error = await _attempt_upload(manager, display_filename, b"safe text")

        assert _file_snapshot(owned_outside) == before_outside, (
            "asset-directory symlink redirected upload outside the configured root"
        )
        if error is not None:
            assert error.error in {"artifact_destination_collision", "storage_unavailable"}
            assert scenario.store.count_assets(_TASK_ID) == 0
            assert _generated_files_within(storage_root) == []
            assert result is None
        else:
            assert result is not None
            await _assert_safe_success_round_trip(
                manager,
                scenario,
                storage_root,
                result,
                display_filename,
                b"safe text",
            )


@pytest.mark.unit
async def test_symlinked_task_directory_cannot_redirect_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task-directory symlink cannot redirect a generated object."""
    storage_root = tmp_path / "storage"
    owned_outside = tmp_path / "owned-task-target"
    owned_outside.mkdir()
    display_filename = "answer.txt"

    with _accepted_task(tmp_path, monkeypatch) as scenario:
        manager = _manager(scenario, storage_root)
        (storage_root / _TASK_ID).symlink_to(owned_outside, target_is_directory=True)

        before_outside = _file_snapshot(owned_outside)
        result, error = await _attempt_upload(manager, display_filename, b"safe text")

        assert _file_snapshot(owned_outside) == before_outside, (
            "task-directory symlink redirected generated asset bytes outside the configured root"
        )
        if error is not None:
            assert error.error in {"artifact_destination_collision", "storage_unavailable"}
            assert scenario.store.count_assets(_TASK_ID) == 0
            assert _generated_files_within(storage_root) == []
            assert result is None
        else:
            assert result is not None
            await _assert_safe_success_round_trip(
                manager,
                scenario,
                storage_root,
                result,
                display_filename,
                b"safe text",
            )


@pytest.mark.unit
async def test_retargeted_storage_symlink_cannot_change_pinned_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Storage uses the root resolved at initialization, despite later retargeting."""
    initial_root = tmp_path / "owned-initial-root"
    replacement_root = tmp_path / "owned-replacement-root"
    initial_root.mkdir(mode=0o700)
    replacement_root.mkdir(mode=0o700)
    initial_root.chmod(0o700)
    replacement_root.chmod(0o700)
    assert stat.S_IMODE(initial_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(replacement_root.stat().st_mode) == 0o700
    storage_link = tmp_path / "storage-link"
    storage_link.symlink_to(initial_root, target_is_directory=True)
    display_filename = "answer.txt"

    with _accepted_task(tmp_path, monkeypatch) as scenario:
        manager = _manager(scenario, storage_link)
        storage_link.unlink()
        storage_link.symlink_to(replacement_root, target_is_directory=True)

        result, error = await _attempt_upload(manager, display_filename, b"safe text")

        assert error is None
        assert result is not None
        assert _file_snapshot(replacement_root) == {}, (
            "retargeted configured-root symlink changed the physical storage root"
        )
        await _assert_safe_success_round_trip(
            manager,
            scenario,
            initial_root,
            result,
            display_filename,
            b"safe text",
        )


@pytest.mark.unit
async def test_renamed_initialized_root_keeps_original_inode_after_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pinned root survives rename and replacement of its configured path."""
    configured_root = tmp_path / "configured-root"
    renamed_original_root = tmp_path / "renamed-original-root"
    replacement_root = tmp_path / "owned-replacement-root"
    configured_root.mkdir(mode=0o700)
    replacement_root.mkdir(mode=0o700)
    configured_root.chmod(0o700)
    replacement_root.chmod(0o700)
    assert stat.S_IMODE(configured_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(replacement_root.stat().st_mode) == 0o700
    display_filename = "answer.txt"

    with _accepted_task(tmp_path, monkeypatch) as scenario:
        manager = _manager(scenario, configured_root)
        configured_root.rename(renamed_original_root)
        configured_root.symlink_to(replacement_root, target_is_directory=True)

        result, error = await _attempt_upload(manager, display_filename, b"safe text")

        assert error is None
        assert result is not None
        assert _file_snapshot(replacement_root) == {}, (
            "replaced configured-root path redirected bytes away from the initialized inode"
        )
        await _assert_safe_success_round_trip(
            manager,
            scenario,
            renamed_original_root,
            result,
            display_filename,
            b"safe text",
        )


@pytest.mark.unit
async def test_unknown_existing_generated_destination_is_never_clobbered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown regular destination remains exact and gains no metadata."""
    storage_root = tmp_path / "storage"
    existing_bytes = b"unknown existing bytes"
    upload_bytes = b"new text"
    display_filename = "answer.txt"

    with _accepted_task(tmp_path, monkeypatch) as scenario:
        fixed_uuid = _fixed_uuid(monkeypatch)
        manager = _manager(scenario, storage_root)
        agent_key = hashlib.sha256(scenario.worker_id.encode("utf-8")).hexdigest()[:32]
        contract_destination = (
            storage_root / "live" / agent_key / fixed_uuid.hex[:2] / f"{fixed_uuid}.blob"
        )
        contract_destination.parent.mkdir(mode=0o700, parents=True)
        contract_destination.write_bytes(existing_bytes)
        legacy_destination = storage_root / _TASK_ID / f"asset-{fixed_uuid}" / display_filename
        legacy_destination.parent.mkdir(parents=True)
        os.link(contract_destination, legacy_destination)
        assert contract_destination.stat().st_ino == legacy_destination.stat().st_ino

        try:
            result, error = await _attempt_upload(manager, display_filename, upload_bytes)

            assert (
                contract_destination.read_bytes(),
                legacy_destination.read_bytes(),
                scenario.store.count_assets(_TASK_ID),
                result,
                None if error is None else error.error,
            ) == (
                existing_bytes,
                existing_bytes,
                0,
                None,
                "artifact_destination_collision",
            ), "upload clobbered or claimed an unknown existing destination"
        finally:
            _close_manager_if_available(manager)


@pytest.mark.unit
async def test_close_is_idempotent_and_closed_manager_never_reopens_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close is explicit, idempotent and cannot affect a reused descriptor."""
    storage_root = tmp_path / "storage"
    unrelated_path = tmp_path / "unrelated-descriptor.txt"
    unrelated_path.write_bytes(b"unrelated descriptor bytes")
    unrelated_descriptor: int | None = None

    with _accepted_task(tmp_path, monkeypatch) as scenario:
        manager = _manager(scenario, storage_root)
        storage_before = _file_snapshot(storage_root)
        close = getattr(manager, "close", None)
        try:
            assert callable(close), "AssetManager.close lifecycle is missing"
            close()
            unrelated_descriptor = os.open(unrelated_path, os.O_RDWR)
            close()

            os.lseek(unrelated_descriptor, 0, os.SEEK_SET)
            assert os.read(unrelated_descriptor, 64) == b"unrelated descriptor bytes"

            upload_result, upload_error = await _attempt_upload(
                manager,
                "answer.txt",
                b"safe text",
            )
            download_error: ServiceError | None = None
            try:
                await manager.download_asset(_TASK_ID, "asset-after-close")
            except ServiceError as caught:
                download_error = caught

            assert upload_result is None
            assert upload_error is not None
            assert upload_error.error == "service_not_ready"
            assert download_error is not None
            assert download_error.error == "service_not_ready"
            assert scenario.store.count_assets(_TASK_ID) == 0
            assert _file_snapshot(storage_root) == storage_before
        finally:
            if unrelated_descriptor is not None:
                os.close(unrelated_descriptor)
            _close_manager_if_available(manager)


@pytest.mark.unit
async def test_string_prefix_sibling_cannot_be_downloaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sibling whose name starts with the root string is outside the root."""
    storage_root = tmp_path / "storage"
    owned_prefix_sibling = tmp_path / "storage-shadow"
    owned_prefix_sibling.mkdir()
    secret = b"owned sibling secret"
    secret_path = owned_prefix_sibling / "secret.txt"
    secret_path.write_bytes(secret)
    display_filename = "../../../storage-shadow/secret.txt"

    with _accepted_task(tmp_path, monkeypatch) as scenario:
        manager = _manager(scenario, storage_root)
        scenario.store.insert_asset(
            {
                "asset_id": "asset-prefix-proof",
                "task_id": _TASK_ID,
                "uploader_id": scenario.worker_id,
                "filename": display_filename,
                "content_type": "text/plain",
                "size_bytes": len(secret),
                "storage_path": f"{_TASK_ID}/asset-prefix-proof/{display_filename}",
                "content_hash": hashlib.sha256(secret).hexdigest(),
                "uploaded_at": scenario.clock.iso(),
            },
            expected_lifecycle_version=1,
            max_files_per_task=10,
        )

        metadata_before = scenario.store.get_asset("asset-prefix-proof", _TASK_ID)
        assert metadata_before is not None
        secret_before = secret_path.read_bytes()
        returned: tuple[bytes, str, str] | None = None
        error: ServiceError | None = None
        try:
            returned = await manager.download_asset(_TASK_ID, "asset-prefix-proof")
        except ServiceError as caught:
            error = caught

        assert secret_path.read_bytes() == secret_before
        assert scenario.store.get_asset("asset-prefix-proof", _TASK_ID) == metadata_before
        assert (returned, None if error is None else error.error) == (None, "asset_not_found"), (
            "string-prefix containment admitted bytes from an outside sibling"
        )
