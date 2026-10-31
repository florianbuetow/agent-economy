"""Coverage for GET /api/metrics response-shape and null-delta guarantees
not yet exercised by the existing metrics test files.

Spec IDs (docs/specifications/service-tests/ui-service-tests.md):
- MET-01: the response body contains exactly the 8 named top-level fields.
  ``MetricsResponse`` in ``ui_service/schemas.py`` sets
  ``model_config = ConfigDict(extra="forbid", ...)``, so an exact key-set
  check is a direct behavioral proof of that contract.
- MET-05: every ``delta_*`` field is strictly ``null`` (not ``0``/NaN) when
  its previous-period denominator is zero. The standard seeded fixture dates
  (Jan-Mar 2026, see tests/integration/helpers.py) are always far in the
  past relative to the *unfrozen*, real-wall-clock ``client`` fixture used
  across this whole suite (only ``quarterly_service.utc_now`` is patched by
  conftest.py — the metrics delta windows are not), so both the current- and
  previous-period windows are empty for every delta at any point this test
  runs in the future. tests/integration/test_metrics_delta.py and
  test_ticker_deltas.py only assert "numeric or null" for exactly this
  reason (they don't rely on that always-in-the-past property); this file
  asserts the strict null the spec actually requires, including
  ``tasks.delta_open`` (WP-13 fix — see the test below).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

EXPECTED_TOP_LEVEL_KEYS = {
    "gdp",
    "agents",
    "tasks",
    "escrow",
    "spec_quality",
    "labor_market",
    "economy_phase",
    "computed_at",
}


async def test_metrics_top_level_keys_are_exact(client) -> None:
    """MET-01: exactly the 8 named fields, no more, no less."""
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert set(response.json().keys()) == EXPECTED_TOP_LEVEL_KEYS


async def test_all_delta_fields_are_strictly_null_with_no_prior_period_data(client) -> None:
    """MET-05: with all seed activity far outside every delta window, every
    delta_* field must be exactly None (not 0, not NaN).

    ``tasks.delta_open`` is included here: WP-13 fixed
    ``ui_service.services.metrics.compute_tasks`` to define it as a
    ``_pct_change`` ratio against the prior-24h newly-opened-task count
    (matching every sibling delta field), instead of a raw, never-null count
    of newly-opened tasks in the last 24h.
    """
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    data = response.json()

    assert data["gdp"]["delta_1h"] is None
    assert data["gdp"]["delta_24h"] is None
    assert data["agents"]["delta_active"] is None
    assert data["tasks"]["delta_open"] is None
    assert data["tasks"]["delta_completed_24h"] is None
    assert data["escrow"]["delta_locked"] is None
    assert data["labor_market"]["delta_avg_bids"] is None
    assert data["labor_market"]["delta_avg_reward"] is None
