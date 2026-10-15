"""Regression coverage for economy-phase boundary combinations not yet
exercised by tests/integration/test_economy_phase_stalled.py (which covers
PHASE-01 stalled and PHASE-04's flat-trend-plus->20%-dispute case only).

Spec IDs (docs/specifications/service-tests/ui-service-tests.md):
- PHASE-02: increasing trend + dispute_rate < 10% -> growing. This also
  closes the backend half of PHASE-06 ("increasing" is emitted verbatim by
  compute_economy_phase — no existing test seeds an increasing-trend
  scenario at all).
- PHASE-03: decreasing trend, 0% dispute rate -> contracting (proving the
  decreasing-trend arm fires independently of the dispute-rate arm already
  covered by test_economy_phase_stalled.py's contracting case).
- PHASE-05(a): increasing trend + dispute_rate exactly 12% (matches neither
  growing's <10% nor contracting's >20%) -> stable (residual rule).
- PHASE-05(b): stable trend + dispute_rate exactly 18% (15-20% band) ->
  stable. (test_economy_phase_stalled.py's own stable case uses a stable
  trend with a 10% dispute rate, which does not exercise the 15-20% band the
  spec calls out.)

Verified against ui_service/services/metrics.py:compute_economy_phase
(read directly before writing these tests): phase is "stalled" iff no tasks
in the last 60 minutes; else "growing" iff trend=="increasing" and
dispute_rate<0.10; else "contracting" iff trend=="decreasing" OR
dispute_rate>0.20; else "stable" (residual). dispute_rate is an all-time
ratio (not time-windowed), so the standard seeded board_tasks rows are
cleared first to make it exactly controllable; "now" is frozen via the same
injectable clock seam (``ui_service.services.database._clock``) that
test_economy_phase_stalled.py already relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ui_service.services import database as database_service

pytestmark = pytest.mark.integration

FROZEN_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


async def _clear_tasks(write_db) -> None:
    await write_db.execute("DELETE FROM board_tasks")
    await write_db.commit()


async def _insert_task(write_db, task_id: str, status: str, created_at: datetime) -> None:
    created_at_iso = database_service.to_iso(created_at)
    await write_db.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, escrow_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            "a-alice",
            f"Task {task_id}",
            "Generated task spec",
            10,
            status,
            86400,
            604800,
            172800,
            created_at_iso,
            f"esc-{task_id}",
            created_at_iso,
        ),
    )


async def test_increasing_trend_low_dispute_rate_is_growing(client, write_db, monkeypatch) -> None:
    """PHASE-02 (+ PHASE-06 backend half): increasing trend, dispute<10% -> growing."""
    monkeypatch.setattr(database_service, "_clock", lambda: FROZEN_NOW)
    await _clear_tasks(write_db)

    # Current 3.5d period: 4 tasks "now" (also within the 60m recent window,
    # so the phase isn't "stalled").
    for i in range(4):
        await _insert_task(write_db, f"t-cur-{i}", "open", FROZEN_NOW - timedelta(minutes=10))
    # Previous 3.5-7d period: 1 task -> ratio 4/1 = 4.0 > 1.05 => increasing.
    await _insert_task(write_db, "t-prev-0", "open", FROZEN_NOW - timedelta(days=5))
    await write_db.commit()

    response = await client.get("/api/metrics")
    assert response.status_code == 200
    phase = response.json()["economy_phase"]
    assert phase["task_creation_trend"] == "increasing"
    assert phase["dispute_rate"] == 0.0
    assert phase["phase"] == "growing"


async def test_decreasing_trend_is_contracting_regardless_of_dispute_rate(
    client, write_db, monkeypatch
) -> None:
    """PHASE-03: decreasing trend, 0% dispute rate -> contracting."""
    monkeypatch.setattr(database_service, "_clock", lambda: FROZEN_NOW)
    await _clear_tasks(write_db)

    # Current 3.5d period: 1 task "now" (keeps recent_tasks > 0, avoids "stalled").
    await _insert_task(write_db, "t-cur-0", "open", FROZEN_NOW - timedelta(minutes=10))
    # Previous 3.5-7d period: 10 tasks -> ratio 1/10 = 0.1 < 0.95 => decreasing.
    for i in range(10):
        await _insert_task(write_db, f"t-prev-{i}", "open", FROZEN_NOW - timedelta(days=5))
    await write_db.commit()

    response = await client.get("/api/metrics")
    assert response.status_code == 200
    phase = response.json()["economy_phase"]
    assert phase["task_creation_trend"] == "decreasing"
    assert phase["dispute_rate"] == 0.0
    assert phase["phase"] == "contracting"


async def test_increasing_trend_with_12pct_dispute_rate_is_stable(
    client, write_db, monkeypatch
) -> None:
    """PHASE-05(a): increasing trend at a dispute rate matching neither
    growing (<10%) nor contracting (>20%) falls through to the residual
    stable branch."""
    monkeypatch.setattr(database_service, "_clock", lambda: FROZEN_NOW)
    await _clear_tasks(write_db)

    for i in range(5):
        await _insert_task(write_db, f"t-cur-{i}", "open", FROZEN_NOW - timedelta(minutes=10))
    await _insert_task(write_db, "t-prev-0", "open", FROZEN_NOW - timedelta(days=5))
    # Filler tasks far outside the 7-day trend window: 3 disputed + 16 open,
    # so dispute_rate = 3 / (6 trend tasks + 19 filler) = 3/25 = 0.12 exactly.
    filler_ts = FROZEN_NOW - timedelta(days=30)
    for i in range(3):
        await _insert_task(write_db, f"t-filler-disputed-{i}", "disputed", filler_ts)
    for i in range(16):
        await _insert_task(write_db, f"t-filler-open-{i}", "open", filler_ts)
    await write_db.commit()

    response = await client.get("/api/metrics")
    assert response.status_code == 200
    phase = response.json()["economy_phase"]
    assert phase["task_creation_trend"] == "increasing"
    assert phase["dispute_rate"] == 0.12
    assert phase["phase"] == "stable"


async def test_stable_trend_with_18pct_dispute_rate_is_stable(
    client, write_db, monkeypatch
) -> None:
    """PHASE-05(b): a flat trend at a dispute rate inside the 15-20% band
    (matching neither growing nor contracting) is stable."""
    monkeypatch.setattr(database_service, "_clock", lambda: FROZEN_NOW)
    await _clear_tasks(write_db)

    for i in range(4):
        await _insert_task(write_db, f"t-cur-{i}", "open", FROZEN_NOW - timedelta(minutes=10))
    for i in range(4):
        await _insert_task(write_db, f"t-prev-{i}", "open", FROZEN_NOW - timedelta(days=5))
    # Filler: 9 disputed + 33 open, so dispute_rate = 9 / (8 trend + 42 filler) = 9/50 = 0.18.
    filler_ts = FROZEN_NOW - timedelta(days=30)
    for i in range(9):
        await _insert_task(write_db, f"t-filler-disputed-{i}", "disputed", filler_ts)
    for i in range(33):
        await _insert_task(write_db, f"t-filler-open-{i}", "open", filler_ts)
    await write_db.commit()

    response = await client.get("/api/metrics")
    assert response.status_code == 200
    phase = response.json()["economy_phase"]
    assert phase["task_creation_trend"] == "stable"
    assert phase["dispute_rate"] == 0.18
    assert phase["phase"] == "stable"
