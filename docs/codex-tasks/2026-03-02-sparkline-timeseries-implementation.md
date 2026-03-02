# Sparkline Time-Series Implementation Plan

> **Tickets**: agent-economy-xr3, agent-economy-mxg, agent-economy-boe, agent-economy-59a
> **Date**: 2026-03-02
> **Scope**: Backend sparkline endpoint (UI + Observatory) + Frontend wiring + Bug fixes + Tests

---

## IMPORTANT RULES — READ FIRST

1. **Read `AGENTS.md` before doing anything** — it contains all project conventions
2. **Use `uv run` for all Python execution** — never use `python`, `python3`, or `pip install`
3. **Do NOT modify any existing test files** — add new test files only
4. **Do NOT use git** — this project has no git repository configured
5. **All Pydantic models must use `ConfigDict(extra="forbid")`**
6. **Never hardcode configuration values** — but this feature uses no config (pure computation)
7. **Business logic goes in `services/` layer** — routers are thin wrappers
8. **After each tier, run verification commands as specified**

---

## Tier 1: Backend — Add `GET /api/metrics/sparklines` to UI Service

### 1A: Add `compute_sparkline_history()` to `services/ui/src/ui_service/services/metrics.py`

**Add this function AFTER the existing `compute_gdp_history()` function (after line 700).** Do NOT modify any existing functions.

```python
async def compute_sparkline_history(
    db: aiosqlite.Connection,
    window: str,
) -> dict[str, Any]:
    """Compute hourly-bucket sparkline time series for exchange board metrics.

    Uses efficient GROUP BY queries on the events table instead of
    the per-step loop pattern used by compute_gdp_history.
    """
    now = utc_now()
    window_delta = {"24h": timedelta(hours=24)}[window]
    start = now - window_delta
    since_iso = to_iso(start)

    # Build 24 hourly bucket keys: "2026-03-02T09", "2026-03-02T10", ...
    buckets: list[str] = []
    current = start
    while current < now:
        buckets.append(to_iso(current)[:13])
        current += timedelta(hours=1)

    async def _fetch_buckets(
        sql: str,
        params: tuple[Any, ...],
    ) -> dict[str, float]:
        """Run a GROUP BY bucket query, return {bucket_str: value}."""
        rows = await execute_fetchall(db, sql, params)
        return {str(row[0]): float(row[1]) for row in rows}

    def _to_series(bucket_dict: dict[str, float]) -> list[float]:
        """Convert bucket dict to ordered list aligned with bucket keys."""
        return [bucket_dict.get(b, 0.0) for b in buckets]

    # 1. Tasks created per hour
    created = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'task.created' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 2. Tasks accepted per hour (proxy for in-execution activity)
    accepted = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'task.accepted' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 3a. Approvals per hour
    approved = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? "
        "AND event_type IN ('task.approved', 'task.auto_approved') "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 3b. All terminal events per hour (for completion rate denominator)
    terminal = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? "
        "AND event_type IN ('task.approved', 'task.auto_approved', "
        "'task.disputed', 'task.cancelled', 'task.expired') "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 4. Disputes filed per hour
    disputed = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'task.disputed' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 5. Escrow locked amount per hour
    escrow = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, "
        "COALESCE(SUM(CAST(json_extract(payload, '$.amount') AS REAL)), 0) "
        "FROM events WHERE timestamp >= ? AND event_type = 'escrow.locked' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 6. Bids submitted per hour
    bids = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'bid.submitted' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 7. Average reward of tasks created per hour
    avg_reward = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, "
        "COALESCE(AVG(CAST(json_extract(payload, '$.reward') AS REAL)), 0) "
        "FROM events WHERE timestamp >= ? AND event_type = 'task.created' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 8. Spec quality feedback events per hour
    spec_quality = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? "
        "AND event_type = 'feedback.revealed' "
        "AND json_extract(payload, '$.category') = 'spec_quality' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    # 9. Agent registrations — cumulative (need baseline before window)
    reg_per_bucket = await _fetch_buckets(
        "SELECT substr(timestamp, 1, 13) AS bucket, COUNT(*) "
        "FROM events WHERE timestamp >= ? AND event_type = 'agent.registered' "
        "GROUP BY bucket ORDER BY bucket",
        (since_iso,),
    )

    baseline = int(
        await execute_scalar(
            db,
            "SELECT COUNT(*) FROM events "
            "WHERE timestamp < ? AND event_type = 'agent.registered'",
            (since_iso,),
        )
        or 0
    )

    reg_series = _to_series(reg_per_bucket)
    running = float(baseline)
    registered_cumulative: list[float] = []
    for val in reg_series:
        running += val
        registered_cumulative.append(running)

    # Completion rate per bucket: approved / terminal (or 0 if no terminal)
    completion_rate: list[float] = []
    for bucket in buckets:
        t = terminal.get(bucket, 0.0)
        a = approved.get(bucket, 0.0)
        completion_rate.append(round(a / t, 3) if t > 0 else 0.0)

    return {
        "window": window,
        "buckets": buckets,
        "metrics": {
            "open_tasks": _to_series(created),
            "in_execution": _to_series(accepted),
            "completion_rate": completion_rate,
            "disputes_active": _to_series(disputed),
            "escrow_locked": _to_series(escrow),
            "avg_bids_per_task": _to_series(bids),
            "avg_reward": _to_series(avg_reward),
            "spec_quality": _to_series(spec_quality),
            "registered_agents": registered_cumulative,
        },
    }
```

