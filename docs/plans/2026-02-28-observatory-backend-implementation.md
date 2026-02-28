# Observatory Backend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement all Observatory service backend endpoints, database layer, schemas, SSE streaming, and test suite so that `just ci-quiet` passes all 11 checks.

**Architecture:** Three-layer read-only service: Routers (thin HTTP handlers) → Services (SQL queries + business logic) → Database (aiosqlite wrapper). All data is read from a shared SQLite database opened with `?mode=ro`. No caching, no authentication, no writes.

**Tech Stack:** FastAPI, aiosqlite, sse-starlette, Pydantic v2, pytest + httpx for testing

**Working directory:** `cd /Users/ryanzidago/Projects/agent-economy-group/agent-economy/.claude/worktrees/observatory-backend/services/observatory`

**Key rules:**
- Python via `uv run ...` — never `python` or `python3` directly
- No default parameter values in `src/` code — all config from `config.yaml`
- Read-only database: `?mode=ro`
- No Co-Authored-By: Claude lines in commits
- `just ci-quiet` is the only validation that matters

**Reference files:**
- API spec: `docs/specifications/service-api/observatory-service-specs.md`
- Test spec: `docs/specifications/service-tests/observatory-service-tests.md`
- Schema: `docs/specifications/schema.sql`

---

## Task 1: Database Layer

**Files:**
- Modify: `src/observatory_service/core/state.py`
- Modify: `src/observatory_service/core/lifespan.py`
- Modify: `src/observatory_service/services/database.py`
- Test: `tests/unit/test_database.py` (create)

### Step 1: Write failing test for database connection

Create `tests/unit/test_database.py`:

```python
"""Tests for database connection and query helpers."""

import aiosqlite
import pytest

from observatory_service.services.database import execute_query, execute_query_one


@pytest.mark.unit
async def test_execute_query_returns_rows(tmp_path):
    """execute_query returns list of Row objects."""
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        await db.execute("INSERT INTO t VALUES (1, 'alice')")
        await db.commit()
        rows = await execute_query(db, "SELECT * FROM t")
    assert len(rows) == 1
    assert rows[0]["name"] == "alice"


@pytest.mark.unit
async def test_execute_query_one_returns_single_row(tmp_path):
    """execute_query_one returns a single Row or None."""
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE t (id INTEGER)")
        await db.execute("INSERT INTO t VALUES (1)")
        await db.commit()
        row = await execute_query_one(db, "SELECT * FROM t WHERE id = 1")
    assert row is not None
    assert row["id"] == 1


@pytest.mark.unit
async def test_execute_query_one_returns_none_when_missing(tmp_path):
    """execute_query_one returns None when no row matches."""
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE t (id INTEGER)")
        await db.commit()
        row = await execute_query_one(db, "SELECT * FROM t WHERE id = 999")
    assert row is None
```

### Step 2: Run test to verify it fails

```bash
uv run pytest tests/unit/test_database.py -v
```
Expected: FAIL — `execute_query` and `execute_query_one` don't exist yet.

### Step 3: Implement database.py

Replace `src/observatory_service/services/database.py`:

```python
"""Read-only database access via aiosqlite."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    import aiosqlite


async def execute_query(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[aiosqlite.Row]:
    """Execute a read-only query and return all rows."""
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(sql, params)
    return await cursor.fetchall()


async def execute_query_one(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> aiosqlite.Row | None:
    """Execute a read-only query and return first row or None."""
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(sql, params)
    return await cursor.fetchone()
```

### Step 4: Run tests to verify they pass

```bash
uv run pytest tests/unit/test_database.py -v
```
Expected: PASS

### Step 5: Update state.py to hold DB connection

Replace `src/observatory_service/core/state.py`:

```python
"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    db: aiosqlite.Connection | None = field(default=None, repr=False)

    @property
    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def started_at(self) -> str:
        """ISO format start time."""
        return self.start_time.isoformat(timespec="seconds").replace("+00:00", "Z")


# Global application state instance
_state_container: dict[str, AppState | None] = {"app_state": None}


def get_app_state() -> AppState:
    """Get the current application state."""
    app_state = _state_container["app_state"]
    if app_state is None:
        msg = "Application state not initialized"
        raise RuntimeError(msg)
    return app_state


def init_app_state() -> AppState:
    """Initialize application state. Called during startup."""
    app_state = AppState()
    _state_container["app_state"] = app_state
    return app_state


def reset_app_state() -> None:
    """Reset application state. Used in testing."""
    _state_container["app_state"] = None
```

