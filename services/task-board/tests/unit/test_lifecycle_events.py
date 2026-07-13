"""TaskDbClient lifecycle event mapping tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from task_board_service.services.task_db_client import TaskDbClient


class _FakeGatewayClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, json: dict[str, Any]) -> httpx.Response:
        self.posts.append({"url": url, "json": json})
        return httpx.Response(200, json={"task_id": "t-1", "status": json["updates"].get("status")})


def _make_client(fake_gateway: _FakeGatewayClient) -> TaskDbClient:
    client = TaskDbClient(base_url="http://db-gateway", timeout_seconds=1)
    client._client.close()
    client._client = fake_gateway  # type: ignore[assignment]
    return client


@pytest.mark.unit
@pytest.mark.parametrize(
    ("updates", "expected_event_type"),
    [
        ({"status": "accepted", "accepted_at": "2026-06-20T12:00:00Z"}, "task.accepted"),
        ({"status": "submitted", "submitted_at": "2026-06-20T12:00:00Z"}, "task.submitted"),
        ({"status": "approved", "approved_at": "2026-06-20T12:00:00Z"}, "task.approved"),
        (
            {
                "status": "approved",
                "approved_at": "2026-06-20T12:00:00Z",
                "escrow_pending": 1,
            },
            "task.auto_approved",
        ),
        ({"status": "disputed", "disputed_at": "2026-06-20T12:00:00Z"}, "task.disputed"),
        ({"status": "ruled", "ruled_at": "2026-06-20T12:00:00Z"}, "task.ruled"),
        ({"status": "cancelled", "cancelled_at": "2026-06-20T12:00:00Z"}, "task.cancelled"),
        ({"status": "expired", "expired_at": "2026-06-20T12:00:00Z"}, "task.expired"),
    ],
)
def test_update_task_emits_specific_lifecycle_event(
    updates: dict[str, Any],
    expected_event_type: str,
) -> None:
    fake_gateway = _FakeGatewayClient()
    client = _make_client(fake_gateway)

    changed = client.update_task("t-1", updates, expected_status=None)

    assert changed == 1
    assert fake_gateway.posts[0]["json"]["event"]["event_type"] == expected_event_type


@pytest.mark.unit
def test_update_task_keeps_generic_event_for_non_lifecycle_updates() -> None:
    fake_gateway = _FakeGatewayClient()
    client = _make_client(fake_gateway)

    changed = client.update_task("t-1", {"escrow_pending": 0}, expected_status=None)

    assert changed == 1
    assert fake_gateway.posts[0]["json"]["event"]["event_type"] == "task.updated"
