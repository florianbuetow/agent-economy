"""T-126 owner policy: completed jobs retain metadata but stop public downloads."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.asset_manager import AssetManager
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from pathlib import Path

_TASK_ID = "t-00000000-0000-4000-8000-000000000929"


def _unchanged_task(task: dict[str, Any]) -> dict[str, Any]:
    return task


@pytest.mark.unit
async def test_approved_job_keeps_evidence_but_stops_public_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real submission and approval stop bytes without deleting the evidence."""
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, TaskTerms(100, 3600, 3600, 3600, 3600))
        scenario.bid(_TASK_ID, 90)
        scenario.accept(_TASK_ID, 90)
        validator = AsyncMock()
        validator.validate_jws_token = AsyncMock(
            return_value={"task_id": _TASK_ID, "_signer_id": scenario.worker_id}
        )
        evaluator = AsyncMock()
        evaluator.evaluate_deadline = AsyncMock(side_effect=_unchanged_task)
        storage_root = tmp_path / "assets"
        manager = AssetManager(
            store=scenario.store,
            token_validator=validator,
            deadline_evaluator=evaluator,
            asset_storage_path=str(storage_root),
            max_file_size=64,
            max_files_per_task=1,
        )
        try:
            content = b"completed job evidence\n"
            uploaded = await manager.upload_asset(
                _TASK_ID, "token", content, "answer.txt", "text/plain"
            )
            asset_id = str(uploaded["asset_id"])
            metadata = scenario.store.get_asset(asset_id, _TASK_ID)
            assert metadata is not None
            assert metadata["content_hash"] == hashlib.sha256(content).hexdigest()
            stored_file = storage_root / str(metadata["storage_path"])
            assert await manager.download_asset(_TASK_ID, asset_id) == (
                content,
                "text/plain",
                "answer.txt",
            )

            submission = scenario.store.claim_task_operation(
                scenario.command(
                    _TASK_ID,
                    "submit_deliverable",
                    "accepted",
                    1,
                    scenario.worker_id,
                    {"worker_id": scenario.worker_id},
                )
            )
            assert submission["operation_state"] == "completed"
            await scenario.approve(_TASK_ID)
            task_before = scenario.task(_TASK_ID)
            assert task_before["status"] == "approved"
            assert stored_file.read_bytes() == content

            result: tuple[bytes, str, str] | None = None
            failure: ServiceError | None = None
            try:
                result = await manager.download_asset(_TASK_ID, asset_id)
            except ServiceError as error:
                failure = error

            listed = await manager.list_assets(_TASK_ID)
            assert len(listed["assets"]) == 1
            assert listed["assets"][0]["asset_id"] == asset_id
            assert listed["assets"][0]["content_hash"] == metadata["content_hash"]
            assert scenario.store.get_asset(asset_id, _TASK_ID) == metadata
            assert scenario.task(_TASK_ID) == task_before
            assert stored_file.read_bytes() == content
            assert result is None and failure is not None, (
                "An approved job still permits public evidence download",
                result,
            )
            assert (failure.status_code, failure.error) == (410, "artifact_unavailable")
        finally:
            manager.close()