### Step 6: Update lifespan.py to open/close DB

Replace `src/observatory_service/core/lifespan.py`:

```python
"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import aiosqlite

from observatory_service.config import get_settings
from observatory_service.core.state import init_app_state
from observatory_service.logging import get_logger, setup_logging

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # === STARTUP ===
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name)
    logger = get_logger(__name__)

    state = init_app_state()

    # Open read-only database connection
    db_uri = f"file:{settings.database.path}?mode=ro"
    try:
        db = await aiosqlite.connect(db_uri, uri=True)
        db.row_factory = aiosqlite.Row
        state.db = db
        logger.info("Database connection opened", extra={"path": settings.database.path})
    except Exception:
        logger.warning(
            "Database not available at startup",
            extra={"path": settings.database.path},
        )

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    if state.db is not None:
        await state.db.close()
        logger.info("Database connection closed")
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
```

### Step 7: Run all tests

```bash
uv run pytest tests/ -v
```
Expected: All pass (existing + new database tests).

### Step 8: Commit

```bash
git add -A
git commit -m "feat: add database layer with read-only aiosqlite wrapper"
```

---

## Task 2: Shared Test Fixtures (Standard Economy Seed Data)

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/unit/routers/conftest.py`

This task creates the shared "standard economy" seed fixture used by most tests. The fixture creates an in-memory SQLite database, runs schema.sql to create tables, and inserts the standard test data (3 agents, 5 tasks, bids, feedback, transactions, events, court records).

### Step 1: Create the standard economy fixture

Update `tests/conftest.py` with the full schema and seed data fixture. This fixture:
- Creates a temporary SQLite file
- Runs the full schema.sql
- Inserts: 3 agents (Alice, Bob, Charlie), 5 tasks with appropriate statuses, 7 bids, escrow records, bank accounts/transactions, 4 visible feedback + 2 sealed, 15 events, 1 court claim with rebuttal and ruling

Timestamps should use `datetime.now(UTC)` offset by relative amounts (e.g., "2h ago") so tests work regardless of when they run.

### Step 2: Update router conftest to use seeded DB

Update `tests/unit/routers/conftest.py` so the `app` fixture opens the seeded database (from the shared fixture) and passes its path into the config. The lifespan opens the DB connection; routers read from it.

### Step 3: Run existing tests to confirm nothing breaks

```bash
uv run pytest tests/ -v
```
Expected: All existing tests still pass.

### Step 4: Commit

```bash
git add -A
git commit -m "feat: add standard economy seed data fixture for tests"
```

---

## Task 3: Health Endpoint (Database-Aware)

**Files:**
- Modify: `src/observatory_service/routers/health.py`
- Modify: `tests/unit/routers/test_health.py`

### Step 1: Write failing tests for HEALTH-01 and HEALTH-02

Add to `tests/unit/routers/test_health.py`:

```python
@pytest.mark.unit
async def test_health_database_readable(seeded_client):
    """HEALTH-01: Health check with database reports database_readable=true."""
    response = await seeded_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["database_readable"] is True
    assert data["latest_event_id"] >= 0

@pytest.mark.unit
async def test_health_reports_latest_event_id(seeded_client):
    """HEALTH-02: latest_event_id matches highest event_id in seeded data."""
    response = await seeded_client.get("/health")
    data = response.json()
    assert data["latest_event_id"] == 15
```

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/unit/routers/test_health.py -v
```
Expected: FAIL — health endpoint currently returns `latest_event_id=0` and `database_readable=False`.

### Step 3: Implement database-aware health check

Update `src/observatory_service/routers/health.py` to query `SELECT MAX(event_id) FROM events` and check database readability.

### Step 4: Run tests

```bash
uv run pytest tests/unit/routers/test_health.py -v
```
Expected: PASS

### Step 5: Commit

```bash
git add -A
git commit -m "feat: health endpoint checks database and reports latest_event_id"
```

---

## Task 4: Pydantic Schemas

