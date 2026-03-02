# Implementation Plan: Unit Tests for Unemployment Rate Calculation

**Ticket:** agent-economy-mrs
**Date:** 2026-03-02

## Overview

The existing tests for `unemployment_rate` only verify response structure (key exists, 24 values, range 0-1). We need tests that verify the **actual calculation logic** by seeding specific event patterns and asserting exact unemployment values.

The function `compute_sparkline_history()` in `services/ui/src/ui_service/services/metrics.py` computes unemployment as:
```
unemployment = (registered - working) / registered
```
Where `working` is tracked via cumulative deltas: `+1` on `task.accepted`, `-1` on `task.approved`/`task.auto_approved`/`task.disputed`.

## CRITICAL RULES

- Do NOT use git. This project has no git repository.
- Do NOT modify any existing test files. Only create NEW test files.
- Use `uv run` for all Python execution. Never use `python` or `python3` directly.

## Test Strategy

We create a NEW integration test file that uses the existing `write_db` and `client` fixtures from `services/ui/tests/integration/conftest.py`. The `write_db` fixture gives us a writable aiosqlite connection to the seeded test database. We insert specific events with controlled timestamps, then call the `/api/metrics/sparklines` endpoint and assert exact values.

**Key challenge:** `compute_sparkline_history()` uses `utc_now()` to determine the 24-hour window. The seeded database has events from January-March 2026. Events outside the 24h window only affect baselines. We need to either:
1. Freeze time (monkeypatch `utc_now`), or
2. Insert events with timestamps relative to "now"

Approach: **Freeze time** using `monkeypatch` on `ui_service.services.metrics.utc_now`. Set it to a known time and seed events relative to that.

## File: `services/ui/tests/integration/test_unemployment_calculation.py` (NEW)

Create this file with the exact content below.

