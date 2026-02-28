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


@pytest.mark.unit
async def test_health_database_readable(seeded_client):
    """HEALTH-01: Health check with database reports database_readable=true."""
    response = await seeded_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["database_readable"] is True
    assert data["latest_event_id"] >= 0


@pytest.mark.unit
async def test_health_reports_latest_event_id(seeded_client):
    """HEALTH-02: latest_event_id matches highest event_id in seeded data."""
    response = await seeded_client.get("/health")
    data = response.json()
    assert data["latest_event_id"] == 15
