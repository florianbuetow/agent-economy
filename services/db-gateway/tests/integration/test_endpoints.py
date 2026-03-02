"""Integration tests — require a running Database Gateway service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestHealthIntegration:
    """Integration tests for /health endpoint."""

    def test_health_check(self, gateway_client: httpx.Client) -> None:
        """GET /health returns 200 with expected fields."""
        response = gateway_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "database_size_bytes" in data
        assert "total_events" in data