```python
"""Tests for unemployment_rate sparkline calculation logic.

Verifies the actual computation, not just response structure.
Seeds specific event patterns and asserts exact unemployment values.

Ticket: agent-economy-mrs
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    import aiosqlite
    import httpx

# Fixed reference time for all tests — 2026-03-02T06:30:00Z
# All seed events in helpers.py are before this, so they fall in baseline or in-window.
REF_NOW = datetime(2026, 3, 2, 6, 30, 0, tzinfo=timezone.utc)


def _bucket(hours_ago: int) -> str:
    """Return the ISO bucket key for N hours before REF_NOW."""
    t = REF_NOW - timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%dT%H")


def _iso(hours_ago: int, minutes: int = 0) -> str:
    """Return full ISO timestamp for N hours + M minutes before REF_NOW."""
    t = REF_NOW - timedelta(hours=hours_ago, minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _insert_event(
    db: aiosqlite.Connection,
    event_type: str,
    timestamp: str,
    agent_id: str = "a-alice",
    task_id: str | None = None,
    payload: dict | None = None,
) -> None:
    """Insert a single event into the events table."""
    await db.execute(
        "INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "board",
            event_type,
            timestamp,
            task_id,
            agent_id,
            f"Test event {event_type}",
            json.dumps(payload or {}),
        ),
    )
    await db.commit()


async def _clear_events(db: aiosqlite.Connection) -> None:
    """Remove all events so we have full control over seed data."""
    await db.execute("DELETE FROM events")
    await db.commit()


async def _seed_registrations(db: aiosqlite.Connection, count: int, hours_ago: int = 48) -> None:
    """Seed N agent.registered events well before the 24h window (baseline)."""
    for i in range(count):
        await db.execute(
            "INSERT INTO events (event_source, event_type, timestamp, agent_id, summary, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "identity",
                "agent.registered",
                _iso(hours_ago, minutes=i),
                f"a-test-{i}",
                f"Test agent {i} registered",
                json.dumps({"agent_name": f"Agent{i}"}),
            ),
        )
    await db.commit()


def _get_unemployment(data: dict, bucket_index: int) -> float:
    """Extract unemployment_rate at a specific bucket index."""
    return data["metrics"]["unemployment_rate"][bucket_index]


@pytest.mark.integration
class TestUnemploymentCalculation:
    """Verify the unemployment_rate sparkline calculation with controlled data."""

    @patch("ui_service.services.metrics.utc_now", return_value=REF_NOW)
    async def test_no_workers_full_unemployment(
        self,
        _mock_now: object,
        client: httpx.AsyncClient,
        write_db: aiosqlite.Connection,
    ) -> None:
        """When no agents are working, unemployment should be 1.0 (100%).

        Setup: 5 registered agents (baseline), no task.accepted events.
        Expected: all buckets have unemployment_rate = 1.0
        """
        await _clear_events(write_db)
        await _seed_registrations(write_db, count=5, hours_ago=48)

        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200
        data = resp.json()
        series = data["metrics"]["unemployment_rate"]

        # All 24 buckets: 5 registered, 0 working => (5-0)/5 = 1.0
        for i, val in enumerate(series):
            assert val == 1.0, f"bucket {i}: expected 1.0, got {val}"

    @patch("ui_service.services.metrics.utc_now", return_value=REF_NOW)
    async def test_all_agents_working_zero_unemployment(
        self,
        _mock_now: object,
        client: httpx.AsyncClient,
        write_db: aiosqlite.Connection,
    ) -> None:
        """When all agents are working, unemployment should be 0.0.

        Setup: 3 registered agents (baseline), 3 task.accepted before window.
        Expected: all buckets have unemployment_rate = 0.0
        """
        await _clear_events(write_db)
        await _seed_registrations(write_db, count=3, hours_ago=48)

        # All 3 agents accepted tasks before the 24h window (baseline working = 3)
        for i in range(3):
            await _insert_event(
                write_db,
                "task.accepted",
                _iso(30, minutes=i),  # 30 hours ago = before 24h window
                agent_id=f"a-test-{i}",
                task_id=f"t-test-{i}",
                payload={"worker_id": f"a-test-{i}", "worker_name": f"Agent{i}"},
            )

        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200
        data = resp.json()
        series = data["metrics"]["unemployment_rate"]

        # All 24 buckets: 3 registered, 3 working => (3-3)/3 = 0.0
        for i, val in enumerate(series):
            assert val == 0.0, f"bucket {i}: expected 0.0, got {val}"

    @patch("ui_service.services.metrics.utc_now", return_value=REF_NOW)
    async def test_mixed_state_known_values(
        self,
        _mock_now: object,
        client: httpx.AsyncClient,
        write_db: aiosqlite.Connection,
    ) -> None:
        """2 of 5 agents working => unemployment 0.6.

        Setup: 5 registered agents (baseline), 2 task.accepted before window.
        Expected: unemployment = (5-2)/5 = 0.6
        """
        await _clear_events(write_db)
        await _seed_registrations(write_db, count=5, hours_ago=48)

        # 2 agents accepted tasks before window
        for i in range(2):
            await _insert_event(
                write_db,
                "task.accepted",
                _iso(30, minutes=i),
                agent_id=f"a-test-{i}",
                task_id=f"t-test-{i}",
                payload={"worker_id": f"a-test-{i}", "worker_name": f"Agent{i}"},
            )

        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200
        data = resp.json()
        series = data["metrics"]["unemployment_rate"]

        for i, val in enumerate(series):
            assert val == 0.6, f"bucket {i}: expected 0.6, got {val}"

    @patch("ui_service.services.metrics.utc_now", return_value=REF_NOW)
    async def test_delta_within_window_agent_starts_working(
        self,
        _mock_now: object,
        client: httpx.AsyncClient,
        write_db: aiosqlite.Connection,
    ) -> None:
        """Agent starts working mid-window, unemployment drops.

        Setup: 4 registered agents (baseline), 0 working at start.
        At bucket hour -12: 1 agent starts working.
        Expected: buckets 0-11 = 1.0, buckets 12-23 = (4-1)/4 = 0.75
        """
        await _clear_events(write_db)
        await _seed_registrations(write_db, count=4, hours_ago=48)

        # Agent starts working 12 hours ago (bucket index 12 in a 24-bucket window)
        await _insert_event(
            write_db,
            "task.accepted",
            _iso(12, minutes=30),  # 12h30m ago => falls in bucket[-12]
            agent_id="a-test-0",
            task_id="t-mid-window",
            payload={"worker_id": "a-test-0", "worker_name": "Agent0"},
        )

        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200
        data = resp.json()
        series = data["metrics"]["unemployment_rate"]

        # First 12 buckets (hours -24 to -13): 4 reg, 0 working => 1.0
        for i in range(12):
            assert series[i] == 1.0, f"bucket {i}: expected 1.0, got {series[i]}"

        # Last 12 buckets (hours -12 to -1): 4 reg, 1 working => 0.75
        for i in range(12, 24):
            assert series[i] == 0.75, f"bucket {i}: expected 0.75, got {series[i]}"

    @patch("ui_service.services.metrics.utc_now", return_value=REF_NOW)
    async def test_delta_agent_stops_working_approved(
        self,
        _mock_now: object,
        client: httpx.AsyncClient,
        write_db: aiosqlite.Connection,
    ) -> None:
        """Agent finishes task mid-window (approved), unemployment rises.

        Setup: 2 registered agents, 1 working (baseline).
        At bucket hour -6: task.approved => agent stops working.
        Expected: buckets 0-17 = 0.5, buckets 18-23 = 1.0
        """
        await _clear_events(write_db)
        await _seed_registrations(write_db, count=2, hours_ago=48)

        # 1 agent working before window
        await _insert_event(
            write_db,
            "task.accepted",
            _iso(30),
            agent_id="a-test-0",
            task_id="t-baseline",
            payload={"worker_id": "a-test-0", "worker_name": "Agent0"},
        )

        # Task approved 6 hours ago => agent stops working
        await _insert_event(
            write_db,
            "task.approved",
            _iso(6, minutes=30),
            agent_id="a-test-0",
            task_id="t-baseline",
            payload={"reward": 100},
        )

        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200
        data = resp.json()
        series = data["metrics"]["unemployment_rate"]

        # First 18 buckets (hours -24 to -7): 2 reg, 1 working => 0.5
        for i in range(18):
            assert series[i] == 0.5, f"bucket {i}: expected 0.5, got {series[i]}"

        # Last 6 buckets (hours -6 to -1): 2 reg, 0 working => 1.0
        for i in range(18, 24):
            assert series[i] == 1.0, f"bucket {i}: expected 1.0, got {series[i]}"

    @patch("ui_service.services.metrics.utc_now", return_value=REF_NOW)
    async def test_delta_agent_stops_working_disputed(
        self,
        _mock_now: object,
        client: httpx.AsyncClient,
        write_db: aiosqlite.Connection,
    ) -> None:
        """Agent stops working via dispute, same effect as approval.

        Setup: 2 registered agents, 1 working (baseline).
        At bucket hour -3: task.disputed => agent stops working.
        Expected: buckets 0-20 = 0.5, buckets 21-23 = 1.0
        """
        await _clear_events(write_db)
        await _seed_registrations(write_db, count=2, hours_ago=48)

        await _insert_event(
            write_db,
            "task.accepted",
            _iso(30),
            agent_id="a-test-0",
            task_id="t-dispute",
            payload={"worker_id": "a-test-0", "worker_name": "Agent0"},
        )

        await _insert_event(
            write_db,
            "task.disputed",
            _iso(3, minutes=30),
            agent_id="a-test-0",
            task_id="t-dispute",
            payload={"reason": "Incomplete"},
        )

        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200
        data = resp.json()
        series = data["metrics"]["unemployment_rate"]

        # First 21 buckets: 2 reg, 1 working => 0.5
        for i in range(21):
            assert series[i] == 0.5, f"bucket {i}: expected 0.5, got {series[i]}"

        # Last 3 buckets: 2 reg, 0 working => 1.0
        for i in range(21, 24):
            assert series[i] == 1.0, f"bucket {i}: expected 1.0, got {series[i]}"

    @patch("ui_service.services.metrics.utc_now", return_value=REF_NOW)
    async def test_multiple_transitions_within_window(
        self,
        _mock_now: object,
        client: httpx.AsyncClient,
        write_db: aiosqlite.Connection,
    ) -> None:
        """Agent starts, finishes, starts again — verify cumulative tracking.

        Setup: 3 registered agents, 0 working at start.
        Hour -20: agent A starts working (unemp 0.667)
        Hour -10: agent A finishes (unemp 1.0)
        Hour -5: agent B starts working (unemp 0.667)
        """
        await _clear_events(write_db)
        await _seed_registrations(write_db, count=3, hours_ago=48)

        # Agent A starts working 20h ago
        await _insert_event(
            write_db,
            "task.accepted",
            _iso(20, minutes=30),
            agent_id="a-test-0",
            task_id="t-first",
            payload={"worker_id": "a-test-0", "worker_name": "Agent0"},
        )
        # Agent A finishes 10h ago
        await _insert_event(
            write_db,
            "task.approved",
            _iso(10, minutes=30),
            agent_id="a-test-0",
            task_id="t-first",
            payload={"reward": 50},
        )
        # Agent B starts working 5h ago
        await _insert_event(
            write_db,
            "task.accepted",
            _iso(5, minutes=30),
            agent_id="a-test-1",
            task_id="t-second",
            payload={"worker_id": "a-test-1", "worker_name": "Agent1"},
        )

        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200
        data = resp.json()
        series = data["metrics"]["unemployment_rate"]

        # Buckets 0-3 (hours -24 to -21): 0 working, 3 reg => 1.0
        for i in range(4):
            assert series[i] == 1.0, f"bucket {i}: expected 1.0, got {series[i]}"

        # Buckets 4-13 (hours -20 to -11): 1 working, 3 reg => 0.667
        for i in range(4, 14):
            assert series[i] == 0.667, f"bucket {i}: expected 0.667, got {series[i]}"

        # Buckets 14-18 (hours -10 to -6): 0 working, 3 reg => 1.0
        for i in range(14, 19):
            assert series[i] == 1.0, f"bucket {i}: expected 1.0, got {series[i]}"

        # Buckets 19-23 (hours -5 to -1): 1 working, 3 reg => 0.667
        for i in range(19, 24):
            assert series[i] == 0.667, f"bucket {i}: expected 0.667, got {series[i]}"

    @patch("ui_service.services.metrics.utc_now", return_value=REF_NOW)
    async def test_zero_registered_agents_returns_zero(
        self,
        _mock_now: object,
        client: httpx.AsyncClient,
        write_db: aiosqlite.Connection,
    ) -> None:
        """When no agents are registered, unemployment should be 0.0 (not division error).

        Setup: 0 registered agents, 0 events.
        Expected: all buckets = 0.0 (guard clause: reg == 0 => rate = 0.0)
        """
        await _clear_events(write_db)
        # No registrations

        resp = await client.get("/api/metrics/sparklines")
        assert resp.status_code == 200
        data = resp.json()
        series = data["metrics"]["unemployment_rate"]

        for i, val in enumerate(series):
            assert val == 0.0, f"bucket {i}: expected 0.0, got {val}"
```

