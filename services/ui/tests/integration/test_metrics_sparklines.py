"""Acceptance tests for metrics sparklines time-series endpoint.

Tickets: agent-economy-xr3, agent-economy-59a
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx

EXPECTED_METRIC_KEYS = {
    "open_tasks",
    "in_execution",
    "completion_rate",
    "disputes_active",
    "escrow_locked",
    "avg_bids_per_task",
    "avg_reward",
    "spec_quality",
    "registered_agents",
    "unemployment_rate",
}


@pytest.mark.integration
class TestMetricsSparklines:
    """GET /api/metrics/sparklines should return time-series data for all metrics."""

    async def test_returns_200_for_valid_window(self, client: httpx.AsyncClient) -> None:
        """Endpoint must accept window=24h."""
        resp = await client.get("/api/metrics/sparklines", params={"window": "24h"})
        assert resp.status_code == 200

    async def test_returns_200_with_default_window(self, client: httpx.AsyncClient) -> None:
        """Endpoint must work with no explicit window param (defaults to 24h)."""
        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200

    async def test_rejects_invalid_window(self, client: httpx.AsyncClient) -> None:
        """400 for unsupported window parameter."""
        resp = await client.get("/api/metrics/sparklines", params={"window": "7d"})
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "invalid_parameter"

    async def test_response_has_required_fields(self, client: httpx.AsyncClient) -> None:
        """Response must contain window, buckets, and metrics."""
        resp = await client.get("/api/metrics/sparklines")
        data = resp.json()
        assert "window" in data
        assert "buckets" in data
        assert "metrics" in data
        assert data["window"] == "24h"

    async def test_buckets_are_24_hourly_strings(self, client: httpx.AsyncClient) -> None:
        """Buckets list should have 24 entries (one per hour)."""
        resp = await client.get("/api/metrics/sparklines")
        data = resp.json()
        buckets = data["buckets"]
        assert isinstance(buckets, list)
        assert len(buckets) == 24
        for bucket in buckets:
            assert isinstance(bucket, str)
            assert len(bucket) == 13  # "2026-03-02T09" format

    async def test_metrics_has_all_expected_keys(self, client: httpx.AsyncClient) -> None:
        """Metrics dict must contain all 9 expected metric keys."""
        resp = await client.get("/api/metrics/sparklines")
        data = resp.json()
        metrics = data["metrics"]
        assert set(metrics.keys()) == EXPECTED_METRIC_KEYS

    async def test_each_metric_has_24_values(self, client: httpx.AsyncClient) -> None:
        """Each metric series must have 24 float values (matching buckets)."""
        resp = await client.get("/api/metrics/sparklines")
        data = resp.json()
        for key in EXPECTED_METRIC_KEYS:
            series = data["metrics"][key]
            assert isinstance(series, list), f"{key} is not a list"
            assert len(series) == 24, f"{key} has {len(series)} values, expected 24"

    async def test_all_values_are_non_negative(self, client: httpx.AsyncClient) -> None:
        """All sparkline values must be non-negative."""
        resp = await client.get("/api/metrics/sparklines")
        data = resp.json()
        for key in EXPECTED_METRIC_KEYS:
            for i, val in enumerate(data["metrics"][key]):
                assert isinstance(val, (int, float)), f"{key}[{i}] is not a number"
                assert val >= 0, f"{key}[{i}] = {val} is negative"

    async def test_registered_agents_non_decreasing(self, client: httpx.AsyncClient) -> None:
        """Registered agents is cumulative — must be non-decreasing."""
        resp = await client.get("/api/metrics/sparklines")
        series = resp.json()["metrics"]["registered_agents"]
        for i in range(1, len(series)):
            assert series[i] >= series[i - 1], (
                f"registered_agents decreased at index {i}: {series[i - 1]} -> {series[i]}"
            )

    async def test_completion_rate_between_0_and_1(self, client: httpx.AsyncClient) -> None:
        """Completion rate values must be between 0.0 and 1.0."""
        resp = await client.get("/api/metrics/sparklines")
        series = resp.json()["metrics"]["completion_rate"]
        for i, val in enumerate(series):
            assert 0.0 <= val <= 1.0, f"completion_rate[{i}] = {val} out of [0, 1]"
