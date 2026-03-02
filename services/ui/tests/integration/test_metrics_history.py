"""Acceptance tests for metrics history time-series endpoint.

Ticket: agent-economy-4qq
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestMetricsHistory:
    """GET /api/metrics/gdp/history should serve time-series data."""

    async def test_accepts_valid_window_and_resolution(self, client: httpx.AsyncClient) -> None:
        """Endpoint must accept window and resolution query params."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        assert resp.status_code == 200

    async def test_returns_data_points_array(self, client: httpx.AsyncClient) -> None:
        """Response must contain a data_points list."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        data = resp.json()
        assert "data_points" in data
        assert isinstance(data["data_points"], list)

    async def test_rejects_invalid_window(self, client: httpx.AsyncClient) -> None:
        """400 for invalid window parameter."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "99d", "resolution": "1h"},
        )
        assert resp.status_code == 400

    async def test_rejects_invalid_resolution(self, client: httpx.AsyncClient) -> None:
        """400 for invalid resolution parameter."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "99s"},
        )
        assert resp.status_code == 400

    async def test_data_points_have_timestamp_and_value(self, client: httpx.AsyncClient) -> None:
        """Each data point must have timestamp and gdp fields."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "1h", "resolution": "1m"},
        )
        data = resp.json()
        for point in data["data_points"]:
            assert "timestamp" in point
            assert "gdp" in point
            assert isinstance(point["gdp"], (int, float))

    async def test_data_points_ordered_ascending(self, client: httpx.AsyncClient) -> None:
        """Data points must be ordered by timestamp ascending."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        points = resp.json()["data_points"]
        if len(points) >= 2:
            timestamps = [p["timestamp"] for p in points]
            assert timestamps == sorted(timestamps)

    async def test_includes_window_and_resolution_in_response(
        self, client: httpx.AsyncClient
    ) -> None:
        """Response must echo back the requested window and resolution."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "7d", "resolution": "1h"},
        )
        data = resp.json()
        assert data["window"] == "7d"
        assert data["resolution"] == "1h"
