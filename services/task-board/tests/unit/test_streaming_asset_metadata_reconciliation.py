"""Focused publication cleanup and metadata-outcome reconciliation regressions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest

from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.errors import AssetInsertRejectedError
from task_board_service.services.task_db_client import TaskDbClient
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from pathlib import Path


_TASK_ID = "t-00000000-0000-4000-8000-000000000905"
_TERMS = TaskTerms(100, 3600, 3600, 3600, 3600)


def _stored_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


@pytest.mark.unit
@pytest.mark.parametrize(
    "outcome",
    ["definite-rejection", "ambiguous-absence", "committed-error", "committed-cancellation"],
)
async def test_metadata_error_deletes_only_definitely_uncommitted_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Definite rejection cleans bytes; a reconciled commit remains downloadable."""
    storage_root = tmp_path / "assets"
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, _TERMS)
        scenario.bid(_TASK_ID, 90)
        scenario.accept(_TASK_ID, 90)
        validator = AsyncMock()
        validator.validate_jws_token = AsyncMock(
            return_value={
                "action": "upload_asset",
                "task_id": _TASK_ID,
                "_signer_id": scenario.worker_id,
            }
        )
        deadline_evaluator = AsyncMock()
        deadline_evaluator.evaluate_deadline = AsyncMock(side_effect=lambda task: task)
        manager = AssetManager(
            store=scenario.store,
            token_validator=validator,
            deadline_evaluator=deadline_evaluator,
            asset_storage_path=str(storage_root),
            max_file_size=64,
            max_files_per_task=2,
        )
        original_insert = scenario.store.insert_asset

        def uncertain_insert(
            asset: dict[str, Any],
            *,
            expected_lifecycle_version: int,
            max_files_per_task: int,
        ) -> None:
            if outcome in {"committed-error", "committed-cancellation"}:
                original_insert(
                    asset,
                    expected_lifecycle_version=expected_lifecycle_version,
                    max_files_per_task=max_files_per_task,
                )
            if outcome == "definite-rejection":
                raise ValueError("simulated definite metadata rejection")
            if outcome == "committed-cancellation":
                raise asyncio.CancelledError
            raise RuntimeError("simulated uncertain metadata outcome")

        monkeypatch.setattr(scenario.store, "insert_asset", uncertain_insert)
        if outcome == "committed-error":
            result = await manager.upload_asset(
                _TASK_ID,
                "token",
                b"exact bytes",
                "evidence.txt",
                "text/plain",
            )
            downloaded, content_type, filename = await manager.download_asset(
                _TASK_ID,
                str(result["asset_id"]),
            )
            assert (downloaded, content_type, filename) == (
                b"exact bytes",
                "text/plain",
                "evidence.txt",
            )
            assert len(_stored_files(storage_root)) == 1
        elif outcome == "committed-cancellation":
            with pytest.raises(asyncio.CancelledError):
                await manager.upload_asset(
                    _TASK_ID,
                    "token",
                    b"exact bytes",
                    "evidence.txt",
                    "text/plain",
                )
            assert scenario.store.count_assets(_TASK_ID) == 1
            assert len(_stored_files(storage_root)) == 1
        elif outcome == "definite-rejection":
            with pytest.raises(ValueError, match="simulated definite metadata rejection"):
                await manager.upload_asset(
                    _TASK_ID,
                    "token",
                    b"exact bytes",
                    "evidence.txt",
                    "text/plain",
                )
            assert scenario.store.count_assets(_TASK_ID) == 0
            assert _stored_files(storage_root) == []
        else:
            with pytest.raises(RuntimeError, match="simulated uncertain metadata outcome"):
                await manager.upload_asset(
                    _TASK_ID,
                    "token",
                    b"exact bytes",
                    "evidence.txt",
                    "text/plain",
                )
            assert scenario.store.count_assets(_TASK_ID) == 0
            assert len(_stored_files(storage_root)) == 1


@pytest.mark.unit
@pytest.mark.parametrize("outcome", ["gateway-422", "timeout"])
async def test_real_gateway_insert_boundary_controls_byte_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """A terminal 4xx cleans owned bytes while transport ambiguity retains them."""
    storage_root = tmp_path / "gateway-assets"
    store = object.__new__(TaskDbClient)
    monkeypatch.setattr(
        store,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "status": "accepted",
            "worker_id": "worker",
            "lifecycle_version": 1,
        },
    )
    monkeypatch.setattr(store, "count_assets", lambda _task_id: 0)

    def gateway_request(
        method: str,
        _path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        del json, params
        if method == "GET":
            return httpx.Response(404)
        if outcome == "gateway-422":
            return httpx.Response(422, text="rejected")
        raise httpx.ReadTimeout("simulated ambiguous timeout")

    monkeypatch.setattr(store, "_request", gateway_request)
    validator = AsyncMock()
    validator.validate_jws_token = AsyncMock(
        return_value={"action": "upload_asset", "task_id": _TASK_ID, "_signer_id": "worker"}
    )
    deadline_evaluator = AsyncMock()
    deadline_evaluator.evaluate_deadline = AsyncMock(side_effect=lambda task: task)
    manager = AssetManager(
        store=store,
        token_validator=validator,
        deadline_evaluator=deadline_evaluator,
        asset_storage_path=str(storage_root),
        max_file_size=64,
        max_files_per_task=2,
    )

    expected_error = AssetInsertRejectedError if outcome == "gateway-422" else httpx.ReadTimeout
    with pytest.raises(expected_error):
        await manager.upload_asset(
            _TASK_ID,
            "token",
            b"exact bytes",
            "evidence.txt",
            "text/plain",
        )

    expected_file_count = 0 if outcome == "gateway-422" else 1
    assert len(_stored_files(storage_root)) == expected_file_count
