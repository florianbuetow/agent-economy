"""Acceptance tests for delta/change fields in GET /api/metrics.

These tests verify that the metrics response includes delta fields
showing percentage change compared to previous time windows.
Tests are expected to FAIL until delta fields are implemented.

Ticket: agent-economy-d9b
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestMetricsDeltaFields:
    """GET /api/metrics should include delta/change fields."""

    async def test_gdp_includes_delta_1h(self, client: httpx.AsyncClient) -> None:
        """GDP metrics should include delta_1h showing hourly percentage change."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "gdp" in data
        assert "delta_1h" in data["gdp"], "GDP metrics must include delta_1h field"
        assert isinstance(data["gdp"]["delta_1h"], (int, float, type(None)))

    async def test_gdp_includes_delta_24h(self, client: httpx.AsyncClient) -> None:
        """GDP metrics should include delta_24h showing daily percentage change."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "delta_24h" in data["gdp"], "GDP metrics must include delta_24h field"
        assert isinstance(data["gdp"]["delta_24h"], (int, float, type(None)))

    async def test_task_metrics_include_deltas(self, client: httpx.AsyncClient) -> None:
        """Task metrics should include delta fields for open and completed counts."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        tasks = data["tasks"]
        assert "delta_open" in tasks, "Task metrics must include delta_open"
        assert "delta_completed_24h" in tasks, "Task metrics must include delta_completed_24h"

    async def test_labor_market_includes_deltas(self, client: httpx.AsyncClient) -> None:
        """Labor market metrics should include delta for avg_bids and avg_reward."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        labor = data["labor_market"]
        assert "delta_avg_bids" in labor, "Labor market must include delta_avg_bids"
        assert "delta_avg_reward" in labor, "Labor market must include delta_avg_reward"

    async def test_escrow_includes_delta(self, client: httpx.AsyncClient) -> None:
        """Escrow metrics should include delta for locked amount."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        escrow = data["escrow"]
        assert "delta_locked" in escrow, "Escrow must include delta_locked"

    async def test_agent_metrics_include_delta(self, client: httpx.AsyncClient) -> None:
        """Agent metrics should include delta for active count."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        agents = data["agents"]
        assert "delta_active" in agents, "Agent metrics must include delta_active"

    async def test_delta_values_are_numeric_or_null(self, client: httpx.AsyncClient) -> None:
        """All delta fields should be numeric (float) or null when insufficient data."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        # Check that delta fields are float or None
        gdp_delta = data["gdp"].get("delta_1h")
        assert gdp_delta is None or isinstance(gdp_delta, (int, float)), (
            f"delta_1h should be numeric or null, got {type(gdp_delta)}"
        )