## Verification

Run these commands in order:

### Step 1: Verify new tests pass

```bash
cd services/ui && just test-integration
```

Expected: All tests pass, including the 8 new tests in `test_unemployment_calculation.py`, the 4 existing unemployment structure tests in `test_sparklines_unemployment.py`, and the 11 original sparkline tests.

### Step 2: Full CI

```bash
cd services/ui && just ci-quiet
```

Expected: ALL CI checks pass (format, style, typecheck, security, spell, semgrep, audit, tests, pyright).

### Step 3: Project-wide CI

```bash
just ci-all-quiet
```

Expected: ALL services pass.

## Summary

| # | File | Action | Tests |
|---|------|--------|-------|
| 1 | `services/ui/tests/integration/test_unemployment_calculation.py` | CREATE | 8 tests |

### Test Cases:

1. **No workers = 100% unemployment** — 5 agents registered, 0 accepted tasks, all buckets = 1.0
2. **All agents working = 0% unemployment** — 3 agents, 3 accepted (baseline), all buckets = 0.0
3. **Mixed state (2/5 working)** — expected 0.6 across all buckets
4. **Agent starts working mid-window** — unemployment drops from 1.0 to 0.75 at specific bucket
5. **Agent stops working (approved)** — unemployment rises from 0.5 to 1.0 at specific bucket
6. **Agent stops working (disputed)** — same effect as approval, verifies disputed is a stop event
7. **Multiple transitions** — start, stop, start again — verifies cumulative delta tracking
8. **Zero registered agents** — verifies no division-by-zero, returns 0.0
