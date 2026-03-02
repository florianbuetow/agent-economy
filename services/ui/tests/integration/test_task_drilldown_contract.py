"""Acceptance tests for task lifecycle page API contract.

These tests verify that GET /api/tasks/{task_id} returns ALL fields
that task.js needs to render the task lifecycle page, including bids,
assets, feedback, dispute data, and timeline events.

Ticket: agent-economy-efw
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestTaskDrilldownContract:
    """GET /api/tasks/{task_id} must provide everything task.js needs."""

    async def test_returns_core_task_fields(self, client: httpx.AsyncClient) -> None:
        """Response must include title, spec, reward, status, and poster info."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "title" in data
        assert "spec" in data
        assert "reward" in data
        assert "status" in data
        assert "poster" in data
        assert "agent_id" in data["poster"]
        assert "name" in data["poster"]

    async def test_returns_deadline_fields(self, client: httpx.AsyncClient) -> None:
        """Response must include all deadline fields."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "deadlines" in data
        deadlines = data["deadlines"]
        assert "bidding_deadline" in deadlines
        # execution_deadline and review_deadline may be null for some statuses

    async def test_returns_bids_array(self, client: httpx.AsyncClient) -> None:
        """Response must include bids array with bidder details."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "bids" in data
        assert isinstance(data["bids"], list)
        assert len(data["bids"]) > 0, "t-task1 has bids in seed data"
        bid = data["bids"][0]
        assert "bid_id" in bid
        assert "bidder" in bid
        assert "name" in bid["bidder"]
        assert "proposal" in bid
        assert "submitted_at" in bid

    async def test_returns_assets_for_submitted_task(self, client: httpx.AsyncClient) -> None:
        """Response must include assets array for tasks with deliverables."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "assets" in data
        assert isinstance(data["assets"], list)
        assert len(data["assets"]) > 0, "t-task1 has assets in seed data"
        asset = data["assets"][0]
        assert "filename" in asset
        assert "content_type" in asset
        assert "size_bytes" in asset

    async def test_returns_feedback_for_completed_task(self, client: httpx.AsyncClient) -> None:
        """Response must include feedback array for approved/ruled tasks."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "feedback" in data
        assert isinstance(data["feedback"], list)
        assert len(data["feedback"]) > 0, "t-task1 has feedback in seed data"

    async def test_returns_dispute_for_disputed_task(self, client: httpx.AsyncClient) -> None:
        """Response must include dispute data for disputed/ruled tasks."""
        resp = await client.get("/api/tasks/t-task5")
        assert resp.status_code == 200
        data = resp.json()
        assert "dispute" in data
        assert data["dispute"] is not None, "t-task5 was disputed and ruled"
        dispute = data["dispute"]
        assert "claim_id" in dispute
        assert "reason" in dispute

    async def test_returns_ruling_for_ruled_task(self, client: httpx.AsyncClient) -> None:
        """Response must include ruling data for ruled tasks."""
        resp = await client.get("/api/tasks/t-task5")
        assert resp.status_code == 200
        data = resp.json()
        dispute = data.get("dispute")
        assert dispute is not None
        assert "ruling" in dispute
        ruling = dispute["ruling"]
        assert ruling is not None, "t-task5 has a ruling"
        assert "worker_pct" in ruling
        assert "summary" in ruling
        assert ruling["worker_pct"] == 70

    async def test_404_for_nonexistent_task(self, client: httpx.AsyncClient) -> None:
        """Requesting a non-existent task_id should return 404."""
        resp = await client.get("/api/tasks/t-does-not-exist")
        assert resp.status_code == 404

    async def test_timestamps_present(self, client: httpx.AsyncClient) -> None:
        """Response must include lifecycle timestamps."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "timestamps" in data
        ts = data["timestamps"]
        assert "created_at" in ts
        assert ts["created_at"] is not None
