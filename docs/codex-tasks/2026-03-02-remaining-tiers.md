# Remaining Tiers: Complete Implementation Plan

This plan covers ALL remaining non-E2E tickets for the UI service. Execute each tier in order. After each tier, validate with CI.

## Rules

- Do NOT use git (no git remote in this project)
- Use `uv run` for all Python execution — never raw `python`, `python3`, or `pip install`
- Do NOT modify any existing test files in `tests/`
- You may create NEW test files
- After each tier, run: `cd services/ui && just ci-quiet`
- If CI fails, fix issues and re-run before proceeding to the next tier

---

## Pre-flight: Read These Files First

Read each file below **in order** before starting any implementation:

1. `AGENTS.md` — project conventions, architecture, testing rules
2. `services/ui/src/ui_service/schemas.py` — all Pydantic response models
3. `services/ui/src/ui_service/services/metrics.py` — metrics business logic (700 lines)
4. `services/ui/src/ui_service/routers/metrics.py` — metrics endpoint handlers
5. `services/ui/src/ui_service/services/agents.py` — agent business logic
6. `services/ui/src/ui_service/routers/agents.py` — agent endpoint handlers
7. `services/ui/data/web/assets/shared.js` — frontend state + API client
8. `services/ui/data/web/assets/landing.js` — landing page sparklines + leaderboard
9. `services/ui/data/web/assets/observatory.js` — observatory sparklines + leaderboard
10. `services/ui/tests/integration/test_sparkline_data.py` — existing sparkline tests
11. `services/ui/tests/integration/conftest.py` — test fixtures

---

## TIER 3: Metrics History API + Real Sparklines (tickets: 4qq, 4tk, xbg)

### Background

The existing `/api/metrics/gdp/history` endpoint already works and returns GDP time-series data. Currently, ALL sparklines in the frontend use `ATE.genSparkline()` which generates random data. The fix is:

1. **GDP sparklines**: Call the existing `/api/metrics/gdp/history` endpoint and use real GDP data
2. **Other metric sparklines**: Since there's no history endpoint for non-GDP metrics, keep using generated data for now but make the generation deterministic (seed-based) instead of random

### Step 3.1: Write tests for metrics history endpoint (ticket 4qq)

Create file: `services/ui/tests/integration/test_metrics_history.py`

```python
"""Acceptance tests for metrics history time-series endpoint.

Ticket: agent-economy-4qq
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestMetricsHistory:
    """GET /api/metrics/gdp/history should serve time-series data."""

    async def test_accepts_valid_window_and_resolution(
        self, client: httpx.AsyncClient
    ) -> None:
        """Endpoint must accept window and resolution query params."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        assert resp.status_code == 200

    async def test_returns_data_points_array(
        self, client: httpx.AsyncClient
    ) -> None:
        """Response must contain a data_points list."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        data = resp.json()
        assert "data_points" in data
        assert isinstance(data["data_points"], list)

    async def test_rejects_invalid_window(
        self, client: httpx.AsyncClient
    ) -> None:
        """400 for invalid window parameter."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "99d", "resolution": "1h"},
        )
        assert resp.status_code == 400

    async def test_rejects_invalid_resolution(
        self, client: httpx.AsyncClient
    ) -> None:
        """400 for invalid resolution parameter."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "99s"},
        )
        assert resp.status_code == 400

    async def test_data_points_have_timestamp_and_value(
        self, client: httpx.AsyncClient
    ) -> None:
        """Each data point must have timestamp and gdp fields."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "1h", "resolution": "1m"},
        )
        data = resp.json()
        for point in data["data_points"]:
            assert "timestamp" in point
            assert "gdp" in point
            assert isinstance(point["gdp"], (int, float))

    async def test_data_points_ordered_ascending(
        self, client: httpx.AsyncClient
    ) -> None:
        """Data points must be ordered by timestamp ascending."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "24h", "resolution": "1h"},
        )
        points = resp.json()["data_points"]
        if len(points) >= 2:
            timestamps = [p["timestamp"] for p in points]
            assert timestamps == sorted(timestamps)

    async def test_includes_window_and_resolution_in_response(
        self, client: httpx.AsyncClient
    ) -> None:
        """Response must echo back the requested window and resolution."""
        resp = await client.get(
            "/api/metrics/gdp/history",
            params={"window": "7d", "resolution": "1h"},
        )
        data = resp.json()
        assert data["window"] == "7d"
        assert data["resolution"] == "1h"
```

