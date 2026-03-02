# P2 API Test Tickets — Codex Execution Plan

## Overview

Write failing acceptance tests for 5 P2 test tickets. These tests validate API contracts that do not yet exist — they MUST fail against current code. The goal is to write syntactically valid, CI-compliant test files that will pass once the corresponding features are implemented.

Tickets covered:
1. **agent-economy-d9b** — Tests for delta/change fields in GET /api/metrics
2. **agent-economy-bxt** — Tests for real delta values in ticker and exchange board
3. **agent-economy-z3y** — Tests for GET /api/tasks general task list endpoint
4. **agent-economy-efw** — Tests for task lifecycle page API integration
5. **agent-economy-5jy** — Tests for sparklines using real GDP history data

## Pre-Flight

Read these files FIRST before doing anything:
1. `AGENTS.md` — project conventions (CRITICAL: uv run, no pip, no hardcoded defaults)
2. This file — the execution plan (read it completely before starting)
3. `services/ui/tests/integration/conftest.py` — existing integration test fixtures
4. `services/ui/tests/integration/helpers.py` — seed data (5 agents, 12 tasks, 25 events)
5. `services/ui/src/ui_service/routers/metrics.py` — current metrics router
6. `services/ui/src/ui_service/routers/tasks.py` — current tasks router
7. `services/ui/src/ui_service/schemas.py` — current Pydantic models
8. `docs/specifications/schema.sql` — database schema

## Rules

- There is NO git in this project. Do NOT use git commands, git worktrees, or attempt any git operations. Simply write files directly.
- Use `uv run` for all Python execution — never raw python, python3, or pip install
- Do NOT modify any existing test files (they are acceptance tests)
- All new test files go in `services/ui/tests/integration/` or `services/ui/tests/unit/`
- Tests MUST be marked with `@pytest.mark.integration` or `@pytest.mark.unit`
- Tests are EXPECTED to fail — they test features that don't exist yet
- Tests MUST be syntactically valid and pass CI lint/format/type checks
- Follow the exact patterns in existing test files (imports, fixtures, assertions)
- Run `cd services/ui && just ci-quiet` after ALL phases to verify CI compliance
- Do NOT run `just test` — the tests are expected to fail. Only run `just ci-quiet` which checks code quality without requiring tests to pass. Actually, `ci-quiet` DOES run tests. So instead:
  - Run `cd services/ui && just code-format && just code-style && just code-spell` to verify code quality
  - Run `cd services/ui && uv run pytest tests/integration/test_metrics_delta.py -v --timeout=30 || true` (expected to fail)
  - The `|| true` ensures CI doesn't stop on expected test failures

---

## Phase 1: Delta Fields in GET /api/metrics (agent-economy-d9b)

### File: `services/ui/tests/integration/test_metrics_delta.py`

This test file verifies that GET /api/metrics includes delta/change fields showing percentage changes over time windows.

```python
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
```

### Verification:

