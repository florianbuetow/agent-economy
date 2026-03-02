"""Acceptance tests for real delta values in ticker and exchange board.

These tests verify that the API provides real delta/change values
that the frontend ticker and exchange board can consume, instead of
hardcoded fake percentages.

Ticket: agent-economy-bxt
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestTickerDeltaValues:
    """GET /api/metrics should provide delta values consumable by the ticker UI."""

    async def test_metrics_has_gdp_delta_for_ticker(self, client: httpx.AsyncClient) -> None:
        """Metrics response must include GDP delta that ticker can display."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        gdp = data["gdp"]
        # The ticker needs a numeric delta value, not a hardcoded string
        assert "delta_1h" in gdp or "delta_24h" in gdp, (
            "GDP must include at least one delta field for ticker display"
        )

    async def test_metrics_has_task_delta_for_board(self, client: httpx.AsyncClient) -> None:
        """Metrics must include task count deltas for the exchange board cells."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        tasks = data["tasks"]
        assert "delta_open" in tasks, "Tasks must include delta_open for exchange board"

    async def test_metrics_has_escrow_delta_for_board(self, client: httpx.AsyncClient) -> None:
        """Metrics must include escrow delta for the exchange board."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "delta_locked" in data["escrow"], (
            "Escrow must include delta_locked for exchange board"
        )

    async def test_metrics_has_labor_delta_for_board(self, client: httpx.AsyncClient) -> None:
        """Metrics must include labor market deltas for the exchange board."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        labor = data["labor_market"]
        assert "delta_avg_bids" in labor, "Labor market must include delta_avg_bids"

    async def test_delta_values_include_direction(self, client: httpx.AsyncClient) -> None:
        """Delta values should be signed (positive or negative) to indicate direction."""
        resp = await client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        gdp_delta = data["gdp"].get("delta_1h")
        # Delta must be a signed number (positive = growth, negative = decline)
        if gdp_delta is not None:
            assert isinstance(gdp_delta, (int, float)), (
                f"Delta must be numeric, got {type(gdp_delta)}"
            )
