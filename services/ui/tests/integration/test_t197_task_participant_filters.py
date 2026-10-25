"""T-197 acceptance tests for authoritative participant task filters."""

from __future__ import annotations

from typing import Any

import pytest

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration


def _task(task_id: str, poster_id: str, worker_id: str | None) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "poster_id": poster_id,
        "title": task_id,
        "reward": 10,
        "status": "approved",
        "bid_count": 1,
        "worker_id": worker_id,
        "created_at": "2026-10-25T10:00:00Z",
        "bidding_deadline": "2026-10-25T11:00:00Z",
        "acceptance_deadline": "2026-10-25T11:30:00Z",
        "execution_deadline": "2026-10-25T12:00:00Z",
        "review_deadline": "2026-10-25T13:00:00Z",
        "financial_effect": {
            "schema_version": 1,
            "settlement_state": "historical",
            "receipt_state": "historical",
            "outcome": "worker_paid",
            "operation_id": None,
            "receipt_reference": None,
            "business_projection_pending": False,
            "pending_target_status": None,
        },
    }


class _ParticipantReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None, str | None]] = []
        self.tasks = [
            _task("t-other-new", "a-other", "a-worker"),
            _task("t-owned-new", "a-owner", "a-other-worker"),
            _task("t-owned-old", "a-owner", "a-worker"),
            _task("t-other-old", "a-other", "a-other-worker"),
        ]

    async def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
    ) -> list[dict[str, Any]]:
        self.calls.append((status, poster_id, worker_id))
        return [
            task
            for task in self.tasks
            if (status is None or task["status"] == status)
            and (poster_id is None or task["poster_id"] == poster_id)
            and (worker_id is None or task["worker_id"] == worker_id)
        ]

    async def get_task(self, task_id: str) -> dict[str, Any]:
        raise AssertionError(f"unexpected detail read for {task_id}")


@pytest.mark.parametrize(
    ("query", "expected_call", "expected_task_id"),
    [
        (
            {"poster_id": "a-owner", "limit": 1, "offset": 1},
            (None, "a-owner", None),
            "t-owned-old",
        ),
        (
            {"worker_id": "a-worker", "limit": 1, "offset": 1},
            (None, None, "a-worker"),
            "t-owned-old",
        ),
    ],
)
async def test_participant_filter_is_authoritative_before_pagination(
    client: Any,
    query: dict[str, object],
    expected_call: tuple[str | None, str | None, str | None],
    expected_task_id: str,
) -> None:
    reader = _ParticipantReader()
    get_app_state().task_board_reader = reader

    response = await client.get("/api/tasks", params=query)

    assert response.status_code == 200
    assert reader.calls == [expected_call]
    body = response.json()
    assert body["total_count"] == 2
    assert [task["task_id"] for task in body["tasks"]] == [expected_task_id]


async def test_task_asset_metadata_exposes_attribution_and_integrity(client: Any) -> None:
    response = await client.get("/api/tasks/t-task1")

    assert response.status_code == 200
    assets = response.json()["assets"]
    assert assets
    assert all(isinstance(asset["uploader_id"], str) for asset in assets)
    assert all(
        asset["content_hash"] is None or isinstance(asset["content_hash"], str) for asset in assets
    )
