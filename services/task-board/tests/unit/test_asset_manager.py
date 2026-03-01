"""Unit tests for AssetManager."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.task_store import TaskStore

if TYPE_CHECKING:
    from pathlib import Path


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _task_data(
    task_id: str,
    status: str,
    worker_id: str | None,
) -> dict[str, object]:
    created_at = _timestamp()
    return {
        "task_id": task_id,
        "poster_id": "a-poster",
        "title": "Task",
        "spec": "Spec",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 3600,
        "review_deadline_seconds": 3600,
        "status": status,
        "escrow_id": "esc-1",
        "bid_count": 1,
        "worker_id": worker_id,
        "accepted_bid_id": "bid-1",
        "created_at": created_at,
        "accepted_at": created_at,
        "submitted_at": None,
        "approved_at": None,
        "cancelled_at": None,
        "disputed_at": None,
        "dispute_reason": None,
        "ruling_id": None,
        "ruled_at": None,
        "worker_pct": None,
        "ruling_summary": None,
        "expired_at": None,
        "escrow_pending": 0,
    }


def _make_manager(
    store: TaskStore,
    storage_path: Path,
    token_payload: dict[str, object],
) -> tuple[AssetManager, AsyncMock, AsyncMock]:
    token_validator = AsyncMock()
    token_validator.validate_jws_token = AsyncMock(return_value=token_payload)
    deadline_evaluator = AsyncMock()
    deadline_evaluator.evaluate_deadline = AsyncMock(side_effect=lambda task: task)
    manager = AssetManager(
        store=store,
        token_validator=token_validator,
        deadline_evaluator=deadline_evaluator,
        asset_storage_path=str(storage_path),
        max_file_size=10,
        max_files_per_task=2,
    )
    return manager, token_validator, deadline_evaluator


@pytest.mark.unit
async def test_upload_asset_success(tmp_path) -> None:
    """upload_asset writes file, persists metadata, and returns SHA256 hash."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "accepted", "a-worker"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    content = b"hello"
    result = await manager.upload_asset("t-1", "token", content, "file.txt", "text/plain")

    expected_hash = hashlib.sha256(content).hexdigest()
    assert result["task_id"] == "t-1"
    assert result["uploader_id"] == "a-worker"
    assert result["filename"] == "file.txt"
    assert result["content_hash"] == expected_hash

    file_path = tmp_path / "assets" / "t-1" / result["asset_id"] / "file.txt"
    assert file_path.exists()
    assert file_path.read_bytes() == content
    store.close()


@pytest.mark.unit
async def test_upload_asset_file_too_large(tmp_path) -> None:
    """upload_asset rejects content over max_file_size."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "accepted", "a-worker"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    with pytest.raises(ServiceError) as exc_info:
        await manager.upload_asset(
            "t-1",
            "token",
            b"01234567890",
            "big.bin",
            "application/octet-stream",
        )

    assert exc_info.value.error == "FILE_TOO_LARGE"
    assert exc_info.value.status_code == 413
    store.close()


@pytest.mark.unit
async def test_upload_asset_too_many_files(tmp_path) -> None:
    """upload_asset rejects when max files per task is reached."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "accepted", "a-worker"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    await manager.upload_asset("t-1", "token", b"a", "one.txt", "text/plain")
    await manager.upload_asset("t-1", "token", b"b", "two.txt", "text/plain")

    with pytest.raises(ServiceError) as exc_info:
        await manager.upload_asset("t-1", "token", b"c", "three.txt", "text/plain")

    assert exc_info.value.error == "TOO_MANY_ASSETS"
    assert exc_info.value.status_code == 409
    store.close()


@pytest.mark.unit
async def test_upload_asset_task_not_found(tmp_path) -> None:
    """upload_asset returns TASK_NOT_FOUND for unknown task."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    with pytest.raises(ServiceError) as exc_info:
        await manager.upload_asset("t-1", "token", b"a", "one.txt", "text/plain")

    assert exc_info.value.error == "TASK_NOT_FOUND"
    assert exc_info.value.status_code == 404
    store.close()


@pytest.mark.unit
async def test_upload_asset_wrong_status(tmp_path) -> None:
    """upload_asset returns INVALID_STATUS when task is not accepted."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "open", "a-worker"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    with pytest.raises(ServiceError) as exc_info:
        await manager.upload_asset("t-1", "token", b"a", "one.txt", "text/plain")

    assert exc_info.value.error == "INVALID_STATUS"
    store.close()


