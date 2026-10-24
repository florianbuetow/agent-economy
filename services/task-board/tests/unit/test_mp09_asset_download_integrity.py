"""T-126 proofs that corrupted stored bytes are never returned as valid evidence."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.asset_manager import AssetManager
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.fakes.native_task_fixture import NativeTaskFixture

_TASK_ID = "t-00000000-0000-4000-8000-000000000919"
_TERMS = TaskTerms(100, 3600, 3600, 3600, 3600)
_ORIGINAL = b"exact approved evidence\n"


def _unchanged_task(task: dict[str, Any]) -> dict[str, Any]:
    return task


@contextmanager
def _accepted_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[NativeTaskFixture, AssetManager, Path]]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, _TERMS)
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
            max_file_size=1024,
            max_files_per_task=2,
        )
        try:
            yield scenario, manager, storage_root
        finally:
            manager.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    "stored_bytes",
    [b"X" + _ORIGINAL[1:], _ORIGINAL[:-1], _ORIGINAL + b"injected text"],
    ids=["same-size-wrong-hash", "truncated", "extended"],
)
async def test_corrupted_bytes_are_blocked_without_rewriting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored_bytes: bytes
) -> None:
    """Same-size tampering and size changes yield an explicit integrity conflict."""
    with _accepted_storage(tmp_path, monkeypatch) as (scenario, manager, storage_root):
        upload = await manager.upload_asset(
            _TASK_ID, "token", _ORIGINAL, "answer.txt", "text/plain"
        )
        asset_id = str(upload["asset_id"])
        metadata = scenario.store.get_asset(asset_id, _TASK_ID)
        assert metadata is not None
        assert metadata["size_bytes"] == len(_ORIGINAL)
        assert metadata["content_hash"] == hashlib.sha256(_ORIGINAL).hexdigest()
        stored_file = storage_root / str(metadata["storage_path"])
        assert stored_file.read_bytes() == _ORIGINAL
        stored_file.write_bytes(stored_bytes)
        sentinel = storage_root / "unrelated.txt"
        sentinel.write_bytes(b"unrelated owned sentinel")

        returned: tuple[bytes, str, str] | None = None
        failure: ServiceError | None = None
        try:
            returned = await manager.download_asset(_TASK_ID, asset_id)
        except ServiceError as error:
            failure = error

        assert stored_file.read_bytes() == stored_bytes
        assert sentinel.read_bytes() == b"unrelated owned sentinel"
        assert scenario.store.get_asset(asset_id, _TASK_ID) == metadata
        assert returned is None and failure is not None, (
            "Corrupted stored evidence was returned successfully instead of blocked",
            returned,
        )
        assert (failure.status_code, failure.error) == (409, "artifact_integrity_blocked")


@pytest.mark.unit
async def test_untouched_evidence_roundtrips_with_exact_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The integrity guard continues to serve the original valid artifact."""
    with _accepted_storage(tmp_path, monkeypatch) as (scenario, manager, _storage_root):
        upload = await manager.upload_asset(
            _TASK_ID, "token", _ORIGINAL, "answer.txt", "text/plain"
        )
        asset_id = str(upload["asset_id"])
        before = scenario.store.get_asset(asset_id, _TASK_ID)
        downloaded = await manager.download_asset(_TASK_ID, asset_id)
        assert downloaded == (_ORIGINAL, "text/plain", "answer.txt")
        assert hashlib.sha256(downloaded[0]).hexdigest() == upload["content_hash"]
        assert len(downloaded[0]) == upload["size_bytes"]
        assert scenario.store.get_asset(asset_id, _TASK_ID) == before