**Files:**
- Modify: `src/observatory_service/schemas.py`

### Step 1: Add all response models

Add to `src/observatory_service/schemas.py` all the Pydantic models needed for every endpoint:

- `GDPMetrics`, `AgentMetrics`, `TaskMetrics`, `EscrowMetrics`, `SpecQualityMetrics`, `LaborMarketMetrics`, `RewardDistribution`, `EconomyPhaseMetrics`, `MetricsResponse`
- `GDPDataPoint`, `GDPHistoryResponse`
- `AgentRef`, `AgentStats`, `SpecQualityStats`, `DeliveryQualityStats`
- `AgentListItem`, `AgentListResponse`
- `RecentTask`, `FeedbackItem`, `AgentProfileResponse`
- `BidderInfo`, `BidItem`, `AssetItem`, `FeedbackDetail`, `DisputeRebuttal`, `DisputeRuling`, `DisputeInfo`, `TaskDeadlines`, `TaskTimestamps`, `TaskDrilldownResponse`
- `CompetitiveTaskItem`, `CompetitiveTasksResponse`
- `UncontestedTaskItem`, `UncontestedTasksResponse`
- `EventItem`, `EventsResponse`
- `QuarterlyGDP`, `QuarterlyTasks`, `QuarterlyLaborMarket`, `QuarterlySpecQuality`, `QuarterlyAgents`, `NotableTask`, `NotableAgent`, `QuarterlyNotable`, `QuarterlyPeriod`, `QuarterlyReportResponse`

All models must use `ConfigDict(extra="forbid")`. Fields must match the API spec exactly.

### Step 2: Verify types compile

```bash
uv run mypy src/observatory_service/schemas.py
```
Expected: PASS

### Step 3: Commit

```bash
git add -A
git commit -m "feat: add all Pydantic response schemas"
```

---

## Task 5: Metrics Service + Router

**Files:**
- Create: `tests/unit/routers/test_metrics.py`
- Modify: `src/observatory_service/services/metrics.py`
- Modify: `src/observatory_service/routers/metrics.py`

### Step 1: Write failing tests for MET-01 through MET-13

Create `tests/unit/routers/test_metrics.py` with all 13 metrics test cases from the test spec:

- MET-01: All required fields present
- MET-02: GDP total = 234 (100 + 50 + 84)
- MET-03: GDP per agent = total / active
- MET-04: Active agents excludes inactive (register Dave with no tasks)
- MET-05: Task status counts correct
- MET-06: Completion rate ≈ 0.667
- MET-07: Escrow total locked = 140
- MET-08: Spec quality percentages sum to 1.0
- MET-09: Spec quality only counts visible feedback
- MET-10: Labor market avg bids per task
- MET-11: Reward distribution buckets (0_to_10:0, 11_to_50:1, 51_to_100:2, over_100:2)
- MET-12: Economy phase is "stalled" when no tasks
- MET-13: Metrics on empty database (all zeros)

Also write GDP History tests (GDP-01 through GDP-05).

### Step 2: Run tests to verify they fail

```bash
uv run pytest tests/unit/routers/test_metrics.py -v
```

### Step 3: Implement metrics service

Fill in `src/observatory_service/services/metrics.py` with SQL query functions:
- `compute_gdp(db)` — approved rewards + ruled dispute worker payouts
- `compute_agents(db)` — total registered, active (last 30 days), with completed tasks
- `compute_tasks(db)` — counts by status, completion rate
- `compute_escrow(db)` — total locked
- `compute_spec_quality(db)` — from visible feedback
- `compute_labor_market(db)` — avg bids, avg reward, posting rate, acceptance latency, unemployment, reward distribution
- `compute_economy_phase(db)` — derived from task creation trend + dispute rate
- `compute_gdp_history(db, window, resolution)` — time-series GDP

### Step 4: Implement metrics router

Fill in `src/observatory_service/routers/metrics.py`:
- `GET /metrics` → calls all compute functions, returns `MetricsResponse`
- `GET /metrics/gdp/history` → validates window/resolution params, returns `GDPHistoryResponse`

### Step 5: Run tests

```bash
uv run pytest tests/unit/routers/test_metrics.py -v
```
Expected: All PASS.

### Step 6: Commit