**Also update the imports at the top of the file.** The function needs `execute_fetchall` in addition to the existing `execute_scalar`. Check the existing imports — if `execute_fetchall` is not already imported from `ui_service.services.database`, add it.

Look at the current imports (around line 8):
```python
from ui_service.services.database import (
    execute_scalar,
    now_iso,
    to_iso,
    utc_now,
)
```

Add `execute_fetchall` to this import:
```python
from ui_service.services.database import (
    execute_fetchall,
    execute_scalar,
    now_iso,
    to_iso,
    utc_now,
)
```

### 1B: Add schema models to `services/ui/src/ui_service/schemas.py`

**Add these classes AFTER the `GDPHistoryResponse` class (after line 137), before the `# Agents` section.**

```python
# ---------------------------------------------------------------------------
# Sparkline History
# ---------------------------------------------------------------------------
class SparklineMetrics(BaseModel):
    """Time-series data for all exchange board sparklines."""

    model_config = ConfigDict(extra="forbid")
    open_tasks: list[float]
    in_execution: list[float]
    completion_rate: list[float]
    disputes_active: list[float]
    escrow_locked: list[float]
    avg_bids_per_task: list[float]
    avg_reward: list[float]
    spec_quality: list[float]
    registered_agents: list[float]


class SparklineResponse(BaseModel):
    """Response for GET /api/metrics/sparklines."""

    model_config = ConfigDict(extra="forbid")
    window: str
    buckets: list[str]
    metrics: SparklineMetrics
```

### 1C: Add route handler to `services/ui/src/ui_service/routers/metrics.py`

**Step 1:** Update the imports at the top of the file. Add `SparklineMetrics` and `SparklineResponse` to the import from `ui_service.schemas`:

Find this block (around line 10):
```python
from ui_service.schemas import (
    AgentMetrics,
    EconomyPhaseMetrics,
    EscrowMetrics,
    GDPDataPoint,
    GDPHistoryResponse,
    GDPMetrics,
    LaborMarketMetrics,
    MetricsResponse,
    RewardDistribution,
    SpecQualityMetrics,
    TaskMetrics,
)
```

Add `SparklineMetrics` and `SparklineResponse` to it (maintain alphabetical order):
```python
from ui_service.schemas import (
    AgentMetrics,
    EconomyPhaseMetrics,
    EscrowMetrics,
    GDPDataPoint,
    GDPHistoryResponse,
    GDPMetrics,
    LaborMarketMetrics,
    MetricsResponse,
    RewardDistribution,
    SparklineMetrics,
    SparklineResponse,
    SpecQualityMetrics,
    TaskMetrics,
)
```

