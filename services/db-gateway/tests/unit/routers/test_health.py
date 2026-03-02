"""Health endpoint tests — HLTH-01 through HLTH-04."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.mark.unit
class TestHealth:
    """Tests for GET /health endpoint."""

    def test_health_schema(self, app_with_writer: TestClient) -> None:
        """HLTH-01: Health schema is correct."""
        resp = app_with_writer.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert "started_at" in data
        assert isinstance(data["started_at"], str)
        assert "database_size_bytes" in data
        assert isinstance(data["database_size_bytes"], int)
        assert "total_events" in data
        assert isinstance(data["total_events"], int)

    def test_total_events_accurate(self, app_with_writer: TestClient) -> None:
        """HLTH-02: total_events is accurate after N agent registrations."""
        n = 3
        for _ in range(n):
            aid = f"a-{uuid4()}"
            app_with_writer.post(
                "/identity/agents",
                json={
                    "agent_id": aid,
                    "name": "TestAgent",
                    "public_key": f"ed25519:{uuid4()}",
                    "registered_at": "2026-02-28T10:00:00Z",
                    "event": make_event(),
                },
            )
        resp = app_with_writer.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_events"] == n

    def test_uptime_monotonic(self, app_with_writer: TestClient) -> None:
        """HLTH-03: Uptime is monotonic (second call > first)."""
        resp1 = app_with_writer.get("/health")
        assert resp1.status_code == 200
        uptime1 = resp1.json()["uptime_seconds"]

        time.sleep(0.05)

        resp2 = app_with_writer.get("/health")
        assert resp2.status_code == 200
        uptime2 = resp2.json()["uptime_seconds"]
        assert uptime2 > uptime1

    def test_database_size_positive_after_writes(self, app_with_writer: TestClient) -> None:
        """HLTH-04: database_size_bytes is positive after writes."""
        aid = f"a-{uuid4()}"
        app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        resp = app_with_writer.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["database_size_bytes"] > 0