```bash
cd services/ui && uv run pytest tests/integration/test_metrics_delta.py -v --timeout=30 2>&1 | head -30
```
Expected: Tests FAIL with KeyError or assertion errors (delta fields don't exist yet). This is correct.

---

## Phase 2: Real Delta Values for Ticker/Exchange Board (agent-economy-bxt)

### File: `services/ui/tests/integration/test_ticker_deltas.py`

```python
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
```

---

## Phase 3: GET /api/tasks General Task List (agent-economy-z3y)

### File: `services/ui/tests/integration/test_task_list.py`

```python
"""Acceptance tests for GET /api/tasks general task list endpoint.

These tests verify a new general-purpose task listing endpoint that
supports filtering by status, pagination, and sorting.

Ticket: agent-economy-z3y
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestTaskListEndpoint:
    """GET /api/tasks should return a browsable list of tasks."""

    async def test_returns_200_with_task_list(self, client: httpx.AsyncClient) -> None:
        """GET /api/tasks should return 200 with a list of tasks."""
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert isinstance(data["tasks"], list)
        assert len(data["tasks"]) > 0, "Seed data should produce at least one task"

    async def test_task_list_item_schema(self, client: httpx.AsyncClient) -> None:
        """Each task in the list must include required fields."""
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        task = tasks[0]
        required_fields = {"task_id", "title", "status", "reward", "poster_id", "created_at", "bid_count"}
        missing = required_fields - set(task.keys())
        assert not missing, f"Task list item missing fields: {missing}"

    async def test_filter_by_status_open(self, client: httpx.AsyncClient) -> None:
        """Filtering by status=open should only return open tasks."""
        resp = await client.get("/api/tasks", params={"status": "open"})
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        for task in tasks:
            assert task["status"] == "open", f"Expected open, got {task['status']}"

    async def test_filter_by_status_disputed(self, client: httpx.AsyncClient) -> None:
        """Filtering by status=disputed should return disputed tasks."""
        resp = await client.get("/api/tasks", params={"status": "disputed"})
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        for task in tasks:
            assert task["status"] == "disputed"

    async def test_pagination_limit(self, client: httpx.AsyncClient) -> None:
        """Limit parameter should cap the number of returned tasks."""
        resp = await client.get("/api/tasks", params={"limit": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) <= 2

    async def test_pagination_offset(self, client: httpx.AsyncClient) -> None:
        """Offset parameter should skip tasks for pagination."""
        resp_all = await client.get("/api/tasks", params={"limit": 100})
        all_tasks = resp_all.json()["tasks"]
        if len(all_tasks) < 2:
            pytest.skip("Not enough tasks to test offset")

        resp_offset = await client.get("/api/tasks", params={"limit": 100, "offset": 1})
        offset_tasks = resp_offset.json()["tasks"]
        # The first task in offset results should be the second task in full results
        assert offset_tasks[0]["task_id"] == all_tasks[1]["task_id"]

    async def test_default_sort_by_created_at_desc(self, client: httpx.AsyncClient) -> None:
        """Tasks should be sorted by created_at descending by default."""
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        if len(tasks) < 2:
            pytest.skip("Not enough tasks to verify sort order")
        # Verify descending order
        for i in range(len(tasks) - 1):
            assert tasks[i]["created_at"] >= tasks[i + 1]["created_at"], (
                f"Expected descending order: {tasks[i]['created_at']} >= {tasks[i+1]['created_at']}"
            )

    async def test_invalid_status_returns_400(self, client: httpx.AsyncClient) -> None:
        """Invalid status filter values should return 400."""
        resp = await client.get("/api/tasks", params={"status": "nonexistent_status"})
        assert resp.status_code == 400

    async def test_empty_list_for_no_matches(self, client: httpx.AsyncClient) -> None:
        """Filtering by a status with no matching tasks returns empty list."""
        # Use a valid status that might have no tasks in seed data
        resp = await client.get("/api/tasks", params={"status": "expired", "limit": 100})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["tasks"], list)
        # We don't assert empty — seed data may or may not have expired tasks
        # But the response shape must be correct
```

---

## Phase 4: Task Lifecycle Page API Integration (agent-economy-efw)

### File: `services/ui/tests/integration/test_task_drilldown_contract.py`

```python
"""Acceptance tests for task lifecycle page API contract.

These tests verify that GET /api/tasks/{task_id} returns ALL fields
that task.js needs to render the task lifecycle page, including bids,
assets, feedback, dispute data, and timeline events.

Ticket: agent-economy-efw
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestTaskDrilldownContract:
    """GET /api/tasks/{task_id} must provide everything task.js needs."""

    async def test_returns_core_task_fields(self, client: httpx.AsyncClient) -> None:
        """Response must include title, spec, reward, status, and poster info."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "title" in data
        assert "spec" in data
        assert "reward" in data
        assert "status" in data
        assert "poster" in data
        assert "agent_id" in data["poster"]
        assert "name" in data["poster"]

    async def test_returns_deadline_fields(self, client: httpx.AsyncClient) -> None:
        """Response must include all deadline fields."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "deadlines" in data
        deadlines = data["deadlines"]
        assert "bidding_deadline" in deadlines
        # execution_deadline and review_deadline may be null for some statuses

    async def test_returns_bids_array(self, client: httpx.AsyncClient) -> None:
        """Response must include bids array with bidder details."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "bids" in data
        assert isinstance(data["bids"], list)
        assert len(data["bids"]) > 0, "t-task1 has bids in seed data"
        bid = data["bids"][0]
        assert "bid_id" in bid
        assert "bidder" in bid
        assert "name" in bid["bidder"]
        assert "proposal" in bid
        assert "submitted_at" in bid

    async def test_returns_assets_for_submitted_task(self, client: httpx.AsyncClient) -> None:
        """Response must include assets array for tasks with deliverables."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "assets" in data
        assert isinstance(data["assets"], list)
        assert len(data["assets"]) > 0, "t-task1 has assets in seed data"
        asset = data["assets"][0]
        assert "filename" in asset
        assert "content_type" in asset
        assert "size_bytes" in asset

    async def test_returns_feedback_for_completed_task(self, client: httpx.AsyncClient) -> None:
        """Response must include feedback array for approved/ruled tasks."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "feedback" in data
        assert isinstance(data["feedback"], list)
        assert len(data["feedback"]) > 0, "t-task1 has feedback in seed data"

    async def test_returns_dispute_for_disputed_task(self, client: httpx.AsyncClient) -> None:
        """Response must include dispute data for disputed/ruled tasks."""
        resp = await client.get("/api/tasks/t-task5")
        assert resp.status_code == 200
        data = resp.json()
        assert "dispute" in data
        assert data["dispute"] is not None, "t-task5 was disputed and ruled"
        dispute = data["dispute"]
        assert "claim_id" in dispute
        assert "reason" in dispute

    async def test_returns_ruling_for_ruled_task(self, client: httpx.AsyncClient) -> None:
        """Response must include ruling data for ruled tasks."""
        resp = await client.get("/api/tasks/t-task5")
        assert resp.status_code == 200
        data = resp.json()
        dispute = data.get("dispute")
        assert dispute is not None
        assert "ruling" in dispute
        ruling = dispute["ruling"]
        assert ruling is not None, "t-task5 has a ruling"
        assert "worker_pct" in ruling
        assert "summary" in ruling
        assert ruling["worker_pct"] == 70

    async def test_404_for_nonexistent_task(self, client: httpx.AsyncClient) -> None:
        """Requesting a non-existent task_id should return 404."""
        resp = await client.get("/api/tasks/t-does-not-exist")
        assert resp.status_code == 404

    async def test_timestamps_present(self, client: httpx.AsyncClient) -> None:
        """Response must include lifecycle timestamps."""
        resp = await client.get("/api/tasks/t-task1")
        assert resp.status_code == 200
        data = resp.json()
        assert "timestamps" in data
        ts = data["timestamps"]
        assert "created_at" in ts
        assert ts["created_at"] is not None
```

---

## Phase 5: Sparklines Using Real GDP History (agent-economy-5jy)

### File: `services/ui/tests/integration/test_sparkline_data.py`

```python
"""Acceptance tests for sparkline data from real API history.

These tests verify that API history endpoints provide enough data
points in the right format for sparkline rendering, replacing the
current random data generation.

Ticket: agent-economy-5jy
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestSparklineDataFromHistory:
    """API history endpoints must provide data suitable for sparkline rendering."""

    async def test_gdp_history_returns_data_points(self, client: httpx.AsyncClient) -> None:
        """GET /api/metrics/gdp/history should return data points for sparklines."""
        resp = await client.get("/api/metrics/gdp/history", params={"window": "24h", "resolution": "1h"})
        assert resp.status_code == 200
        data = resp.json()
        assert "data_points" in data
        assert isinstance(data["data_points"], list)

    async def test_gdp_history_has_sufficient_points(self, client: httpx.AsyncClient) -> None:
        """History should have at least 12 data points for meaningful sparklines."""
        resp = await client.get("/api/metrics/gdp/history", params={"window": "24h", "resolution": "1h"})
        assert resp.status_code == 200
        points = resp.json()["data_points"]
        assert len(points) >= 12, (
            f"Sparklines need at least 12 data points, got {len(points)}"
        )

    async def test_gdp_history_points_have_numeric_values(self, client: httpx.AsyncClient) -> None:
        """Each data point must have a numeric value field for bar chart heights."""
        resp = await client.get("/api/metrics/gdp/history", params={"window": "24h", "resolution": "1h"})
        assert resp.status_code == 200
        points = resp.json()["data_points"]
        if len(points) == 0:
            pytest.skip("No data points available")
        for point in points:
            assert "gdp" in point or "value" in point, (
                f"Data point must have 'gdp' or 'value' field, got keys: {list(point.keys())}"
            )
            value = point.get("gdp") or point.get("value")
            assert isinstance(value, (int, float)), (
                f"Sparkline value must be numeric, got {type(value)}"
            )

    async def test_gdp_history_points_have_timestamps(self, client: httpx.AsyncClient) -> None:
        """Each data point must have a timestamp for x-axis positioning."""
        resp = await client.get("/api/metrics/gdp/history", params={"window": "24h", "resolution": "1h"})
        assert resp.status_code == 200
        points = resp.json()["data_points"]
        if len(points) == 0:
            pytest.skip("No data points available")
        for point in points:
            assert "timestamp" in point, "Each data point must have a timestamp"

    async def test_7d_window_provides_more_points(self, client: httpx.AsyncClient) -> None:
        """7d window with 1h resolution should provide up to 168 data points."""
        resp = await client.get("/api/metrics/gdp/history", params={"window": "7d", "resolution": "1h"})
        assert resp.status_code == 200
        points = resp.json()["data_points"]
        # Should have more points than 24h window
        assert len(points) >= 12, "7d window should provide at least 12 data points"

    async def test_1h_window_with_1m_resolution(self, client: httpx.AsyncClient) -> None:
        """1h window with 1m resolution should provide fine-grained data."""
        resp = await client.get("/api/metrics/gdp/history", params={"window": "1h", "resolution": "1m"})
        assert resp.status_code == 200
        data = resp.json()
        assert "data_points" in data
        assert isinstance(data["data_points"], list)
```

---

## Phase 6: Code Quality Verification

Run code quality checks (NOT tests — the tests are expected to fail):

```bash
cd services/ui && just code-format
cd services/ui && just code-style
cd services/ui && just code-spell
```

Fix any issues found. Common problems:
- Line length > 100: break long assertion messages across lines
- Import ordering: run `just code-format` to fix
- Spelling: add words to `../../config/codespell/ignore.txt` if needed

After fixing:
```bash
cd services/ui && just code-format && just code-style && just code-spell
```

All three must pass cleanly.

Then verify the tests exist and fail as expected:
```bash
cd services/ui && uv run pytest tests/integration/test_metrics_delta.py tests/integration/test_ticker_deltas.py tests/integration/test_task_list.py tests/integration/test_task_drilldown_contract.py tests/integration/test_sparkline_data.py -v --timeout=30 2>&1 | tail -20
```

Expected: Multiple test failures (features not implemented yet). This is correct — we want them to fail.

---

## Summary of Files Created

| File | Ticket | Purpose |
|------|--------|---------|
| `services/ui/tests/integration/test_metrics_delta.py` | d9b | Tests for delta fields in GET /api/metrics |
| `services/ui/tests/integration/test_ticker_deltas.py` | bxt | Tests for ticker/board real delta consumption |
| `services/ui/tests/integration/test_task_list.py` | z3y | Tests for GET /api/tasks general listing |
| `services/ui/tests/integration/test_task_drilldown_contract.py` | efw | Tests for task page API contract completeness |
| `services/ui/tests/integration/test_sparkline_data.py` | 5jy | Tests for sparkline-ready history data |