**Step 2:** Add a new constant after `VALID_RESOLUTIONS`:

```python
VALID_SPARKLINE_WINDOWS = {"24h"}
```

**Step 3:** Add the new route handler AFTER the existing `get_gdp_history` function (at the end of the file):

```python
@router.get("/metrics/sparklines")  # nosemgrep
async def get_sparklines(
    window: str = Query("24h"),
) -> JSONResponse:
    """Return all metric sparkline time series for the exchange board."""
    if window not in VALID_SPARKLINE_WINDOWS:
        raise ServiceError(
            error="invalid_parameter",
            message=(
                f"Invalid window: {window}. "
                f"Must be one of: {', '.join(sorted(VALID_SPARKLINE_WINDOWS))}"
            ),
            status_code=400,
            details={"parameter": "window", "value": window},
        )

    state = get_app_state()
    db = state.db
    assert db is not None

    raw = await metrics_service.compute_sparkline_history(db, window)

    response = SparklineResponse(
        window=raw["window"],
        buckets=raw["buckets"],
        metrics=SparklineMetrics(**raw["metrics"]),
    )

    return JSONResponse(content=response.model_dump(by_alias=True))
```

### Tier 1 Verification

```bash
cd services/ui && just ci-quiet
```

This must pass before proceeding. If there are import errors, type errors, or formatting issues, fix them now.

---

## Tier 2: Mirror to Observatory Service

The observatory service has the same structure but under `observatory_service.*` namespace.

### 2A: Add `compute_sparkline_history()` to `services/observatory/src/observatory_service/services/metrics.py`

Read this file first to understand its structure. It will be very similar to the UI service's `metrics.py` but with different imports:

```python
from observatory_service.services.database import (
    execute_fetchall,  # ADD THIS
    execute_scalar,
    now_iso,
    to_iso,
    utc_now,
)
```

