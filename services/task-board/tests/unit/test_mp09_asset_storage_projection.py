"""T-126 authoritative storage-key projection acceptance proof."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from pathlib import Path

_TASK_ID = "t-00000000-0000-4000-8000-000000000902"
_ASSET_ID = "asset-00000000-0000-4000-8000-000000000902"
_ARTIFACT_UUID = "00000000-0000-4000-8000-000000000902"
_STORAGE_PATH = f"live/{_ARTIFACT_UUID[:2]}/{_ARTIFACT_UUID}.blob"
_FILENAME = "answer.txt"
_CONTENT = b"authoritative storage projection"
_CONTENT_HASH = hashlib.sha256(_CONTENT).hexdigest()
_TERMS = TaskTerms(100, 3600, 3600, 3600, 3600)


@pytest.mark.unit
def test_get_asset_preserves_authoritative_generated_storage_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TaskDbClient returns the exact generated key persisted by Gateway V5."""
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, _TERMS)
        scenario.bid(_TASK_ID, 90)
        scenario.accept(_TASK_ID, 90)
        metadata = {
            "asset_id": _ASSET_ID,
            "task_id": _TASK_ID,
            "uploader_id": scenario.worker_id,
            "filename": _FILENAME,
            "content_type": "text/plain",
            "size_bytes": len(_CONTENT),
            "storage_path": _STORAGE_PATH,
            "content_hash": _CONTENT_HASH,
            "uploaded_at": scenario.clock.iso(),
        }
        scenario.store.insert_asset(metadata, expected_lifecycle_version=1, max_files_per_task=10)

        producer_row = scenario.store.reader.get_asset(_ASSET_ID, _TASK_ID)
        assert producer_row is not None
        assert producer_row["storage_path"] == _STORAGE_PATH

        projected = scenario.store.get_asset(_ASSET_ID, _TASK_ID)
        assert projected == metadata
