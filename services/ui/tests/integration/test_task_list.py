"""Acceptance tests for GET /api/tasks general task list endpoint.

These tests verify a new general-purpose task listing endpoint that
supports filtering by status, pagination, and sorting.

Ticket: agent-economy-z3y
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestTaskListEndpoint:
    """GET /api/tasks should return a browsable list of tasks."""

    async def test_returns_200_with_task_list(self, client: httpx.AsyncClient) -> None:
        """GET /api/tasks should return 200 with a list of tasks."""
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert isinstance(data["tasks"], list)
        assert len(data["tasks"]) > 0, "Seed data should produce at least one task"

    async def test_task_list_item_schema(self, client: httpx.AsyncClient) -> None:
        """Each task in the list must include required fields."""
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        task = tasks[0]
        required_fields = {
            "task_id",
            "title",
            "status",
            "reward",
            "poster_id",
            "created_at",
            "bid_count",
        }
        missing = required_fields - set(task.keys())
        assert not missing, f"Task list item missing fields: {missing}"

    async def test_filter_by_status_open(self, client: httpx.AsyncClient) -> None:
        """Filtering by status=open should only return open tasks."""
        resp = await client.get("/api/tasks", params={"status": "open"})
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        for task in tasks:
            assert task["status"] == "open", f"Expected open, got {task['status']}"

    async def test_filter_by_status_disputed(self, client: httpx.AsyncClient) -> None:
        """Filtering by status=disputed should return disputed tasks."""
        resp = await client.get("/api/tasks", params={"status": "disputed"})
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        for task in tasks:
            assert task["status"] == "disputed"

    async def test_pagination_limit(self, client: httpx.AsyncClient) -> None:
        """Limit parameter should cap the number of returned tasks."""
        resp = await client.get("/api/tasks", params={"limit": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) <= 2

    async def test_pagination_offset(self, client: httpx.AsyncClient) -> None:
        """Offset parameter should skip tasks for pagination."""
        resp_all = await client.get("/api/tasks", params={"limit": 100})
        all_tasks = resp_all.json()["tasks"]
        assert len(all_tasks) >= 2, "Seed data must include at least 2 tasks"

        resp_offset = await client.get("/api/tasks", params={"limit": 100, "offset": 1})
        offset_tasks = resp_offset.json()["tasks"]
        # The first task in offset results should be the second task in full results
        assert offset_tasks[0]["task_id"] == all_tasks[1]["task_id"]

    async def test_default_sort_by_created_at_desc(self, client: httpx.AsyncClient) -> None:
        """Tasks should be sorted by created_at descending by default."""
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert len(tasks) >= 2, "Seed data must include at least 2 tasks"
        # Verify descending order
        for i in range(len(tasks) - 1):
            assert tasks[i]["created_at"] >= tasks[i + 1]["created_at"], (
                "Expected descending order: "
                f"{tasks[i]['created_at']} >= {tasks[i + 1]['created_at']}"
            )

    async def test_invalid_status_returns_400(self, client: httpx.AsyncClient) -> None:
        """Invalid status filter values should return 400."""
        resp = await client.get("/api/tasks", params={"status": "nonexistent_status"})
        assert resp.status_code == 400

    async def test_empty_list_for_no_matches(self, client: httpx.AsyncClient) -> None:
        """Filtering by a status with no matching tasks returns empty list."""
        # Use a valid status that might have no tasks in seed data
        resp = await client.get("/api/tasks", params={"status": "expired", "limit": 100})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["tasks"], list)
        # We don't assert empty - seed data may or may not have expired tasks
        # But the response shape must be correct