Add the exact same `compute_sparkline_history()` function as in Tier 1A — the code is identical, only the import path differs (and that's handled at the top of the file).

**IMPORTANT:** Read `services/observatory/src/observatory_service/services/database.py` first to confirm `execute_fetchall` exists there. If it does not, you will need to add it (copy from `services/ui/src/ui_service/services/database.py`).

### 2B: Add schema models to `services/observatory/src/observatory_service/schemas.py`

Read the observatory `schemas.py` first. It will likely already have `GDPHistoryResponse`. Add `SparklineMetrics` and `SparklineResponse` in the same location (after GDP History section).

The schema classes are **identical** to the UI service versions from Tier 1B.

### 2C: Add route handler to `services/observatory/src/observatory_service/routers/metrics.py`

Same changes as Tier 1C but with `observatory_service` imports:

1. Add `SparklineMetrics, SparklineResponse` to the schema imports
2. Add `VALID_SPARKLINE_WINDOWS = {"24h"}`
3. Add the `get_sparklines()` route handler (identical code, just different import namespace)

### Tier 2 Verification

```bash
cd services/observatory && just ci-quiet
```

---

## Tier 3: Frontend — Wire Sparklines + Fix Bugs

### 3A: Update `services/ui/data/web/assets/shared.js`

**Step 1:** Add `sparklines: {}` to the initial state object `S` (around line 17, after `rewardDist`):

Find this line:
```javascript
  rewardDist: { '0-10': 0, '11-50': 0, '51-100': 0, '100+': 0 }
```

Change it to (add comma and new property):
```javascript
  rewardDist: { '0-10': 0, '11-50': 0, '51-100': 0, '100+': 0 },
  sparklines: {}
```

**Step 2:** Add `fetchSparklines()` function AFTER the existing `fetchGDPHistory()` function (around line 214):

```javascript
  /**
   * Fetch all metric sparkline series from /api/metrics/sparklines.
   * Populates ATE.S.sparklines keyed by metric name.
   */
  async function fetchSparklines() {
    try {
      var response = await fetch('/api/metrics/sparklines?window=24h');
      if (!response.ok) {
        console.warn('[ATE] fetchSparklines failed:', response.status);
        return null;
      }
      var data = await response.json();
      S.sparklines = data.metrics || {};
      return S.sparklines;
    } catch (err) {
      console.warn('[ATE] fetchSparklines error:', err.message);
      return null;
    }
  }
```

**Step 3:** Export `fetchSparklines` in the `window.ATE` object at the bottom of the file.

Find the export block (around line 393):
```javascript
  window.ATE = {
    ...
    fetchGDPHistory: fetchGDPHistory,
    fetchEvents: fetchEvents,
    ...
  };
```

Add `fetchSparklines: fetchSparklines,` after `fetchGDPHistory: fetchGDPHistory,`.

**Step 4:** Fix the spec quality direction bug in `buildBottomTicker()`.

Find this line (around line 366):
```javascript
      { sym: 'SPEC/QUAL', val: Math.round(S.specQ.avg) + '%', chg: '\u2191' + S.specQ.delta.toFixed(1) + '%', up: true },
```

Replace with:
```javascript
      { sym: 'SPEC/QUAL', val: Math.round(S.specQ.avg) + '%', chg: (S.specQ.delta >= 0 ? '\u2191' : '\u2193') + Math.abs(S.specQ.delta).toFixed(1) + '%', up: S.specQ.delta > 0 ? true : S.specQ.delta < 0 ? false : null },
```

### 3B: Update `services/ui/data/web/assets/landing.js`

**Step 1:** Add `fetchSparklines` to the boot `Promise.all` call.

Find (around line 198):
```javascript
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents(), ATE.fetchGDPHistory()]);
```

Replace with:
```javascript
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents(), ATE.fetchGDPHistory(), ATE.fetchSparklines()]);
```

**Step 2:** Add a `spark()` helper inside `buildExchangeBoard()`.

Find the `buildExchangeBoard()` function (around line 41). Add this helper right inside the function, before the `cells` array definition:

```javascript
    function spark(key) {
      var data = S.sparklines[key];
      return data && data.length >= 2 ? data : [0];
    }
```

**Step 3:** Replace `spark: [0]` with real data for 9 cells.

In the `cells` array (lines 55-65), make these replacements:

| Line (approx) | Cell label | Old | New |
|---|---|---|---|
| 55 | Open Tasks | `spark: [0]` | `spark: spark('open_tasks')` |
| 56 | In Execution | `spark: [0]` | `spark: spark('in_execution')` |
| 57 | Completion Rate | `spark: [0]` | `spark: spark('completion_rate')` |
| 58 | Disputes Active | `spark: [0]` | `spark: spark('disputes_active')` |
| 59 | Escrow Locked | `spark: [0]` | `spark: spark('escrow_locked')` |
| 60 | Avg Bids/Task | `spark: [0]` | `spark: spark('avg_bids_per_task')` |
| 61 | Avg Reward | `spark: [0]` | `spark: spark('avg_reward')` |
| 63 | Spec Quality | `spark: [0]` | `spark: spark('spec_quality')` |
| 64 | Registered | `spark: [0]` | `spark: spark('registered_agents')` |

**Leave these two cells unchanged** (still `spark: [0]`):
- `Unemployment` (line 62)
- `Rewards 51-100` (line 65)

**Step 4:** Fix spec quality direction in exchange board.

Find line 63 (the Spec Quality cell):
```javascript
      { label: 'Spec Quality', value: Math.round(S.specQ.avg) + '%', delta: '+' + S.specQ.delta.toFixed(1) + '%', up: true, spark: [0] },
```

Replace with:
```javascript
      { label: 'Spec Quality', value: Math.round(S.specQ.avg) + '%', delta: (S.specQ.delta >= 0 ? '+' : '') + S.specQ.delta.toFixed(1) + '%', up: S.specQ.delta > 0 ? true : S.specQ.delta < 0 ? false : null, spark: spark('spec_quality') },
```

**Step 5:** Fix spec quality direction in KPI strip.

Find line 18:
```javascript
      { label: 'Spec Quality', value: Math.round(S.specQ.avg), suffix: '%', note: '\u2191 ' + S.specQ.delta.toFixed(1) + '% this week', noteUp: true },
```

Replace with:
```javascript
      { label: 'Spec Quality', value: Math.round(S.specQ.avg), suffix: '%', note: (S.specQ.delta >= 0 ? '\u2191 ' : '\u2193 ') + Math.abs(S.specQ.delta).toFixed(1) + '% this week', noteUp: S.specQ.delta > 0 ? true : S.specQ.delta < 0 ? false : null },
```

**Step 6:** Fix economy phase KPI note.

Find line 19:
```javascript
      { label: 'Economy Phase', value: null, text: S.phase.toUpperCase(), suffix: '', note: 'tasks \u2191 disputes \u2193', noteUp: true }
```

Replace with:
```javascript
      { label: 'Economy Phase', value: null, text: S.phase.toUpperCase(), suffix: '', note: 'tasks ' + (S.taskCreationTrend === 'increasing' ? '\u2191' : S.taskCreationTrend === 'decreasing' ? '\u2193' : '\u2192'), noteUp: S.taskCreationTrend === 'increasing' ? true : S.taskCreationTrend === 'decreasing' ? false : null }
```

### Tier 3 Verification

```bash
cd services/ui && just ci-quiet
```

The frontend files are not type-checked by mypy/pyright, but CI checks spell-checking and semgrep rules on them.

---

## Tier 4: Integration Tests

### 4A: Create new test file `services/ui/tests/integration/test_metrics_sparklines.py`

**Do NOT modify any existing test files.** Create this new file:

```python
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
                f"registered_agents decreased at index {i}: {series[i-1]} -> {series[i]}"
            )

    async def test_completion_rate_between_0_and_1(self, client: httpx.AsyncClient) -> None:
        """Completion rate values must be between 0.0 and 1.0."""
        resp = await client.get("/api/metrics/sparklines")
        series = resp.json()["metrics"]["completion_rate"]
        for i, val in enumerate(series):
            assert 0.0 <= val <= 1.0, f"completion_rate[{i}] = {val} out of [0, 1]"
```

### Tier 4 Verification

```bash
cd services/ui && just ci-quiet
```

ALL tests must pass, including existing ones. The new tests run against the seeded test database from `helpers.py`.

---

## Final Verification — MUST DO

After all tiers are complete, run the full project CI:

```bash
cd /Users/flo/Developer/github/agent-economy && just ci-all-quiet
```

This runs CI for ALL services. It must pass with zero failures.

If any service fails, investigate and fix. Common issues:
- Import ordering (run `cd services/<name> && just code-format`)
- Type annotation missing (mypy/pyright errors)
- Spelling errors in variable names or comments (codespell)

---

## Summary of All Files Modified

| File | Change |
|------|--------|
| `services/ui/src/ui_service/services/metrics.py` | Add `execute_fetchall` import, add `compute_sparkline_history()` |
| `services/ui/src/ui_service/schemas.py` | Add `SparklineMetrics`, `SparklineResponse` |
| `services/ui/src/ui_service/routers/metrics.py` | Add schema imports, `VALID_SPARKLINE_WINDOWS`, `get_sparklines()` route |
| `services/observatory/src/observatory_service/services/metrics.py` | Same as UI service metrics.py changes |
| `services/observatory/src/observatory_service/schemas.py` | Same as UI service schemas.py changes |
| `services/observatory/src/observatory_service/routers/metrics.py` | Same as UI service router changes |
| `services/ui/data/web/assets/shared.js` | Add `sparklines: {}`, `fetchSparklines()`, fix spec quality ticker |
| `services/ui/data/web/assets/landing.js` | Wire sparklines, fix spec quality direction, fix economy phase note |
| `services/ui/tests/integration/test_metrics_sparklines.py` | **NEW FILE** — 11 integration tests |