After creating this file, run: `cd services/ui && just ci-quiet`

All tests should PASS because the `/api/metrics/gdp/history` endpoint already exists and works.

### Step 3.2: Wire GDP sparklines to real API data (ticket xbg)

This step converts the frontend to fetch real GDP history for sparklines instead of using random data.

#### Step 3.2a: Add `fetchGDPHistory()` to shared.js

In `services/ui/data/web/assets/shared.js`, add a new `gdpHistory` field to the `S` object defaults (after `taskCreationTrend`):

```javascript
    taskCreationTrend: 'stable',
    gdpHistory: [],
```

Then, after the `fetchAgents()` function definition (around line 204), add a new function:

```javascript
  /**
   * Fetch GDP history for sparkline rendering.
   * Returns array of numeric GDP values or null on error.
   */
  async function fetchGDPHistory() {
    try {
      var response = await fetch('/api/metrics/gdp/history?window=24h&resolution=1h');
      if (!response.ok) {
        console.warn('[ATE] fetchGDPHistory failed:', response.status);
        return null;
      }
      var data = await response.json();
      S.gdpHistory = (data.data_points || []).map(function(p) { return p.gdp; });
      return S.gdpHistory;
    } catch (err) {
      console.warn('[ATE] fetchGDPHistory error:', err.message);
      return null;
    }
  }
```

Then add `fetchGDPHistory` to the exports object at the bottom of the file:

```javascript
    fetchGDPHistory: fetchGDPHistory,
```

#### Step 3.2b: Use real GDP data in observatory.js

In `services/ui/data/web/assets/observatory.js`, function `buildGDPPanel()` (around line 35), replace the random sparkline generation:

Find:
```javascript
    var gdpSpark = ATE.sparkData(24, S.gdp.total || 100, (S.gdp.total || 100) * 0.02);
    var perAgentSpark = ATE.sparkData(24, S.gdp.perAgent || 100, (S.gdp.perAgent || 100) * 0.05);
```

Replace with:
```javascript
    var gdpSpark = S.gdpHistory.length >= 2 ? S.gdpHistory : ATE.sparkData(24, S.gdp.total || 100, (S.gdp.total || 100) * 0.02);
    var perAgentSpark = S.gdpHistory.length >= 2 && S.agents.active > 0 ? S.gdpHistory.map(function(v) { return v / S.agents.active; }) : ATE.sparkData(24, S.gdp.perAgent || 100, (S.gdp.perAgent || 100) * 0.05);
```

Then in the observatory boot sequence (DOMContentLoaded handler, around line 130-145), add `ATE.fetchGDPHistory()` to the initial data load. Find the boot sequence — it should look like:

```javascript
  document.addEventListener('DOMContentLoaded', async function() {
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents()]);
```

Add `ATE.fetchGDPHistory()` to the parallel fetch:

```javascript
  document.addEventListener('DOMContentLoaded', async function() {
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents(), ATE.fetchGDPHistory()]);
```

#### Step 3.2c: Use real GDP data in landing.js for GDP cells

In `services/ui/data/web/assets/landing.js`, function `buildExchangeBoard()`, the first 4 cells are GDP-related. Replace their spark fields with real data where available.

Find the cells array (lines 50-65). Replace ONLY the first 4 GDP cell spark values:

```javascript
      { label: 'GDP Total', value: ..., spark: S.gdpHistory.length >= 2 ? S.gdpHistory : ATE.genSparkline(16, 40, 8) },
      { label: 'GDP Last 24h', value: ..., spark: S.gdpHistory.length >= 2 ? S.gdpHistory : ATE.genSparkline(16, 30, 10) },
      { label: 'GDP / Agent', value: ..., spark: S.gdpHistory.length >= 2 && S.agents.active > 0 ? S.gdpHistory.map(function(v) { return v / S.agents.active; }) : ATE.genSparkline(16, 42, 6) },
      { label: 'GDP Rate', value: ..., spark: S.gdpHistory.length >= 2 ? S.gdpHistory.slice(1).map(function(v, i) { return v - S.gdpHistory[i]; }) : ATE.genSparkline(16, 13, 4) },
```

**IMPORTANT:** Keep the rest of each cell definition (label, value, delta, up) unchanged. Only replace the `spark:` value.

Also add `ATE.fetchGDPHistory()` to the landing page boot sequence. Find:

```javascript
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents()]);
```

Replace with:

```javascript
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents(), ATE.fetchGDPHistory()]);
```

### Step 3.3: Validation Gate

Run: `cd services/ui && just ci-quiet`

ALL checks must pass. If any fail, fix and re-run.

---

## TIER 4: Agent Streak API + Frontend Wiring (tickets: hdk, ex5, yai, n6r)

### Background

The agent leaderboards show streak fire icons when `streak >= 3`, but streak is hardcoded to `0` in `fetchAgents()`. The fix is:

1. Add `current_streak: int` to `AgentStats` in schemas.py
2. Compute streak in `_compute_agent_stats()` in services/agents.py
3. Map the field in shared.js `fetchAgents()`

### Step 4.1: Write tests for current_streak (ticket hdk)

Create file: `services/ui/tests/integration/test_agent_streak.py`

```python
"""Acceptance tests for current_streak field in AgentStats.

Ticket: agent-economy-hdk
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestAgentStreak:
    """GET /api/agents response should include current_streak field."""

    async def test_agent_list_has_current_streak(
        self, client: httpx.AsyncClient
    ) -> None:
        """Each agent in the list must have a current_streak integer field."""
        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        for agent in data["agents"]:
            assert "current_streak" in agent["stats"], (
                f"Agent {agent['agent_id']} missing current_streak in stats"
            )
            assert isinstance(agent["stats"]["current_streak"], int)

    async def test_agent_profile_has_current_streak(
        self, client: httpx.AsyncClient
    ) -> None:
        """Agent profile must include current_streak in stats."""
        # First get an agent_id
        list_resp = await client.get("/api/agents?limit=1")
        agents = list_resp.json()["agents"]
        if not agents:
            pytest.skip("No agents in test database")
        agent_id = agents[0]["agent_id"]
        resp = await client.get(f"/api/agents/{agent_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_streak" in data["stats"]
        assert isinstance(data["stats"]["current_streak"], int)

    async def test_streak_is_non_negative(
        self, client: httpx.AsyncClient
    ) -> None:
        """Streak must be >= 0."""
        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        for agent in resp.json()["agents"]:
            assert agent["stats"]["current_streak"] >= 0
```

After creating this file, run: `cd services/ui && just ci-quiet`

These tests will FAIL because `current_streak` does not exist yet. That's expected — just verify they compile and CI passes on everything except the test failures. Actually, since the Pydantic model uses `extra="forbid"`, the test will fail with a validation error. That's fine — we'll fix it in Step 4.2.

**IMPORTANT:** If `just ci-quiet` fails only because these new tests fail (not because of lint/format/type errors), that is OK — proceed to Step 4.2. If it fails for other reasons (lint, format, types, security), fix those first.

### Step 4.2: Add current_streak to schemas (ticket ex5)

In `services/ui/src/ui_service/schemas.py`, find the `AgentStats` class (around line 156-163):

```python
class AgentStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks_posted: int
    tasks_completed_as_worker: int
    total_earned: int
    total_spent: int
    spec_quality: SpecQualityStats
    delivery_quality: DeliveryQualityStats
```

Add `current_streak: int` as the last field:

```python
class AgentStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks_posted: int
    tasks_completed_as_worker: int
    total_earned: int
    total_spent: int
    spec_quality: SpecQualityStats
    delivery_quality: DeliveryQualityStats
    current_streak: int
```

