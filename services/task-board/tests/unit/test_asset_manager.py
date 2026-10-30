"""AssetManager behavior against explicit native Gateway task history."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.asset_manager import AssetManager
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.fakes.gateway_task_store import GatewayTaskStore
    from tests.fakes.native_task_fixture import NativeTaskFixture

_TERMS = TaskTerms(100, 3600, 3600, 3600, 3600)
_TASK_ID = "t-00000000-0000-4000-8000-000000000201"


@contextmanager
def _scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: Literal["missing", "open", "accepted"],
) -> Iterator[NativeTaskFixture]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        if state != "missing":
            scenario.open_task(_TASK_ID, _TERMS)
        if state == "accepted":
            scenario.bid(_TASK_ID, 90)
            scenario.accept(_TASK_ID, 90)
            task = scenario.task(_TASK_ID)
            assert task["status"] == "accepted"
            assert task["worker_id"] == scenario.worker_id
            assert task["accepted_reward"] == 100
            assert task["accepted_bid_amount"] == 90
        yield scenario


def _make_manager(
    store: GatewayTaskStore,
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


def _manager(scenario: NativeTaskFixture, tmp_path: Path, signer_id: str) -> AssetManager:
    manager, _validator, _deadline = _make_manager(
        scenario.store,
        tmp_path / "assets",
        {"action": "upload_asset", "task_id": _TASK_ID, "_signer_id": signer_id},
    )
    return manager


def _asset(scenario: NativeTaskFixture, filename: str = "result.txt") -> dict[str, object]:
    return {
        "asset_id": "asset-1",
        "task_id": _TASK_ID,
        "uploader_id": scenario.worker_id,
        "filename": filename,
        "content_type": "text/plain",
        "size_bytes": 5,
        "storage_path": f"{_TASK_ID}/asset-1/{filename}",
        "content_hash": "abc",
        "uploaded_at": scenario.clock.iso(),
    }


@pytest.mark.unit
async def test_upload_asset_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """upload_asset writes file, persists metadata, and returns SHA256 hash."""
    with _scenario(tmp_path, monkeypatch, "accepted") as scenario:
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        content = b"hello"
        result = await manager.upload_asset(_TASK_ID, "token", content, "file.txt", "text/plain")
        expected_hash = hashlib.sha256(content).hexdigest()
        assert result["task_id"] == _TASK_ID
        assert result["uploader_id"] == scenario.worker_id
        assert result["filename"] == "file.txt"
        assert result["content_hash"] == expected_hash
        persisted = scenario.store.get_asset(str(result["asset_id"]), _TASK_ID)
        assert persisted is not None
        assert persisted["content_hash"] == expected_hash
        assert persisted["size_bytes"] == len(content)
        storage_root = tmp_path / "assets"
        file_path = storage_root / str(persisted["storage_path"])
        assert file_path.resolve().is_relative_to(storage_root.resolve())
        assert file_path.name != result["filename"]
        assert file_path.exists()
        assert file_path.read_bytes() == content


@pytest.mark.unit
async def test_upload_asset_file_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """upload_asset rejects content over max_file_size."""
    with _scenario(tmp_path, monkeypatch, "accepted") as scenario:
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        with pytest.raises(ServiceError) as raised:
            await manager.upload_asset(
                _TASK_ID, "token", b"01234567890", "big.bin", "application/octet-stream"
            )
        assert raised.value.error == "file_too_large"
        assert raised.value.status_code == 413
        assert scenario.store.count_assets(_TASK_ID) == 0


@pytest.mark.unit
async def test_upload_asset_too_many_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """upload_asset rejects when max files per task is reached."""
    with _scenario(tmp_path, monkeypatch, "accepted") as scenario:
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        await manager.upload_asset(_TASK_ID, "token", b"a", "one.txt", "text/plain")
        await manager.upload_asset(_TASK_ID, "token", b"b", "two.txt", "text/plain")
        with pytest.raises(ServiceError) as raised:
            await manager.upload_asset(_TASK_ID, "token", b"c", "three.txt", "text/plain")
        assert raised.value.error == "too_many_assets"
        assert raised.value.status_code == 409
        assert scenario.store.count_assets(_TASK_ID) == 2


@pytest.mark.unit
async def test_upload_asset_task_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """upload_asset returns task_not_found for unknown task."""
    with _scenario(tmp_path, monkeypatch, "missing") as scenario:
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        with pytest.raises(ServiceError) as raised:
            await manager.upload_asset(_TASK_ID, "token", b"a", "one.txt", "text/plain")
        assert raised.value.error == "task_not_found"
        assert raised.value.status_code == 404


@pytest.mark.unit
async def test_upload_asset_wrong_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """upload_asset returns invalid_status when task is not accepted."""
    with _scenario(tmp_path, monkeypatch, "open") as scenario:
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        with pytest.raises(ServiceError) as raised:
            await manager.upload_asset(_TASK_ID, "token", b"a", "one.txt", "text/plain")
        assert raised.value.error == "invalid_status"
        assert scenario.store.count_assets(_TASK_ID) == 0


@pytest.mark.unit
async def test_upload_asset_wrong_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """upload_asset returns forbidden when signer is not assigned worker."""
    with _scenario(tmp_path, monkeypatch, "accepted") as scenario:
        manager = _manager(scenario, tmp_path, "a-other")
        with pytest.raises(ServiceError) as raised:
            await manager.upload_asset(_TASK_ID, "token", b"a", "one.txt", "text/plain")
        assert raised.value.error == "forbidden"
        assert raised.value.status_code == 403
        assert scenario.store.count_assets(_TASK_ID) == 0


@pytest.mark.unit
async def test_list_assets_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """list_assets returns task_id and persisted metadata."""
    with _scenario(tmp_path, monkeypatch, "accepted") as scenario:
        scenario.store.insert_asset(
            _asset(scenario), expected_lifecycle_version=1, max_files_per_task=2
        )
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        result = await manager.list_assets(_TASK_ID)
        assert result["task_id"] == _TASK_ID
        assert result["assets"] == [
            {
                "asset_id": "asset-1",
                "uploader_id": scenario.worker_id,
                "filename": "result.txt",
                "content_type": "text/plain",
                "size_bytes": 5,
                "content_hash": "abc",
                "uploaded_at": scenario.clock.iso(),
                "role": "output",
            }
        ]


@pytest.mark.unit
async def test_list_assets_task_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """list_assets returns task_not_found for unknown task."""
    with _scenario(tmp_path, monkeypatch, "missing") as scenario:
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        with pytest.raises(ServiceError) as raised:
            await manager.list_assets(_TASK_ID)
        assert raised.value.error == "task_not_found"
        assert raised.value.status_code == 404


@pytest.mark.unit
async def test_download_asset_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """download_asset returns content, content_type, and filename."""
    with _scenario(tmp_path, monkeypatch, "accepted") as scenario:
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        uploaded = await manager.upload_asset(_TASK_ID, "token", b"hello", "file.txt", "text/plain")
        content, content_type, filename = await manager.download_asset(
            _TASK_ID, str(uploaded["asset_id"])
        )
        assert content == b"hello"
        assert content_type == "text/plain"
        assert filename == "file.txt"


@pytest.mark.unit
async def test_download_asset_task_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """download_asset returns task_not_found for unknown task."""
    with _scenario(tmp_path, monkeypatch, "missing") as scenario:
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        with pytest.raises(ServiceError) as raised:
            await manager.download_asset(_TASK_ID, "asset-1")
        assert raised.value.error == "task_not_found"
        assert raised.value.status_code == 404


@pytest.mark.unit
async def test_download_asset_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """download_asset returns asset_not_found for unknown asset."""
    with _scenario(tmp_path, monkeypatch, "accepted") as scenario:
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        with pytest.raises(ServiceError) as raised:
            await manager.download_asset(_TASK_ID, "asset-1")
        assert raised.value.error == "asset_not_found"
        assert raised.value.status_code == 404


@pytest.mark.unit
async def test_download_asset_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """download_asset blocks path traversal via stored filename."""
    with _scenario(tmp_path, monkeypatch, "accepted") as scenario:
        scenario.store.insert_asset(
            _asset(scenario, "../../../../outside.txt"),
            expected_lifecycle_version=1,
            max_files_per_task=2,
        )
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        with pytest.raises(ServiceError) as raised:
            await manager.download_asset(_TASK_ID, "asset-1")
        assert raised.value.error == "asset_not_found"
        assert raised.value.status_code == 404


@pytest.mark.unit
def test_count_assets_delegates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """count_assets returns the persisted Gateway count."""
    with _scenario(tmp_path, monkeypatch, "accepted") as scenario:
        scenario.store.insert_asset(
            _asset(scenario), expected_lifecycle_version=1, max_files_per_task=2
        )
        manager = _manager(scenario, tmp_path, scenario.worker_id)
        assert manager.count_assets(_TASK_ID) == 1
