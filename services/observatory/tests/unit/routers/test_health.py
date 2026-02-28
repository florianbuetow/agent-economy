"""Tests for the health endpoint."""

import pytest


@pytest.mark.unit
async def test_health_returns_ok(client):
    """GET /health returns 200 with expected fields."""
    response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["uptime_seconds"], (int, float))
    assert isinstance(data["started_at"], str)
    assert "latest_event_id" in data
    assert "database_readable" in data
