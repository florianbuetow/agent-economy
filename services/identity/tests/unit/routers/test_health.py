"""Unit tests for health endpoint."""

import pytest


@pytest.mark.unit
async def test_health_returns_ok(client):
    """GET /health returns 200 with correct schema."""
    response = await client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["uptime_seconds"], (int, float))
    assert isinstance(data["started_at"], str)
    assert data["registered_agents"] == 0


@pytest.mark.unit
async def test_health_post_not_allowed(client):
    """POST /health returns 405."""
    response = await client.post("/health")
    assert response.status_code == 405
    assert response.json()["error"] == "METHOD_NOT_ALLOWED"
