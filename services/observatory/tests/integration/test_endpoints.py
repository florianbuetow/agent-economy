"""Integration tests — exercise full request/response cycle through the app."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.integration
class TestHealthEndpoint:
    """Test health endpoint through the full app stack."""

    async def test_health_returns_ok_with_seeded_database(self, client: AsyncClient) -> None:
        """GET /health returns 200 with database_readable=True on a seeded DB."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["database_readable"] is True
        assert data["latest_event_id"] > 0
        assert "uptime_seconds" in data
        assert "started_at" in data
