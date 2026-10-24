"""Focused acceptance tests for atomic legacy asset admission."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.schema import SQLITE_MAX_INTEGER
from tests.conftest import make_event
from tests.fakes.native_board_history import (
    BusinessClock,
    TaskGraph,
    accept_bid,
    create_task,
    register_agent,
    register_worker,
    submit_bid,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


@pytest.fixture(autouse=True)
def _fixed_gateway_business_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep native task fixture deadlines independent of wall-clock time."""
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", BusinessClock())


def _asset_body(
    task_id: str,
    uploader_id: str,
    *,
    asset_id: str | None = None,
    admission: object = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "asset_id": asset_id or f"asset-{uuid4()}",
        "task_id": task_id,
        "uploader_id": uploader_id,
        "filename": "deliverable.txt",
        "content_type": "text/plain",
        "size_bytes": 7,
        "storage_path": f"assets/{task_id}/deliverable.txt",
        "content_hash": "a" * 64,
        "uploaded_at": "2099-01-01T12:00:01Z",
        "event": make_event(
            source="board",
            event_type="asset.uploaded",
            task_id=task_id,
            agent_id=uploader_id,
        ),
    }
    if admission is not None:
        body["admission"] = admission
    return body


def _admission(*, version: int = 1, quota: int = 2) -> dict[str, object]:
    return {
        "expected_status": "accepted",
        "expected_lifecycle_version": version,
        "max_files_per_task": quota,
    }


def _accept_task(client: TestClient) -> tuple[TaskGraph, str]:
    graph = create_task(client)
    worker_id = register_worker(client)
    bid_id, submitted = submit_bid(client, graph, worker_id)
    assert submitted.status_code == 200, submitted.text
    accepted = accept_bid(client, graph, worker_id, bid_id)
    assert accepted.status_code == 200, accepted.text
    return graph, worker_id


def _write_counts(writer: DbWriter) -> tuple[int, int]:
    asset_count = writer.connection.execute("SELECT COUNT(*) FROM board_assets").fetchone()[0]
    event_count = writer.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return int(asset_count), int(event_count)


@pytest.mark.unit
@pytest.mark.parametrize(
    "admission",
    [
        None,
        {},
        {
            "expected_status": "open",
            "expected_lifecycle_version": 1,
            "max_files_per_task": 2,
        },
        {
            "expected_status": "accepted",
            "expected_lifecycle_version": True,
            "max_files_per_task": 2,
        },
        {
            "expected_status": "accepted",
            "expected_lifecycle_version": -1,
            "max_files_per_task": 2,
        },
        {
            "expected_status": "accepted",
            "expected_lifecycle_version": SQLITE_MAX_INTEGER + 1,
            "max_files_per_task": 2,
        },
        {
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "max_files_per_task": True,
        },
        {
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "max_files_per_task": 0,
        },
        {
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "max_files_per_task": SQLITE_MAX_INTEGER + 1,
        },
        {
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "max_files_per_task": 2,
            "unexpected": "field",
        },
    ],
)
def test_asset_router_rejects_missing_or_invalid_admission_without_writes(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    admission: object,
) -> None:
    """HTTP admission is required, strict, bounded, and side-effect free on failure."""
    before = _write_counts(db_writer)
    response = app_with_writer.post(
        "/board/assets",
        json=_asset_body("t-does-not-matter", "a-does-not-matter", admission=admission),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"
    assert _write_counts(db_writer) == before


@pytest.mark.unit
def test_raw_asset_writer_cannot_bypass_required_admission(
    db_writer: DbWriter,
) -> None:
    """Calling the writer directly without admission cannot bypass the HTTP guard."""
    before = _write_counts(db_writer)

    with pytest.raises(Exception) as raised:
        db_writer.record_asset(_asset_body("t-missing", "a-missing"), None)

    error = raised.value
    assert getattr(error, "error", None) == "invalid_payload"
    assert getattr(error, "status_code", None) == 400
    assert _write_counts(db_writer) == before


@pytest.mark.unit
def test_asset_writer_enforces_task_version_worker_and_quota_atomically(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    """All final admission checks and both inserts share one write transaction."""
    graph, worker_id = _accept_task(app_with_writer)
    other_worker_id = register_agent(app_with_writer, name="Other worker")

    guarded_requests = [
        (
            _asset_body(graph.task_id, worker_id, admission=_admission(version=0)),
            409,
            "task_version_conflict",
        ),
        (_asset_body(graph.task_id, other_worker_id, admission=_admission()), 403, "forbidden"),
    ]
    for body, status, code in guarded_requests:
        before = _write_counts(db_writer)
        response = app_with_writer.post("/board/assets", json=body)
        assert response.status_code == status
        assert response.json()["error"] == code
        assert _write_counts(db_writer) == before

    first_body = _asset_body(graph.task_id, worker_id, admission=_admission(quota=1))
    first = app_with_writer.post("/board/assets", json=first_body)
    assert first.status_code == 201, first.text

    before_quota_rejection = _write_counts(db_writer)
    duplicate = app_with_writer.post("/board/assets", json=first_body)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "asset_exists"
    assert _write_counts(db_writer) == before_quota_rejection

    second = app_with_writer.post(
        "/board/assets",
        json=_asset_body(graph.task_id, worker_id, admission=_admission(quota=1)),
    )
    assert second.status_code == 409
    assert second.json()["error"] == "too_many_assets"
    assert _write_counts(db_writer) == before_quota_rejection


@pytest.mark.unit
def test_asset_writer_distinguishes_missing_and_nonaccepted_tasks_without_writes(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    """Missing task and changed status use the required stable error codes."""
    graph = create_task(app_with_writer)
    uploader_id = register_agent(app_with_writer, name="Uploader")

    cases = [
        (
            _asset_body("t-missing", uploader_id, admission=_admission(version=0)),
            404,
            "task_not_found",
        ),
        (
            _asset_body(graph.task_id, uploader_id, admission=_admission(version=0)),
            409,
            "task_version_conflict",
        ),
    ]
    for body, status, code in cases:
        before = _write_counts(db_writer)
        response = app_with_writer.post("/board/assets", json=body)
        assert response.status_code == status
        assert response.json()["error"] == code
        assert _write_counts(db_writer) == before
