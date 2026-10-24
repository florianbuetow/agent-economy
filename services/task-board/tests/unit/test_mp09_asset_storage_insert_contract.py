"""Regression contract for authoritative asset storage-key insertion."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import pytest

from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from pathlib import Path

_TASK_ID = "t-00000000-0000-4000-8000-000000000904"
_TERMS = TaskTerms(100, 3600, 3600, 3600, 3600)
_CONTENT = b"authoritative insert contract"
_CONTENT_HASH = hashlib.sha256(_CONTENT).hexdigest()
_VALID_STORAGE_PATH = "live/00/00000000-0000-4000-8000-000000000904.blob"
_VALIDATION_MESSAGE = "storage_path must be a non-blank string"


def _asset(scenario: Any, asset_id: str) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "task_id": _TASK_ID,
        "uploader_id": scenario.worker_id,
        "filename": "answer.txt",
        "content_type": "text/plain",
        "size_bytes": len(_CONTENT),
        "storage_path": _VALID_STORAGE_PATH,
        "content_hash": _CONTENT_HASH,
        "uploaded_at": scenario.clock.iso(),
    }


def _authoritative_rows(scenario: Any) -> tuple[list[dict[str, Any]], list[tuple[Any, ...]]]:
    assets = scenario.store.reader.get_assets_for_task(_TASK_ID)
    events = scenario.store.writer.connection.execute(
        "SELECT event_id,event_source,event_type,timestamp,task_id,agent_id,summary,payload "
        "FROM events ORDER BY event_id"
    ).fetchall()
    return assets, [tuple(row) for row in events]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "storage_path"),
    [
        pytest.param("missing", None, id="missing"),
        pytest.param("none", None, id="none"),
        pytest.param("empty", "", id="empty"),
        pytest.param("whitespace", "   ", id="whitespace"),
    ],
)
def test_insert_asset_rejects_absent_or_blank_storage_path_before_gateway_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    storage_path: object,
) -> None:
    """Invalid producer metadata changes neither asset nor event rows."""
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, _TERMS)
        scenario.bid(_TASK_ID, 90)
        scenario.accept(_TASK_ID, 90)
        metadata = _asset(scenario, f"asset-{case}")
        if case == "missing":
            metadata.pop("storage_path")
        else:
            metadata["storage_path"] = storage_path
        before = _authoritative_rows(scenario)
        error: ValueError | None = None

        try:
            scenario.store.insert_asset(
                metadata, expected_lifecycle_version=1, max_files_per_task=10
            )
        except ValueError as caught:
            error = caught

        assert (
            None if error is None else str(error),
            _authoritative_rows(scenario),
        ) == (
            _VALIDATION_MESSAGE,
            before,
        ), "invalid storage_path reached the real Gateway metadata/event writer"


@pytest.mark.unit
def test_insert_asset_preserves_legitimate_explicit_relative_storage_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid explicit relative storage key reaches Gateway unchanged."""
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.open_task(_TASK_ID, _TERMS)
        scenario.bid(_TASK_ID, 90)
        scenario.accept(_TASK_ID, 90)
        metadata = _asset(scenario, "asset-valid")
        before_assets, before_events = _authoritative_rows(scenario)

        scenario.store.insert_asset(metadata, expected_lifecycle_version=1, max_files_per_task=10)

        after_assets, after_events = _authoritative_rows(scenario)
        assert before_assets == []
        assert after_assets == [metadata]
        assert len(after_events) == len(before_events) + 1
        assert after_events[-1][2] == "asset.uploaded"
