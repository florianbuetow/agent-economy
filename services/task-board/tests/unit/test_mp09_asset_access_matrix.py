"""Public read cutoff matrix; atomic grants and Court authority are separate proofs."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.task_operations import complete_bank_operation
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fakes.native_task_fixture import NativeTaskFixture

_TASK_ID = "t-00000000-0000-4000-8000-000000000939"
_CONTENT = b"retain exact evidence after terminal transition\n"
_TERMINAL = frozenset({"approved", "ruled", "cancelled", "expired"})


def _unchanged_task(task: dict[str, Any]) -> dict[str, Any]:
    return task


def _manager(scenario: NativeTaskFixture, storage_root: Path) -> AssetManager:
    validator = AsyncMock()
    validator.validate_jws_token = AsyncMock(
        return_value={"task_id": _TASK_ID, "_signer_id": scenario.worker_id}
    )
    evaluator = AsyncMock()
    evaluator.evaluate_deadline = AsyncMock(side_effect=_unchanged_task)
    return AssetManager(
        store=scenario.store,
        token_validator=validator,
        deadline_evaluator=evaluator,
        asset_storage_path=str(storage_root),
        max_file_size=128,
        max_files_per_task=1,
    )


def _historical_poster_asset(scenario: NativeTaskFixture, storage_root: Path) -> str:
    """Explicit V5 row/file fixture, not a claim of native poster-input support."""
    asset_id = "asset-00000000-0000-4000-8000-000000000940"
    storage_key = "live/00/00000000-0000-4000-8000-000000000940.blob"
    (storage_root / "live").mkdir(mode=0o700)
    (storage_root / "live/00").mkdir(mode=0o700)
    (storage_root / storage_key).write_bytes(_CONTENT)
    scenario.store.writer.connection.execute(
        "INSERT INTO board_assets "
        "(asset_id,task_id,uploader_id,filename,content_type,size_bytes,storage_path,"
        "content_hash,uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            asset_id,
            _TASK_ID,
            scenario.poster_id,
            "answer.txt",
            "text/plain",
            len(_CONTENT),
            storage_key,
            hashlib.sha256(_CONTENT).hexdigest(),
            scenario.clock.iso(),
        ),
    )
    scenario.store.writer.connection.commit()
    return asset_id


async def _cancel(scenario: NativeTaskFixture) -> None:
    task = scenario.task(_TASK_ID)
    claimed = scenario.store.claim_task_operation(
        scenario.command(
            _TASK_ID,
            "cancel_task",
            "open",
            0,
            scenario.poster_id,
            {
                "poster_id": scenario.poster_id,
                "escrow_id": task["escrow_id"],
                "reward": task["reward"],
                "source_reference": f"task:{_TASK_ID}:cancelled",
                "cutoff_us": task["bidding_cutoff_us"],
            },
        )
    )
    settled = await complete_bank_operation(scenario.store, scenario.escrow, claimed)
    assert settled["operation_state"] == "completed"


async def _worker_status(scenario: NativeTaskFixture, status: str) -> None:
    if status == "accepted":
        return
    if status == "expired":
        scenario.clock.at_seconds(3600)
        expired = await scenario.evaluator(None).evaluate_deadline(scenario.task(_TASK_ID))
        assert expired["status"] == "expired"
        return
    submitted = scenario.store.claim_task_operation(
        scenario.command(
            _TASK_ID,
            "submit_deliverable",
            "accepted",
            1,
            scenario.worker_id,
            {"worker_id": scenario.worker_id},
        )
    )
    assert submitted["operation_state"] == "completed"
    if status == "submitted":
        return
    if status == "approved":
        await scenario.approve(_TASK_ID)
        return
    scenario.dispute(_TASK_ID, rebuttal_seconds=3600)
    if status == "disputed":
        return
    assert status == "ruled"
    ruling_id = f"clm-{_TASK_ID}"
    arguments = scenario.accepted_arguments(_TASK_ID)
    arguments.update(
        ruling_id=ruling_id,
        worker_pct=100,
        summary="All work accepted by the Court fixture",
        source_reference=f"task:{_TASK_ID}:ruling:{ruling_id}",
    )
    command = scenario.command(_TASK_ID, "record_ruling", "disputed", 3, "a-platform", arguments)
    command["actor_kind"] = "platform"
    claimed = scenario.store.claim_task_operation(command)
    settled = await complete_bank_operation(scenario.store, scenario.escrow, claimed)
    assert settled["operation_state"] == "completed"


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    ["open", "accepted", "submitted", "disputed", "approved", "ruled", "cancelled", "expired"],
)
async def test_public_bytes_follow_exact_status_matrix_without_deleting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """Native transitions plus labeled historical open/cancelled asset fixtures."""
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, TaskTerms(100, 3600, 3600, 3600, 3600))
        storage_root = tmp_path / "assets"
        manager = _manager(scenario, storage_root)
        try:
            if status in {"open", "cancelled"}:
                asset_id = _historical_poster_asset(scenario, storage_root)
                if status == "cancelled":
                    await _cancel(scenario)
            else:
                scenario.bid(_TASK_ID, 90)
                scenario.accept(_TASK_ID, 90)
                upload = await manager.upload_asset(
                    _TASK_ID, "token", _CONTENT, "answer.txt", "text/plain"
                )
                asset_id = str(upload["asset_id"])
                await _worker_status(scenario, status)
            task_before = scenario.task(_TASK_ID)
            assert task_before["status"] == status
            metadata = scenario.store.get_asset(asset_id, _TASK_ID)
            assert metadata is not None
            stored_file = storage_root / str(metadata["storage_path"])
            assert stored_file.read_bytes() == _CONTENT
            events_before = scenario.store.writer.get_total_events()
            result: tuple[bytes, str, str] | None = None
            failure: ServiceError | None = None
            try:
                result = await manager.download_asset(_TASK_ID, asset_id)
            except ServiceError as error:
                failure = error

            listing = await manager.list_assets(_TASK_ID)
            assert len(listing["assets"]) == 1
            assert listing["assets"][0]["asset_id"] == asset_id
            assert listing["assets"][0]["content_hash"] == hashlib.sha256(_CONTENT).hexdigest()
            assert scenario.store.get_asset(asset_id, _TASK_ID) == metadata
            assert scenario.task(_TASK_ID) == task_before
            assert scenario.store.writer.get_total_events() == events_before
            assert stored_file.read_bytes() == _CONTENT
            if status in _TERMINAL:
                assert result is None and failure is not None, (status, result)
                assert (failure.status_code, failure.error) == (410, "artifact_unavailable")
            else:
                assert failure is None
                assert result == (_CONTENT, "text/plain", "answer.txt")
        finally:
            manager.close()