@pytest.mark.unit
async def test_upload_asset_wrong_worker(tmp_path) -> None:
    """upload_asset returns FORBIDDEN when signer is not assigned worker."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "accepted", "a-worker"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-other"},
    )

    with pytest.raises(ServiceError) as exc_info:
        await manager.upload_asset("t-1", "token", b"a", "one.txt", "text/plain")

    assert exc_info.value.error == "FORBIDDEN"
    assert exc_info.value.status_code == 403
    store.close()


@pytest.mark.unit
async def test_list_assets_success(tmp_path) -> None:
    """list_assets returns task_id and assets list."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "accepted", "a-worker"))
    store.insert_asset(
        {
            "asset_id": "asset-1",
            "task_id": "t-1",
            "uploader_id": "a-worker",
            "filename": "result.txt",
            "content_type": "text/plain",
            "size_bytes": 5,
            "content_hash": "abc",
            "uploaded_at": _timestamp(),
        }
    )
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    result = await manager.list_assets("t-1")

    assert result["task_id"] == "t-1"
    assert len(result["assets"]) == 1
    assert result["assets"][0]["asset_id"] == "asset-1"
    store.close()


@pytest.mark.unit
async def test_list_assets_task_not_found(tmp_path) -> None:
    """list_assets returns TASK_NOT_FOUND for unknown task."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    with pytest.raises(ServiceError) as exc_info:
        await manager.list_assets("t-1")

    assert exc_info.value.error == "TASK_NOT_FOUND"
    assert exc_info.value.status_code == 404
    store.close()


@pytest.mark.unit
async def test_download_asset_success(tmp_path) -> None:
    """download_asset returns content, content_type, and filename."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "accepted", "a-worker"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )
    upload_result = await manager.upload_asset("t-1", "token", b"hello", "file.txt", "text/plain")

    content, content_type, filename = await manager.download_asset("t-1", upload_result["asset_id"])

    assert content == b"hello"
    assert content_type == "text/plain"
    assert filename == "file.txt"
    store.close()


@pytest.mark.unit
async def test_download_asset_task_not_found(tmp_path) -> None:
    """download_asset returns TASK_NOT_FOUND for unknown task."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    with pytest.raises(ServiceError) as exc_info:
        await manager.download_asset("t-1", "asset-1")

    assert exc_info.value.error == "TASK_NOT_FOUND"
    assert exc_info.value.status_code == 404
    store.close()


@pytest.mark.unit
async def test_download_asset_not_found(tmp_path) -> None:
    """download_asset returns ASSET_NOT_FOUND for unknown asset."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "accepted", "a-worker"))
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    with pytest.raises(ServiceError) as exc_info:
        await manager.download_asset("t-1", "asset-1")

    assert exc_info.value.error == "ASSET_NOT_FOUND"
    assert exc_info.value.status_code == 404
    store.close()


@pytest.mark.unit
async def test_download_asset_path_traversal(tmp_path) -> None:
    """download_asset blocks path traversal via stored filename."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "accepted", "a-worker"))
    store.insert_asset(
        {
            "asset_id": "asset-1",
            "task_id": "t-1",
            "uploader_id": "a-worker",
            "filename": "../../../../outside.txt",
            "content_type": "text/plain",
            "size_bytes": 5,
            "content_hash": "abc",
            "uploaded_at": _timestamp(),
        }
    )
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    with pytest.raises(ServiceError) as exc_info:
        await manager.download_asset("t-1", "asset-1")

    assert exc_info.value.error == "ASSET_NOT_FOUND"
    assert exc_info.value.status_code == 404
    store.close()


@pytest.mark.unit
def test_count_assets_delegates(tmp_path) -> None:
    """count_assets returns TaskStore count."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "accepted", "a-worker"))
    store.insert_asset(
        {
            "asset_id": "asset-1",
            "task_id": "t-1",
            "uploader_id": "a-worker",
            "filename": "result.txt",
            "content_type": "text/plain",
            "size_bytes": 5,
            "content_hash": "abc",
            "uploaded_at": _timestamp(),
        }
    )
    manager, _token_validator, _deadline_evaluator = _make_manager(
        store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": "t-1", "_signer_id": "a-worker"},
    )

    result = manager.count_assets("t-1")

    assert result == 1
    store.close()
