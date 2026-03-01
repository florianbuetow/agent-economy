"""Tests for the health check endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.unit
class TestHealthEndpoint:
    """Test GET /health."""

    async def test_health_returns_200(self, client: AsyncClient) -> None:
        """GET /health returns 200."""
        response = await client.get("/health")
        assert response.status_code == 200

    async def test_health_returns_status_ok(self, client: AsyncClient) -> None:
        """GET /health response has status 'ok'."""
        response = await client.get("/health")
        data = response.json()
        assert data["status"] == "ok"

    async def test_health_contains_uptime_seconds(self, client: AsyncClient) -> None:
        """GET /health response contains uptime_seconds."""
        response = await client.get("/health")
        data = response.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))

    async def test_health_contains_started_at(self, client: AsyncClient) -> None:
        """GET /health response contains started_at."""
        response = await client.get("/health")
        data = response.json()
        assert "started_at" in data
        assert isinstance(data["started_at"], str)

    async def test_health_contains_total_feedback(self, client: AsyncClient) -> None:
        """GET /health response contains total_feedback."""
        response = await client.get("/health")
        data = response.json()
        assert "total_feedback" in data
        assert isinstance(data["total_feedback"], int)

    async def test_total_feedback_starts_at_zero(self, client: AsyncClient) -> None:
        """total_feedback starts at 0 for a fresh app."""
        response = await client.get("/health")
        data = response.json()
        assert data["total_feedback"] == 0