```bash
git add -A
git commit -m "feat: implement metrics endpoints with GDP, agents, tasks, escrow, spec quality, labor market, economy phase"
```

---

## Task 6: Events Service + Router (SSE + History)

**Files:**
- Create: `tests/unit/routers/test_events.py`
- Modify: `src/observatory_service/services/events.py`
- Modify: `src/observatory_service/routers/events.py`

### Step 1: Write failing tests

Create `tests/unit/routers/test_events.py` with:
- SSE-01 through SSE-05 (stream delivery, cursor resumption, keepalive, retry field, new event appearance)
- EVT-01 through EVT-13 (reverse chronological, limit, before/after pagination, source/type/agent_id/task_id filters, combined filters, empty result, invalid limit, limit clamping, non-integer limit)

SSE tests use httpx stream mode or similar async iteration to consume SSE messages.

### Step 2: Implement events service

Fill in `src/observatory_service/services/events.py`:
- `get_events(db, limit, before, after, source, type, agent_id, task_id)` — paginated history query
- `stream_events(db, last_event_id, batch_size, poll_interval, keepalive_interval)` — async generator that yields SSE events

### Step 3: Implement events router

Fill in `src/observatory_service/routers/events.py`:
- `GET /events` → validates params, calls `get_events`, returns `EventsResponse`
- `GET /events/stream` → returns `EventSourceResponse` wrapping `stream_events` generator

### Step 4: Run tests

```bash
uv run pytest tests/unit/routers/test_events.py -v
```
Expected: All PASS.

### Step 5: Commit

```bash
git add -A
git commit -m "feat: implement events endpoints with SSE streaming and paginated history"
```

---

## Task 7: Agents Service + Router

**Files:**
- Create: `tests/unit/routers/test_agents.py`
- Modify: `src/observatory_service/services/agents.py`
- Modify: `src/observatory_service/routers/agents.py`

### Step 1: Write failing tests

Create `tests/unit/routers/test_agents.py` with:
- AGT-01 through AGT-06 (listing with stats, default sort, sort options, pagination, invalid sort_by, visible feedback only)
- PROF-01 through PROF-05 (full profile, stats accuracy, recent tasks ordering, visible feedback only, agent not found)

### Step 2: Implement agents service

Fill in `src/observatory_service/services/agents.py`:
- `get_agents(db, sort_by, order, limit, offset)` — listing with computed stats
- `get_agent_profile(db, agent_id)` — single agent with balance, recent tasks, recent feedback

Stats computed via JOINs: `identity_agents` ↔ `board_tasks` ↔ `bank_transactions` ↔ `reputation_feedback`

### Step 3: Implement agents router

Fill in `src/observatory_service/routers/agents.py`:
- `GET /agents` → validates sort_by/order params, returns `AgentListResponse`
- `GET /agents/{agent_id}` → returns `AgentProfileResponse` or raises `AGENT_NOT_FOUND`

### Step 4: Run tests

```bash
uv run pytest tests/unit/routers/test_agents.py -v
```
Expected: All PASS.

### Step 5: Commit

```bash
git add -A
git commit -m "feat: implement agent listing and profile endpoints"
```

---

## Task 8: Tasks Service + Router

**Files:**
- Create: `tests/unit/routers/test_tasks.py`
- Modify: `src/observatory_service/services/tasks.py`
- Modify: `src/observatory_service/routers/tasks.py`

### Step 1: Write failing tests

Create `tests/unit/routers/test_tasks.py` with:
- TASK-01 through TASK-09 (full lifecycle, poster/worker name resolution, bids with delivery quality, accepted bid marking, dispute data, null dispute, open task no worker, visible feedback only, task not found)
- COMP-01 through COMP-04 (sorted by bid count, default status filter, limit, empty result)
- UNCON-01 through UNCON-04 (zero bids, excludes tasks with bids, age filter, excludes non-open tasks)

### Step 2: Implement tasks service

Fill in `src/observatory_service/services/tasks.py`:
- `get_task(db, task_id)` — full drilldown with poster/worker names, bids (with bidder delivery quality), assets, visible feedback, dispute (claim + rebuttal + ruling)
- `get_competitive_tasks(db, limit, status)` — top by bid count
- `get_uncontested_tasks(db, min_age_minutes, limit)` — zero bids, open status, age filter

