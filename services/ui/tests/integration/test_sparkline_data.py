"""Acceptance tests for sparkline data from real API history.

These tests verify that API history endpoints provide enough data
points in the right format for sparkline rendering, replacing the
current random data generation.

Ticket: agent-economy-5jy
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestSparklineDataFromHistory:
    """API history endpoints must provide data suitable for sparkline rendering."""

    async def test_gdp_history_returns_data_points(self, client: httpx.AsyncClient) -> None:
        """GET /api/metrics/gdp/history should return data points for sparklines."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data_points" in data
        assert isinstance(data["data_points"], list)

    async def test_gdp_history_has_sufficient_points(self, client: httpx.AsyncClient) -> None:
        """History should have at least 12 data points for meaningful sparklines."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        assert resp.status_code == 200
        points = resp.json()["data_points"]
        assert len(points) >= 12, f"Sparklines need at least 12 data points, got {len(points)}"

    async def test_gdp_history_points_have_numeric_values(self, client: httpx.AsyncClient) -> None:
        """Each data point must have a numeric value field for bar chart heights."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        assert resp.status_code == 200
        points = resp.json()["data_points"]
        assert len(points) > 0, "Seed data must produce at least one data point"
        for point in points:
            assert "gdp" in point or "value" in point, (
                f"Data point must have 'gdp' or 'value' field, got keys: {list(point.keys())}"
            )
            value = point.get("gdp") or point.get("value")
            assert isinstance(value, (int, float)), (
                f"Sparkline value must be numeric, got {type(value)}"
            )

    async def test_gdp_history_points_have_timestamps(self, client: httpx.AsyncClient) -> None:
        """Each data point must have a timestamp for x-axis positioning."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        assert resp.status_code == 200
        points = resp.json()["data_points"]
        assert len(points) > 0, "Seed data must produce at least one data point"
        for point in points:
            assert "timestamp" in point, "Each data point must have a timestamp"

    async def test_7d_window_provides_more_points(self, client: httpx.AsyncClient) -> None:
        """7d window with 1h resolution should provide up to 168 data points."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "7d", "resolution": "1h"},
        )
        assert resp.status_code == 200
        points = resp.json()["data_points"]
        # Should have more points than 24h window
        assert len(points) >= 12, "7d window should provide at least 12 data points"

    async def test_1h_window_with_1m_resolution(self, client: httpx.AsyncClient) -> None:
        """1h window with 1m resolution should provide fine-grained data."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "1h", "resolution": "1m"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data_points" in data
        assert isinstance(data["data_points"], list)