### Step 4.3: Compute streak in services/agents.py (ticket ex5)

In `services/ui/src/ui_service/services/agents.py`, find the `_compute_agent_stats()` function.

At the end of the function (just before the `return` statement), add the streak computation:

```python
    # Compute current streak — count of consecutive approved tasks as worker,
    # counting backwards from the most recent.
    streak_rows = await execute_fetchall(
        db,
        "SELECT status FROM board_tasks "
        "WHERE worker_id = ? AND status IN ('approved', 'disputed', 'ruled', 'cancelled') "
        "ORDER BY COALESCE(approved_at, submitted_at, created_at) DESC",
        (agent_id,),
    )
    current_streak = 0
    for row in streak_rows:
        if row[0] == "approved":
            current_streak += 1
        else:
            break
```

Then add `current_streak` to the returned dict. Find the return statement and add the field. The current return statement looks something like:

```python
    return {
        "tasks_posted": tasks_posted,
        "tasks_completed_as_worker": tasks_completed_as_worker,
        "total_earned": total_earned,
        "total_spent": total_spent,
        "spec_quality": { ... },
        "delivery_quality": { ... },
    }
```

Add `"current_streak": current_streak,` to the dict.

### Step 4.4: Wire streak in routers/agents.py (ticket ex5)

Check `services/ui/src/ui_service/routers/agents.py`. In the `GET /agents` handler, the `AgentStats` model is constructed from the service data. Find where `AgentStats(...)` is built and add `current_streak=s["current_streak"]` to the constructor call.

Similarly for the `GET /agents/{agent_id}` handler — find `AgentStats(...)` and add the field.

### Step 4.5: Map streak in shared.js fetchAgents() (ticket n6r, yai)

In `services/ui/data/web/assets/shared.js`, find the `fetchAgents()` function. In the AGENTS.push() call (around line 177-197), find:

```javascript
          streak: 0
```

Replace with:

```javascript
          streak: a.stats.current_streak || 0
```

### Step 4.6: Validation Gate

Run: `cd services/ui && just ci-quiet`

ALL checks must pass, including the new streak tests from Step 4.1.

---

## TIER 5: Dead Code Cleanup (tickets: lqj, r0h)

### Background

After wiring real GDP sparklines, the `sparkData()` and `genSparkline()` functions in shared.js are STILL needed for non-GDP metric sparklines (the exchange board cells that don't have a history API). So DO NOT remove them yet.

**Skip this tier entirely.** The `sparkData()` and `genSparkline()` functions cannot be removed until ALL sparklines use real data, which requires the full metrics history API (ticket 4tk). Mark tickets lqj and r0h as deferred.

---

## Verification: Full Project CI

After completing all tiers, run:

```bash
cd /Users/flo/Developer/github/agent-economy && just ci-all-quiet
```

This validates ALL services, not just the UI service. Every check must pass.

---

## Summary of All Changes

### Tier 3 (Sparklines)
| File | Change |
|------|--------|
| `tests/integration/test_metrics_history.py` | NEW: 7 tests for metrics history endpoint |
| `data/web/assets/shared.js` | Add `gdpHistory: []` to S, add `fetchGDPHistory()`, export it |
| `data/web/assets/observatory.js` | Use real GDP history for sparklines, add fetchGDPHistory to boot |
| `data/web/assets/landing.js` | Use real GDP history for GDP cells, add fetchGDPHistory to boot |

### Tier 4 (Agent Streaks)
| File | Change |
|------|--------|
| `tests/integration/test_agent_streak.py` | NEW: 3 tests for current_streak field |
| `src/ui_service/schemas.py` | Add `current_streak: int` to AgentStats |
| `src/ui_service/services/agents.py` | Compute streak in `_compute_agent_stats()` |
| `src/ui_service/routers/agents.py` | Map `current_streak` in AgentStats construction |
| `data/web/assets/shared.js` | Map `a.stats.current_streak` in fetchAgents() |

### Tier 5 (Dead Code)
| Action | Reason |
|--------|--------|
| SKIP / DEFER | sparkData/genSparkline still needed for non-GDP sparklines |
