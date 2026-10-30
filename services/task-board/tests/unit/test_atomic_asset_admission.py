"""Task Board forwards one frozen upload admission to the Gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.streaming_upload import (
    AssetStorageQuota,
    AssetUploadLimits,
    ParsedAssetUpload,
)
from task_board_service.services.task_db_client import TaskDbClient

if TYPE_CHECKING:
    from pathlib import Path


_TASK_ID = "t-00000000-0000-4000-8000-000000000990"


class _AdmissionStore:
    def __init__(self, *, lifecycle_version: int) -> None:
        self.lifecycle_version = lifecycle_version
        self.insert_calls: list[tuple[dict[str, Any], int, int]] = []

    def get_task(self, task_id: str) -> dict[str, Any]:
        assert task_id == _TASK_ID
        return {
            "task_id": task_id,
            "status": "accepted",
            "worker_id": "a-worker",
            "lifecycle_version": self.lifecycle_version,
        }

    def count_assets(self, task_id: str) -> int:
        assert task_id == _TASK_ID
        return 0

    def insert_asset(
        self,
        asset_data: dict[str, Any],
        *,
        expected_lifecycle_version: int,
        max_files_per_task: int,
    ) -> None:
        self.insert_calls.append((asset_data, expected_lifecycle_version, max_files_per_task))

    def get_asset(self, asset_id: str, task_id: str) -> dict[str, Any] | None:
        del asset_id, task_id
        return None


def _manager(store: Any, storage_root: Path) -> AssetManager:
    validator = AsyncMock()
    validator.validate_jws_token = AsyncMock(
        return_value={
            "action": "upload_asset",
            "task_id": _TASK_ID,
            "_signer_id": "a-worker",
        }
    )
    deadline = AsyncMock()

    def unchanged_task(task: dict[str, Any]) -> dict[str, Any]:
        return task

    deadline.evaluate_deadline = AsyncMock(side_effect=unchanged_task)
    return AssetManager(
        store=store,
        token_validator=validator,
        deadline_evaluator=deadline,
        asset_storage_path=str(storage_root),
        max_file_size=64,
        max_files_per_task=3,
    )


@pytest.mark.unit
async def test_direct_upload_forwards_initial_lifecycle_version(tmp_path: Path) -> None:
    """The version observed before bytes are written is the admission fence."""
    store = _AdmissionStore(lifecycle_version=7)
    manager = _manager(store, tmp_path / "assets")

    await manager.upload_asset(_TASK_ID, "token", b"answer", "answer.txt", "text/plain")

    assert len(store.insert_calls) == 1
    _asset, expected_version, quota = store.insert_calls[0]
    assert (expected_version, quota) == (7, 3)


@pytest.mark.unit
async def test_streamed_upload_keeps_pre_body_lifecycle_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streaming does not replace the initial fence with a post-body read."""
    store = _AdmissionStore(lifecycle_version=11)
    manager = _manager(store, tmp_path / "assets")

    async def parsed_upload(
        _request: object,
        _limits: AssetUploadLimits,
        _content_length: int | None,
        write_chunk: Any,
    ) -> ParsedAssetUpload:
        await write_chunk(b"streamed")
        store.lifecycle_version = 12
        return ParsedAssetUpload(
            filename="streamed.txt",
            content_type="text/plain",
            size_bytes=8,
            content_hash="6" * 64,
        )

    monkeypatch.setattr(
        "task_board_service.services.asset_manager.stream_asset_upload",
        parsed_upload,
    )
    request = type(
        "RequestStub",
        (),
        {"headers": {"content-type": "multipart/form-data; boundary=x"}},
    )()
    limits = AssetUploadLimits(
        max_file_size=64,
        max_request_size=128,
        max_multipart_overhead_bytes=32,
        stream_chunk_bytes=16,
        max_header_count=8,
        max_header_size=128,
        max_boundary_bytes=70,
        max_display_filename_utf8_bytes=255,
    )

    await manager.upload_streaming_asset(
        task_id=_TASK_ID,
        token="token",
        request=request,  # type: ignore[arg-type]
        limits=limits,
        quota=AssetStorageQuota(max_total_bytes_per_agent=1 << 20, max_total_bytes=1 << 30),
        idempotency_key=None,
    )

    assert len(store.insert_calls) == 1
    _asset, expected_version, quota = store.insert_calls[0]
    assert (expected_version, quota) == (11, 3)


@pytest.mark.unit
def test_db_client_sends_exact_required_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Gateway payload carries status, frozen version, and configured quota."""
    client = object.__new__(TaskDbClient)
    captured: dict[str, Any] = {}

    def request(
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        captured.update(method=method, path=path, json=json, params=params)
        return httpx.Response(201, json={})

    monkeypatch.setattr(client, "_request", request)
    client.insert_asset(
        {
            "asset_id": "asset-1",
            "task_id": _TASK_ID,
            "uploader_id": "a-worker",
            "filename": "answer.txt",
            "content_type": "text/plain",
            "size_bytes": 6,
            "storage_path": "live/aa/answer.blob",
            "content_hash": "a" * 64,
            "uploaded_at": "2026-10-23T10:00:00Z",
        },
        expected_lifecycle_version=13,
        max_files_per_task=4,
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/board/assets"
    assert captured["params"] is None
    assert captured["json"]["admission"] == {
        "expected_status": "accepted",
        "expected_lifecycle_version": 13,
        "max_files_per_task": 4,
    }


@pytest.mark.unit
async def test_definite_gateway_rejection_preserves_business_error_and_cleans_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typed rejection is exposed after cleanup without rewriting its details."""
    storage_root = tmp_path / "assets"
    client = object.__new__(TaskDbClient)

    def get_task(task_id: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "status": "accepted",
            "worker_id": "a-worker",
            "lifecycle_version": 17,
        }

    def count_assets(_task_id: str) -> int:
        return 0

    monkeypatch.setattr(
        client,
        "get_task",
        get_task,
    )
    monkeypatch.setattr(client, "count_assets", count_assets)

    def request(
        method: str,
        _path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        del json, params
        if method == "GET":
            return httpx.Response(404)
        return httpx.Response(
            409,
            json={
                "error": "task_version_conflict",
                "message": "Task lifecycle changed during upload",
                "details": {"expected_lifecycle_version": 17, "actual_lifecycle_version": 18},
            },
        )

    monkeypatch.setattr(client, "_request", request)
    manager = _manager(client, storage_root)

    with pytest.raises(ServiceError) as raised:
        await manager.upload_asset(
            _TASK_ID,
            "token",
            b"answer",
            "answer.txt",
            "text/plain",
        )

    assert raised.value.status_code == 409
    assert raised.value.error == "task_version_conflict"
    assert raised.value.message == "Task lifecycle changed during upload"
    assert raised.value.details == {
        "expected_lifecycle_version": 17,
        "actual_lifecycle_version": 18,
    }
    assert [path for path in storage_root.rglob("*") if path.is_file()] == []