### Step 3: Implement tasks router

Fill in `src/observatory_service/routers/tasks.py`:
- `GET /tasks/{task_id}` → returns `TaskDrilldownResponse` or raises `TASK_NOT_FOUND`
- `GET /tasks/-/competitive` → returns `CompetitiveTasksResponse`
- `GET /tasks/-/uncontested` → returns `UncontestedTasksResponse`

### Step 4: Run tests

```bash
uv run pytest tests/unit/routers/test_tasks.py -v
```
Expected: All PASS.

### Step 5: Commit

```bash
git add -A
git commit -m "feat: implement task drilldown, competitive, and uncontested endpoints"
```

---

## Task 9: Quarterly Report Service + Router

**Files:**
- Create: `tests/unit/routers/test_quarterly.py`
- Modify: `src/observatory_service/services/quarterly.py`
- Modify: `src/observatory_service/routers/quarterly.py`

### Step 1: Write failing tests

Create `tests/unit/routers/test_quarterly.py` with:
- QTR-01 through QTR-07 (current quarter, explicit quarter, GDP delta from previous quarter, notable tasks/agents, Q5 out of range, malformed format, no data 404)

### Step 2: Implement quarterly service

Fill in `src/observatory_service/services/quarterly.py`:
- `get_quarterly_report(db, quarter)` — validates quarter format, computes GDP for current and previous quarter, finds notable tasks/agents, returns full report
- Quarter validation: must match `YYYY-QN` where N is 1-4
- Quarter period calculation: Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec

### Step 3: Implement quarterly router

Fill in `src/observatory_service/routers/quarterly.py`:
- `GET /quarterly-report` → validates quarter param, returns `QuarterlyReportResponse` or raises `INVALID_QUARTER`/`NO_DATA`

### Step 4: Run tests

```bash
uv run pytest tests/unit/routers/test_quarterly.py -v
```
Expected: All PASS.

### Step 5: Commit

```bash
git add -A
git commit -m "feat: implement quarterly report endpoint"
```

---

## Task 10: Edge Case Tests

**Files:**
- Create: `tests/unit/test_edge_cases.py`

### Step 1: Write edge case tests

Create `tests/unit/test_edge_cases.py` with:
- EDGE-01: Empty database — all endpoints return gracefully (no 500s)
- EDGE-02: Very long task spec (10,000 chars)
- EDGE-03: Agent with no activity
- EDGE-04: Task with no bids
- EDGE-05: Unicode in agent names and task titles

### Step 2: Write read-only enforcement tests

Add to `tests/unit/test_edge_cases.py`:
- RO-01: Database connection uses read-only mode
- RO-02: All endpoints succeed with read-only connection

### Step 3: Run all tests

```bash
uv run pytest tests/ -v
```
Expected: All PASS.

### Step 4: Commit

```bash
git add -A
git commit -m "feat: add edge case and read-only enforcement tests"
```

---

## Task 11: CI Green

**Files:** Various (fix whatever breaks)

### Step 1: Run full CI

```bash
cd /Users/ryanzidago/Projects/agent-economy-group/agent-economy/.claude/worktrees/observatory-backend/services/observatory
just ci-quiet
```

### Step 2: Fix issues

Common issues:
- **Semgrep**: May flag default parameter values in src/ — remove any defaults
- **mypy/pyright**: Add proper type annotations throughout
- **ruff**: Fix import ordering, broad exception catches, unused variables
- **deptry**: Ensure all imports are from declared dependencies
- **codespell**: Add domain terms to `config/codespell/ignore.txt` (e.g., "pct", "bidder")
- **bandit**: Ensure no security issues (B101 is already skipped)

### Step 3: Re-run CI

```bash
just ci-quiet
```
Expected: All 11 checks pass.

### Step 4: Commit fixes

```bash
git add -A
git commit -m "fix: resolve CI issues (linting, types, spelling)"
```

---

## Task 12: Final Verification

### Step 1: Run full test suite with coverage

```bash
uv run pytest tests/ -v --tb=short
```
Expected: All 70+ tests pass.

### Step 2: Run CI one final time

```bash
just ci-quiet
```
Expected: All 11 checks pass with green checkmarks.

### Step 3: Push

```bash
git push -u origin observatory-backend
```
