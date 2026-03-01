"""Health endpoint tests for Task Board service."""

from __future__ import annotations

import asyncio

import pytest

from tests.unit.routers.conftest import create_task


@pytest.mark.unit
async def test_health_returns_ok_with_correct_schema(client):
    """HEALTH-01: GET /health returns 200 with correct schema."""
    response = await client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["uptime_seconds"], (int, float))
    assert isinstance(data["started_at"], str)
    assert data["total_tasks"] == 0
    assert isinstance(data["tasks_by_status"], dict)

    expected_statuses = {
        "open",
        "accepted",
        "submitted",
        "approved",
        "cancelled",
        "disputed",
        "ruled",
        "expired",
    }
    assert set(data["tasks_by_status"].keys()) == expected_statuses
    for status_key in expected_statuses:
        assert data["tasks_by_status"][status_key] == 0


@pytest.mark.unit
async def test_health_uptime_increases_over_time(client):
    """HEALTH-03: Uptime is monotonic — second call returns higher uptime."""
    first = await client.get("/health")
    assert first.status_code == 200
    first_uptime = first.json()["uptime_seconds"]

    await asyncio.sleep(1.1)

    second = await client.get("/health")
    assert second.status_code == 200
    second_uptime = second.json()["uptime_seconds"]

    assert second_uptime > first_uptime


@pytest.mark.unit
async def test_health_task_counts_reflect_actual_data(client, alice_keypair, alice_agent_id):
    """HEALTH-02: Total task count matches created tasks."""
    # Verify initial state
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["total_tasks"] == 0

    # Create first task
    resp1 = await create_task(client, alice_keypair, alice_agent_id)
    assert resp1.status_code == 201

    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["total_tasks"] == 1

    # Create second task
    resp2 = await create_task(client, alice_keypair, alice_agent_id)
    assert resp2.status_code == 201

    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["total_tasks"] == 2


@pytest.mark.unit
async def test_health_post_not_allowed(client):
    """HEALTH-04: POST /health returns 405 METHOD_NOT_ALLOWED."""
    response = await client.post("/health")
    assert response.status_code == 405
    assert response.json()["error"] == "METHOD_NOT_ALLOWED"
