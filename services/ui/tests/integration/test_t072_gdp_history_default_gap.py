"""GDP-01 gap: GET /api/metrics/gdp/history with NO query params must
default to window=1h, resolution=1m, and produce cumulative (non-decreasing)
data points.

Every existing gdp/history test in this suite passes explicit
window/resolution query params (see tests/integration/test_metrics_history.py,
test_gdp_history_bucketed.py, test_sparkline_data.py, and test_metrics.py's
test_gdp_history_valid_params/test_gdp_history_invalid_window_returns_400)
— none exercises the bare-defaults path
(``ui_service/routers/metrics.py``'s ``window: str = Query("1h")`` /
``resolution: str = Query("1m")``).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_gdp_history_default_window_and_resolution(client) -> None:
    """GDP-01: no query params -> window == '1h', resolution == '1m'."""
    response = await client.get("/api/metrics/gdp/history")
    assert response.status_code == 200
    data = response.json()
    assert data["window"] == "1h"
    assert data["resolution"] == "1m"


async def test_gdp_history_default_data_points_are_cumulative_non_decreasing(client) -> None:
    """GDP-01: data_points must be non-decreasing (cumulative GDP)."""
    response = await client.get("/api/metrics/gdp/history")
    assert response.status_code == 200
    points = response.json()["data_points"]
    assert len(points) > 1
    values = [p["gdp"] for p in points]
    assert values == sorted(values)
