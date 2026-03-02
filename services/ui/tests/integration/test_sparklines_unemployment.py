"""Acceptance tests for unemployment_rate sparkline metric.

Tickets: agent-economy-cr9, agent-economy-a64
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestUnemploymentSparkline:
    """GET /api/metrics/sparklines must include unemployment_rate."""

    async def test_unemployment_rate_key_present(self, client: httpx.AsyncClient) -> None:
        """The metrics dict must contain the unemployment_rate key."""
        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200
        metrics = resp.json()["metrics"]
        assert "unemployment_rate" in metrics

    async def test_unemployment_rate_has_24_values(self, client: httpx.AsyncClient) -> None:
        """unemployment_rate series must have 24 float values (matching buckets)."""
        resp = await client.get("/api/metrics/sparklines")
        data = resp.json()
        series = data["metrics"]["unemployment_rate"]
        assert isinstance(series, list)
        assert len(series) == 24

    async def test_unemployment_rate_values_between_0_and_1(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        """All unemployment_rate values must be in [0.0, 1.0]."""
        resp = await client.get("/api/metrics/sparklines")
        series = resp.json()["metrics"]["unemployment_rate"]
        for i, val in enumerate(series):
            assert isinstance(val, (int, float)), f"unemployment_rate[{i}] is not a number"
            assert 0.0 <= val <= 1.0, f"unemployment_rate[{i}] = {val} out of [0.0, 1.0]"

    async def test_unemployment_rate_all_non_negative(self, client: httpx.AsyncClient) -> None:
        """No negative values in unemployment_rate."""
        resp = await client.get("/api/metrics/sparklines")
        series = resp.json()["metrics"]["unemployment_rate"]
        for i, val in enumerate(series):
            assert val >= 0, f"unemployment_rate[{i}] = {val} is negative"
