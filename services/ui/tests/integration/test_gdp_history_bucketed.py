"""Regression test for GAP-E10: GDP history must be bucketed GROUP BY, not per-point.

Before this fix, ``compute_gdp_history`` looped once per requested data point
and ran 2 scalar queries per iteration (one for approved tasks, one for
ruled tasks) — up to ~20k queries for a 7d window at 1m resolution. This test
proves two things on the standard seeded fixture DB:

1. Behavior preservation: the cumulative GDP value at every data point still
   matches what the old per-timestamp recomputation would have produced —
   verified directly (every seeded transaction is from 2026-03, far older
   than any window relative to wall-clock "now", so every point must equal
   the all-time GDP total) and indirectly (inserting a transaction partway
   through a fine-grained window must shift only the points at/after it).
2. A query-count guard: the total number of ``db.execute`` calls behind
   ``GET /api/metrics/gdp/history`` stays a small constant, regardless of how
   many data points the window/resolution combination produces.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from ui_service.core.state import get_app_state

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.integration


async def test_all_points_match_total_when_no_recent_activity(client) -> None:
    """All seeded activity predates every supported window, so gdp is flat = total."""
    metrics_resp = await client.get("/api/metrics")
    assert metrics_resp.status_code == 200
    expected_total = metrics_resp.json()["gdp"]["total"]

    history_resp = await client.get(
        "/api/metrics/gdp/history", params={"window": "7d", "resolution": "1h"}
    )
    assert history_resp.status_code == 200
    points = history_resp.json()["data_points"]
    assert len(points) >= 12

    for point in points:
        assert point["gdp"] == expected_total


async def test_mid_window_transaction_shifts_only_points_at_or_after_it(client, write_db) -> None:
    """A task approved partway through the window bumps gdp starting at its bucket."""
    baseline_resp = await client.get("/api/metrics")
    assert baseline_resp.status_code == 200
    baseline_total = baseline_resp.json()["gdp"]["total"]

    now = datetime.now(UTC)
    # Comfortably inside a minute (avoid boundary flakiness) and well within
    # a 1h window with 1m resolution.
    approved_at = now - timedelta(minutes=30, seconds=30)

    await write_db.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, execution_deadline, "
        "review_deadline, escrow_id, worker_id, accepted_bid_id, dispute_reason, ruling_id, "
        "worker_pct, ruling_summary, created_at, accepted_at, submitted_at, approved_at, "
        "cancelled_at, disputed_at, ruled_at, expired_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "t-gdp-history-mid-window",
            "a-alice",
            "Mid-window GDP task",
            "spec",
            777,
            "approved",
            86400,
            604800,
            172800,
            approved_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            None,
            None,
            "esc-gdp-history-mid-window",
            "a-bob",
            None,
            None,
            None,
            None,
            None,
            approved_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            None,
            None,
            approved_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            None,
            None,
            None,
            None,
        ),
    )
    await write_db.commit()

    history_resp = await client.get(
        "/api/metrics/gdp/history", params={"window": "1h", "resolution": "1m"}
    )
    assert history_resp.status_code == 200
    points = history_resp.json()["data_points"]
    assert len(points) >= 50

    approved_at_iso = approved_at.isoformat(timespec="seconds").replace("+00:00", "Z")

    before_values = [p["gdp"] for p in points if p["timestamp"] < approved_at_iso]
    at_or_after_values = [p["gdp"] for p in points if p["timestamp"] >= approved_at_iso]

    assert before_values, "expected at least one data point before the inserted task"
    assert at_or_after_values, "expected at least one data point at/after the inserted task"
    assert all(v == baseline_total for v in before_values)
    assert all(v == baseline_total + 777 for v in at_or_after_values)


async def test_gdp_history_query_count_is_constant_not_per_point(client) -> None:
    """A 7d/1m request (168*60=10080 points) must not scale query count with point count."""
    state = get_app_state()
    db = state.db
    assert db is not None

    call_count = 0
    original_execute: Callable[..., Any] = db.execute

    def counting_execute(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return original_execute(*args, **kwargs)

    db.execute = counting_execute  # type: ignore[method-assign]
    try:
        response = await client.get(
            "/api/metrics/gdp/history", params={"window": "7d", "resolution": "1m"}
        )
    finally:
        db.execute = original_execute  # type: ignore[method-assign]

    assert response.status_code == 200
    point_count = len(response.json()["data_points"])
    assert point_count > 100, "test setup is broken — expected thousands of data points"
    assert call_count <= 4, (
        f"GET /api/metrics/gdp/history made {call_count} db.execute calls for "
        f"{point_count} data points (expected <= 4) — looks like the per-point loop is back"
    )
