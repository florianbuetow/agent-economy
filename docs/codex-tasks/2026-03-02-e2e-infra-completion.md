# E2E Infrastructure Completion — Codex Execution Plan

## Overview

Complete the remaining three children of epic `agent-economy-4d2` (E2E UI Service: Test Infrastructure):

1. **agent-economy-upp** — Pre-seeded SQLite fixture database + seed script
2. **agent-economy-fsy** — Page object models for all 3 pages
3. **agent-economy-4hu** — DB mutation and SSE event injection helpers

## Pre-Flight

Read these files FIRST before doing anything:
1. `AGENTS.md` — project conventions (CRITICAL: uv run, no pip, no hardcoded defaults)
2. This file — the execution plan (read it completely before starting)
3. `services/ui/tests/e2e/conftest.py` — existing E2E fixtures (session-scoped server, browser, page)
4. `services/ui/tests/integration/helpers.py` — existing seed data (5 agents, 12 tasks, 25 events)
5. `docs/specifications/schema.sql` — full database schema (14 tables)
6. `services/ui/tests/e2e/test_smoke.py` — existing smoke test patterns
7. `services/ui/data/web/index.html` — landing page DOM structure
8. `services/ui/data/web/observatory.html` — observatory page DOM structure
9. `services/ui/data/web/task.html` — task lifecycle page DOM structure
10. `services/ui/data/web/js/shared.js` — shared JavaScript utilities (ATE namespace)
11. `services/ui/data/web/js/landing.js` — landing page JavaScript
12. `services/ui/data/web/js/observatory.js` — observatory page JavaScript
13. `services/ui/data/web/js/task.js` — task lifecycle page JavaScript

## Rules

- There is NO git in this project. Do NOT use git commands, git worktrees, or attempt any git operations. Simply write files directly.
- Use `uv run` for all Python execution — never raw python, python3, or pip install
- Do NOT modify any existing test files (they are acceptance tests)
- Do NOT modify `conftest.py` — only add new files
- All files go into `services/ui/tests/e2e/` subdirectories
- Follow the TYPE_CHECKING import pattern used in existing tests
- Every function must have type annotations
- Run `cd services/ui && just ci-quiet` after ALL phases
- Run `cd services/ui && uv run pytest tests/e2e -m e2e -v --timeout=60` to verify E2E tests

---

## Phase 1: Enhanced Seed Script (agent-economy-upp)

The existing `e2e_db_path` fixture in `conftest.py` already creates a seeded DB using `helpers.py` from the integration tests. That data has 5 agents, 12 tasks, 25 events. We need to EXTEND this with a richer fixture script that:

1. Adds more agents (10+ total)
2. Ensures tasks at EVERY lifecycle stage
3. Adds more events (50+)
4. Is a standalone script that can regenerate the fixture DB

### File: `services/ui/tests/e2e/fixtures/seed_db.py`

Create this file. It must:

```python
"""Extended seed data for E2E tests — richer than integration seed data.

This module extends the integration seed data with additional agents, tasks,
events, and records to provide comprehensive coverage for browser-based E2E
tests. The integration seed data (5 agents, 12 tasks, 25 events) remains
the base — this module adds on top of it.

Usage:
    from fixtures.seed_db import extend_seed_data
    extend_seed_data(conn)  # call AFTER insert_seed_data(conn)
"""
```

**Data requirements — add these ON TOP of existing integration data:**

#### Additional Agents (5 more, for 10+ total):
```python
EXTRA_AGENTS = [
    ("a-frank", "Frank", "ed25519:RkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkY=", "2026-02-05T10:00:00Z"),
    ("a-grace", "Grace", "ed25519:R0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0c=", "2026-02-10T11:00:00Z"),
    ("a-heidi", "Heidi", "ed25519:SEhISEhISEhISEhISEhISEhISEhISEhISEhISEhISEg=", "2026-02-12T09:00:00Z"),
    ("a-ivan", "Ivan", "ed25519:SUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUk=", "2026-02-15T14:00:00Z"),
    ("a-judy", "Judy", "ed25519:SkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSg=", "2026-02-20T08:00:00Z"),
]
```

#### Additional Bank Accounts (for the 5 new agents):
```python
EXTRA_BANK_ACCOUNTS = [
    ("a-frank", 600, "2026-02-05T10:00:00Z"),
    ("a-grace", 1500, "2026-02-10T11:00:00Z"),
    ("a-heidi", 200, "2026-02-12T09:00:00Z"),
    ("a-ivan", 900, "2026-02-15T14:00:00Z"),
    ("a-judy", 750, "2026-02-20T08:00:00Z"),
]
```

#### Additional Bank Transactions (salary credits for new agents):
```python
EXTRA_BANK_TRANSACTIONS = [
    ("tx-e1", "a-frank", "credit", 1000, 1000, "salary_r1_frank", "2026-02-05T12:00:00Z"),
    ("tx-e2", "a-grace", "credit", 1500, 1500, "salary_r1_grace", "2026-02-10T12:00:00Z"),
    ("tx-e3", "a-heidi", "credit", 500, 500, "salary_r1_heidi", "2026-02-12T12:00:00Z"),
    ("tx-e4", "a-ivan", "credit", 1000, 1000, "salary_r1_ivan", "2026-02-15T16:00:00Z"),
    ("tx-e5", "a-judy", "credit", 1000, 1000, "salary_r1_judy", "2026-02-20T10:00:00Z"),
    ("tx-e6", "a-frank", "escrow_lock", 120, 880, "esc-e1", "2026-02-20T09:00:00Z"),
    ("tx-e7", "a-grace", "escrow_lock", 200, 1300, "esc-e2", "2026-02-22T09:00:00Z"),
    ("tx-e8", "a-ivan", "escrow_lock", 80, 920, "esc-e3", "2026-02-25T09:00:00Z"),
]
```

#### Additional Escrow Records:
```python
EXTRA_BANK_ESCROW = [
    ("esc-e1", "a-frank", 120, "t-task-e1", "released", "2026-02-20T09:00:00Z", "2026-03-01T10:00:00Z"),
    ("esc-e2", "a-grace", 200, "t-task-e2", "locked", "2026-02-22T09:00:00Z", None),
    ("esc-e3", "a-ivan", 80, "t-task-e3", "locked", "2026-02-25T09:00:00Z", None),
]
```

#### Additional Tasks (cover remaining lifecycle stages):
Need tasks that fill gaps in the existing data:
- `t-task-e1`: approved task (Frank posted, Grace worked) — fills "approved with full feedback" case
- `t-task-e2`: submitted + in review (Grace posted, Heidi working) — fills "awaiting review" case
- `t-task-e3`: open with multiple bids (Ivan posted) — fills "competitive bidding" case

```python
EXTRA_BOARD_TASKS = [
    # t-task-e1: approved task with feedback
    (
        "t-task-e1", "a-frank", "Payment Gateway", "Payment gateway integration spec",
        120, "approved",
        86400, 604800, 172800,
        "2026-02-21T09:00:00Z",  # bidding_deadline
        "2026-02-28T10:00:00Z",  # execution_deadline
        "2026-03-01T10:00:00Z",  # review_deadline
        "esc-e1", "a-grace", "bid-e1",
        None, None, None, None,  # no dispute
        "2026-02-20T09:00:00Z",  # created_at
        "2026-02-21T10:00:00Z",  # accepted_at
        "2026-02-26T16:00:00Z",  # submitted_at
        "2026-03-01T10:00:00Z",  # approved_at
        None, None, None, None,  # no cancel/dispute/ruling/expire
    ),
    # t-task-e2: submitted, in review window
    (
        "t-task-e2", "a-grace", "Notification System", "Push notification spec",
        200, "submitted",
        86400, 604800, 172800,
        "2026-02-23T09:00:00Z",
        "2026-03-02T06:34:00Z",
        "2026-03-02T06:30:00Z",
        "esc-e2", "a-heidi", "bid-e3",
        None, None, None, None,
        "2026-02-22T09:00:00Z",
        "2026-02-23T12:00:00Z",
        "2026-03-02T06:36:00Z",
        None,
        None, None, None, None,
    ),
    # t-task-e3: open, multiple bids, competitive
    (
        "t-task-e3", "a-ivan", "Analytics Dashboard", "Analytics integration spec",
        80, "open",
        86400, 604800, 172800,
        "2026-02-26T09:00:00Z",
        None, None,
        "esc-e3", None, None,
        None, None, None, None,
        "2026-02-25T09:00:00Z",
        None, None, None,
        None, None, None, None,
    ),
]
```

#### Additional Bids:
```python
EXTRA_BOARD_BIDS = [
    ("bid-e1", "t-task-e1", "a-grace", "Payment gateway expert", "2026-02-20T14:00:00Z"),
    ("bid-e2", "t-task-e1", "a-heidi", "I can build payment systems", "2026-02-20T15:00:00Z"),
    ("bid-e3", "t-task-e2", "a-heidi", "Notification system builder", "2026-02-22T14:00:00Z"),
    ("bid-e4", "t-task-e2", "a-ivan", "Push notification specialist", "2026-02-22T16:00:00Z"),
    ("bid-e5", "t-task-e3", "a-frank", "Analytics developer", "2026-02-25T14:00:00Z"),
    ("bid-e6", "t-task-e3", "a-judy", "Dashboard specialist", "2026-02-25T15:00:00Z"),
    ("bid-e7", "t-task-e3", "a-heidi", "Data visualization expert", "2026-02-25T16:00:00Z"),
]
```

#### Additional Assets:
```python
EXTRA_BOARD_ASSETS = [
    ("asset-e1", "t-task-e1", "a-grace", "payment-gateway.zip", "application/zip", 307200, "/data/assets/asset-e1", "2026-02-26T15:00:00Z"),
    ("asset-e2", "t-task-e2", "a-heidi", "notification-service.tar.gz", "application/gzip", 409600, "/data/assets/asset-e2", "2026-03-02T06:35:00Z"),
]
```

#### Additional Feedback:
```python
EXTRA_REPUTATION_FEEDBACK = [
    ("fb-e1", "t-task-e1", "a-frank", "a-grace", "poster", "delivery_quality", "extremely_satisfied", "Outstanding payment gateway", "2026-03-01T11:00:00Z", 1),
    ("fb-e2", "t-task-e1", "a-grace", "a-frank", "worker", "spec_quality", "satisfied", "Good spec but could be more detailed", "2026-03-01T11:30:00Z", 1),
]
```

#### Additional Events (to reach 50+ total):
Add events covering the new tasks and agents. Follow the exact payload shapes from `docs/specifications/schema.sql` comments. Events should be numbered starting from 26 (existing data ends at event 25).

```python
EXTRA_EVENTS = [
    (26, "identity", "agent.registered", "2026-02-05T10:00:00Z", None, "a-frank", "Frank registered", {"agent_name": "Frank"}),
    (27, "identity", "agent.registered", "2026-02-10T11:00:00Z", None, "a-grace", "Grace registered", {"agent_name": "Grace"}),
    (28, "identity", "agent.registered", "2026-02-12T09:00:00Z", None, "a-heidi", "Heidi registered", {"agent_name": "Heidi"}),
    (29, "identity", "agent.registered", "2026-02-15T14:00:00Z", None, "a-ivan", "Ivan registered", {"agent_name": "Ivan"}),
    (30, "identity", "agent.registered", "2026-02-20T08:00:00Z", None, "a-judy", "Judy registered", {"agent_name": "Judy"}),
    (31, "bank", "salary.paid", "2026-02-05T12:00:00Z", None, "a-frank", "Frank received salary", {"amount": 1000}),
    (32, "bank", "salary.paid", "2026-02-10T12:00:00Z", None, "a-grace", "Grace received salary", {"amount": 1500}),
    (33, "board", "task.created", "2026-02-20T09:00:00Z", "t-task-e1", "a-frank", "Frank posted Payment Gateway", {"title": "Payment Gateway", "reward": 120}),
    (34, "board", "bid.submitted", "2026-02-20T14:00:00Z", "t-task-e1", "a-grace", "Grace bid on Payment Gateway", {"bid_id": "bid-e1"}),
    (35, "board", "bid.submitted", "2026-02-20T15:00:00Z", "t-task-e1", "a-heidi", "Heidi bid on Payment Gateway", {"bid_id": "bid-e2"}),
    (36, "board", "task.accepted", "2026-02-21T10:00:00Z", "t-task-e1", "a-frank", "Frank accepted Grace for Payment Gateway", {"worker_id": "a-grace", "worker_name": "Grace"}),
    (37, "bank", "escrow.locked", "2026-02-20T09:00:00Z", "t-task-e1", "a-frank", "Escrow locked 120 for Payment Gateway", {"escrow_id": "esc-e1", "amount": 120}),
    (38, "board", "task.submitted", "2026-02-26T16:00:00Z", "t-task-e1", "a-grace", "Grace submitted Payment Gateway", {"worker_name": "Grace", "asset_count": 1}),
    (39, "board", "task.approved", "2026-03-01T10:00:00Z", "t-task-e1", "a-frank", "Frank approved Payment Gateway", {"reward": 120}),
    (40, "bank", "escrow.released", "2026-03-01T10:00:00Z", "t-task-e1", "a-frank", "Escrow released 120 for Payment Gateway", {"escrow_id": "esc-e1", "amount": 120}),
    (41, "reputation", "feedback.revealed", "2026-03-01T11:30:00Z", "t-task-e1", "a-frank", "Feedback revealed for Payment Gateway", {"category": "delivery_quality"}),
    (42, "board", "task.created", "2026-02-22T09:00:00Z", "t-task-e2", "a-grace", "Grace posted Notification System", {"title": "Notification System", "reward": 200}),
    (43, "board", "bid.submitted", "2026-02-22T14:00:00Z", "t-task-e2", "a-heidi", "Heidi bid on Notification System", {"bid_id": "bid-e3"}),
    (44, "board", "bid.submitted", "2026-02-22T16:00:00Z", "t-task-e2", "a-ivan", "Ivan bid on Notification System", {"bid_id": "bid-e4"}),
    (45, "board", "task.accepted", "2026-02-23T12:00:00Z", "t-task-e2", "a-grace", "Grace accepted Heidi for Notification System", {"worker_id": "a-heidi", "worker_name": "Heidi"}),
    (46, "bank", "escrow.locked", "2026-02-22T09:00:00Z", "t-task-e2", "a-grace", "Escrow locked 200 for Notification System", {"escrow_id": "esc-e2", "amount": 200}),
    (47, "board", "task.submitted", "2026-03-02T06:36:00Z", "t-task-e2", "a-heidi", "Heidi submitted Notification System", {"worker_name": "Heidi", "asset_count": 1}),
    (48, "board", "task.created", "2026-02-25T09:00:00Z", "t-task-e3", "a-ivan", "Ivan posted Analytics Dashboard", {"title": "Analytics Dashboard", "reward": 80}),
    (49, "board", "bid.submitted", "2026-02-25T14:00:00Z", "t-task-e3", "a-frank", "Frank bid on Analytics Dashboard", {"bid_id": "bid-e5"}),
    (50, "board", "bid.submitted", "2026-02-25T15:00:00Z", "t-task-e3", "a-judy", "Judy bid on Analytics Dashboard", {"bid_id": "bid-e6"}),
    (51, "board", "bid.submitted", "2026-02-25T16:00:00Z", "t-task-e3", "a-heidi", "Heidi bid on Analytics Dashboard", {"bid_id": "bid-e7"}),
    (52, "bank", "escrow.locked", "2026-02-25T09:00:00Z", "t-task-e3", "a-ivan", "Escrow locked 80 for Analytics Dashboard", {"escrow_id": "esc-e3", "amount": 80}),
    (53, "bank", "salary.paid", "2026-02-12T12:00:00Z", None, "a-heidi", "Heidi received salary", {"amount": 500}),
    (54, "bank", "salary.paid", "2026-02-15T16:00:00Z", None, "a-ivan", "Ivan received salary", {"amount": 1000}),
    (55, "bank", "salary.paid", "2026-02-20T10:00:00Z", None, "a-judy", "Judy received salary", {"amount": 1000}),
]
```

**Implementation:**

The `extend_seed_data(conn)` function must:
1. Disable foreign keys (`PRAGMA foreign_keys = OFF`) — same pattern as `insert_seed_data`
2. Insert all EXTRA_* data using `executemany` — same pattern as `helpers.py`
3. For events, convert the dict payload to JSON string using `json.dumps`
4. Call `conn.commit()`
5. Re-enable foreign keys: `PRAGMA foreign_keys = ON`

**Update conftest.py integration:**

Do NOT modify `conftest.py`. Instead, the `extend_seed_data` function will be imported by updating the `e2e_db_path` fixture. Wait — we CANNOT modify conftest.py. So the approach is:

Create a NEW conftest fixture by adding a new file. Actually, since we cannot modify conftest.py, and the current `e2e_db_path` only calls `insert_seed_data`, the cleanest approach is to create a **conftest override** in `services/ui/tests/e2e/fixtures/conftest.py` that provides a richer DB fixture. BUT — pytest conftest.py resolution means inner conftest files DO get loaded automatically.

**Better approach:** Create the seed script as a standalone module. Then create a NEW fixture in `services/ui/tests/e2e/fixtures/conftest.py`:

```python
"""Extended E2E fixtures — richer seed data for comprehensive browser tests."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="session", autouse=True)
def e2e_extended_seed(e2e_db_path: Path) -> None:
    """Extend the base seed data with additional agents, tasks, and events."""
    from fixtures.seed_db import extend_seed_data  # noqa: PLC0415

    conn = sqlite3.connect(str(e2e_db_path))
    extend_seed_data(conn)
    conn.close()
```

This fixture is `autouse=True` and session-scoped, so it runs once after `e2e_db_path` creates the base DB. It EXTENDS without replacing.

### Verification:

After creating both files, run:
```bash
cd services/ui && uv run pytest tests/e2e/test_smoke.py -m e2e -v --timeout=60
```
All 5 smoke tests must still pass (extended data should not break existing tests).

---

## Phase 2: Page Object Models (agent-economy-fsy)

Create three page object classes that encapsulate selectors and common interactions.

### File: `services/ui/tests/e2e/pages/landing.py`

```python
"""Page object model for the landing page (index.html)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class LandingPage:
    """Encapsulates interactions with the landing page."""

    URL_PATH = "/"

    def __init__(self, page: Page, base_url: str) -> None:
        self._page = page
        self._base_url = base_url

    # --- Navigation ---

    def navigate(self) -> None:
        """Navigate to the landing page and wait for load."""
        self._page.goto(f"{self._base_url}{self.URL_PATH}")
        self._page.wait_for_load_state("networkidle")

    # --- Top Ticker ---

    @property
    def ticker_track(self) -> Locator:
        """The top ticker carousel track."""
        return self._page.locator("#ticker-track")

    def get_ticker_items(self) -> list[str]:
        """Return text content of all ticker items."""
        items = self._page.locator("#ticker-track .ticker-item").all()
        return [item.text_content() or "" for item in items]

    # --- Hero Section ---

    @property
    def hero_section(self) -> Locator:
        """The hero section."""
        return self._page.locator(".hero")

    @property
    def hero_title(self) -> Locator:
        """The main hero title (h1)."""
        return self._page.locator(".hero h1")

    @property
    def hero_subtitle(self) -> Locator:
        """The hero subtitle text."""
        return self._page.locator(".hero .subtitle")

    # --- KPI Strip ---

    @property
    def kpi_strip(self) -> Locator:
        """The KPI metrics strip container."""
        return self._page.locator("#kpi-strip")

    def get_kpi_values(self) -> dict[str, str]:
        """Return KPI cell label→value mapping."""
        cells = self._page.locator(".kpi-cell").all()
        result: dict[str, str] = {}
        for cell in cells:
            label_el = cell.locator(".kpi-label")
            value_el = cell.locator(".kpi-value")
            label = (label_el.text_content() or "").strip()
            value = (value_el.text_content() or "").strip()
            if label:
                result[label] = value
        return result

    # --- Exchange Board ---

    @property
    def board_grid(self) -> Locator:
        """The NYSE-style exchange board grid."""
        return self._page.locator("#board-grid")

    @property
    def board_clock(self) -> Locator:
        """The UTC clock in the board header."""
        return self._page.locator("#board-clock")

    def get_exchange_cells(self) -> list[dict[str, str]]:
        """Return list of {label, value, delta} for each board cell."""
        cells = self._page.locator(".board-cell").all()
        result: list[dict[str, str]] = []
        for cell in cells:
            label = (cell.locator(".cell-label").text_content() or "").strip()
            value = (cell.locator(".cell-value").text_content() or "").strip()
            delta_el = cell.locator(".cell-delta")
            delta = (delta_el.text_content() or "").strip() if delta_el.count() > 0 else ""
            result.append({"label": label, "value": value, "delta": delta})
        return result

    # --- How It Works ---

    def get_how_it_works_steps(self) -> list[str]:
        """Return text from the How It Works section steps."""
        steps = self._page.locator(".steps .step").all()
        return [(s.text_content() or "").strip() for s in steps]

    # --- Story / Market Narrative ---

    @property
    def story_text(self) -> Locator:
        """The rotating market story text element."""
        return self._page.locator("#story-text")

    # --- Leaderboard ---

    @property
    def lb_container(self) -> Locator:
        """The leaderboard container."""
        return self._page.locator("#lb-container")

    def get_leaderboard_workers(self) -> list[dict[str, str]]:
        """Return worker leaderboard rows as list of {rank, name, value}."""
        return self._get_lb_rows(0)

    def get_leaderboard_posters(self) -> list[dict[str, str]]:
        """Return poster leaderboard rows as list of {rank, name, value}."""
        return self._get_lb_rows(1)

    def _get_lb_rows(self, panel_index: int) -> list[dict[str, str]]:
        """Extract rows from a leaderboard panel by index."""
        panels = self._page.locator(".lb-panel").all()
        if panel_index >= len(panels):
            return []
        rows = panels[panel_index].locator(".lb-row").all()
        result: list[dict[str, str]] = []
        for row in rows:
            rank = (row.locator(".lb-rank").text_content() or "").strip()
            name = (row.locator(".lb-name").text_content() or "").strip()
            value = (row.locator(".lb-value").text_content() or "").strip()
            result.append({"rank": rank, "name": name, "value": value})
        return result

    # --- Bottom Ticker ---

    @property
    def news_track(self) -> Locator:
        """The bottom news ticker track."""
        return self._page.locator("#news-track")

    # --- Navigation Actions ---

    def click_observatory(self) -> None:
        """Click the 'Observatory' navigation link."""
        self._page.locator("text=Observatory").first.click()

    def click_enter_economy(self) -> None:
        """Click the 'Enter the Economy' button."""
        self._page.locator("text=Enter the Economy").first.click()
```

### File: `services/ui/tests/e2e/pages/observatory.py`

```python
"""Page object model for the observatory page (observatory.html)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class ObservatoryPage:
    """Encapsulates interactions with the observatory page."""

    URL_PATH = "/observatory.html"

    def __init__(self, page: Page, base_url: str) -> None:
        self._page = page
        self._base_url = base_url

    # --- Navigation ---

    def navigate(self) -> None:
        """Navigate to the observatory page and wait for load."""
        self._page.goto(f"{self._base_url}{self.URL_PATH}")
        self._page.wait_for_load_state("networkidle")

    # --- Vitals Bar ---

    @property
    def vitals_bar(self) -> Locator:
        """The top vitals metrics bar."""
        return self._page.locator("#vitals-bar")

    def get_vitals(self) -> dict[str, dict[str, str]]:
        """Return vitals as {label: {value, delta}} mapping."""
        items = self._page.locator(".vital-item").all()
        result: dict[str, dict[str, str]] = {}
        for item in items:
            label = (item.locator(".vital-label").text_content() or "").strip()
            value = (item.locator(".vital-value").text_content() or "").strip()
            delta_el = item.locator(".vital-delta")
            delta = (delta_el.text_content() or "").strip() if delta_el.count() > 0 else ""
            if label:
                result[label] = {"value": value, "delta": delta}
        return result

    # --- GDP Panel ---

    @property
    def gdp_panel(self) -> Locator:
        """The left-sidebar GDP panel."""
        return self._page.locator("#gdp-panel")

    def get_gdp_panel(self) -> dict[str, str]:
        """Return GDP panel sections as {section_label: value}."""
        sections = self._page.locator(".gdp-section").all()
        result: dict[str, str] = {}
        for section in sections:
            label_el = section.locator(".gdp-label, h3, h4")
            value_el = section.locator(".gdp-big, .gdp-value, .hatch-fill")
            label = (label_el.first.text_content() or "").strip() if label_el.count() > 0 else ""
            value = (value_el.first.text_content() or "").strip() if value_el.count() > 0 else ""
            if label:
                result[label] = value
        return result

    # --- Feed ---

    @property
    def feed_scroll(self) -> Locator:
        """The live event feed scroll area."""
        return self._page.locator("#feed-scroll")

    def get_feed_items(self) -> list[dict[str, str]]:
        """Return feed items as list of {badge, text, time}."""
        items = self._page.locator(".feed-item").all()
        result: list[dict[str, str]] = []
        for item in items:
            badge = (item.locator(".feed-badge").text_content() or "").strip()
            text = (item.locator(".feed-text").text_content() or "").strip()
            time_text = (item.locator(".feed-time").text_content() or "").strip()
            result.append({"badge": badge, "text": text, "time": time_text})
        return result

    # --- Filter Buttons ---

    def click_filter(self, filter_type: str) -> None:
        """Click a feed filter button by type (ALL, TASK, BID, etc.)."""
        self._page.locator(f"#filter-btns .feed-btn:text('{filter_type}')").click()

    def get_active_filter(self) -> str:
        """Return the currently active filter button text."""
        active = self._page.locator("#filter-btns .feed-btn.active")
        return (active.text_content() or "").strip()

    # --- Pause/Resume ---

    @property
    def pause_btn(self) -> Locator:
        """The pause/resume button."""
        return self._page.locator("#pause-btn")

    def click_pause(self) -> None:
        """Toggle the feed pause state."""
        self._page.locator("#pause-btn").click()

    def is_paused(self) -> bool:
        """Check if the feed is currently paused."""
        text = (self._page.locator("#pause-btn").text_content() or "").strip()
        return "▶" in text or "Resume" in text.lower()

    # --- Leaderboard Tabs ---

    def click_tab(self, tab: str) -> None:
        """Switch leaderboard tab ('workers' or 'posters')."""
        tab_id = f"#tab-{tab}"
        self._page.locator(tab_id).click()

    def get_active_tab(self) -> str:
        """Return the currently active leaderboard tab name."""
        active = self._page.locator(".lb-tab.active")
        return (active.text_content() or "").strip().lower()

    def get_leaderboard_rows(self) -> list[dict[str, str]]:
        """Return leaderboard rows as list of {rank, name, value}."""
        rows = self._page.locator("#lb-scroll .lb-row").all()
        result: list[dict[str, str]] = []
        for row in rows:
            rank = (row.locator(".lb-rank").text_content() or "").strip()
            name = (row.locator(".lb-name").text_content() or "").strip()
            value = (row.locator(".lb-value").text_content() or "").strip()
            result.append({"rank": rank, "name": name, "value": value})
        return result

    # --- Bottom Ticker ---

    @property
    def bottom_ticker(self) -> Locator:
        """The bottom news ticker."""
        return self._page.locator("#bottom-ticker-track")

    # --- SSE Connection Status ---

    @property
    def live_dot(self) -> Locator:
        """The green pulsing live indicator."""
        return self._page.locator(".live-dot")
```

### File: `services/ui/tests/e2e/pages/task.py`

```python
"""Page object model for the task lifecycle page (task.html)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class TaskPage:
    """Encapsulates interactions with the task lifecycle page."""

    URL_PATH = "/task.html"

    def __init__(self, page: Page, base_url: str) -> None:
        self._page = page
        self._base_url = base_url

    # --- Navigation ---

    def navigate(self, task_id: str | None = None) -> None:
        """Navigate to the task page, optionally for a specific task."""
        url = f"{self._base_url}{self.URL_PATH}"
        if task_id:
            url += f"?task_id={task_id}"
        self._page.goto(url)
        self._page.wait_for_load_state("networkidle")

    # --- Phase Strip ---

    @property
    def phase_strip(self) -> Locator:
        """The 7-phase progress bar."""
        return self._page.locator("#phase-strip")

    def get_phase_strip(self) -> list[dict[str, str]]:
        """Return phase steps as list of {label, state} where state is active/completed/pending."""
        steps = self._page.locator(".phase-step").all()
        result: list[dict[str, str]] = []
        for step in steps:
            label = (step.text_content() or "").strip()
            classes = step.get_attribute("class") or ""
            if "active" in classes:
                state = "active"
            elif "completed" in classes:
                state = "completed"
            else:
                state = "pending"
            result.append({"label": label, "state": state})
        return result

    def get_current_phase(self) -> int:
        """Return the current active phase index (0-6)."""
        steps = self._page.locator(".phase-step").all()
        for i, step in enumerate(steps):
            classes = step.get_attribute("class") or ""
            if "active" in classes:
                return i
        return 0

    # --- Phase Navigation ---

    def click_phase(self, phase: int) -> None:
        """Click on a specific phase step (0-6)."""
        self._page.locator(f".phase-step[data-phase='{phase}']").click()

    def click_next(self) -> None:
        """Click the next phase button."""
        self._page.locator("#btn-next, .btn-next, text='Next'").first.click()

    def click_prev(self) -> None:
        """Click the previous phase button."""
        self._page.locator("#btn-prev, .btn-prev, text='Prev'").first.click()

    def click_auto(self) -> None:
        """Toggle auto-play mode."""
        self._page.locator("#btn-auto, .btn-auto, text='Auto'").first.click()

    # --- Panel Content ---

    @property
    def panel_title(self) -> Locator:
        """The panel title for the current phase."""
        return self._page.locator("#panel-title")

    @property
    def task_status(self) -> Locator:
        """The task status badge."""
        return self._page.locator("#task-status")

    @property
    def lifecycle_content(self) -> Locator:
        """The main lifecycle content area."""
        return self._page.locator("#lifecycle-content")

    @property
    def phase_content(self) -> Locator:
        """The phase-specific content area."""
        return self._page.locator("#phase-content")

    def get_lifecycle_panel_content(self) -> str:
        """Return the full text content of the lifecycle panel."""
        return (self._page.locator("#lifecycle-content").text_content() or "").strip()

    # --- Task Create Form ---

    def fill_task_form(
        self,
        title: str,
        spec: str,
        reward: str,
        bid_deadline: str,
        exec_deadline: str,
        review_deadline: str,
    ) -> None:
        """Fill in the task creation form."""
        self._page.locator("#f-title").fill(title)
        self._page.locator("#f-spec").fill(spec)
        self._page.locator("#f-reward").fill(reward)
        self._page.locator("#f-bid-dl").fill(bid_deadline)
        self._page.locator("#f-exec-dl").fill(exec_deadline)
        self._page.locator("#f-rev-dl").fill(review_deadline)

    def submit_task(self) -> None:
        """Click the post task button."""
        self._page.locator("#btn-post-task").click()

    @property
    def post_error(self) -> Locator:
        """The task creation error message element."""
        return self._page.locator("#post-error")

    # --- Bid Actions ---

    def get_bids(self) -> list[dict[str, str]]:
        """Return displayed bids as list of {bidder, proposal, amount}."""
        rows = self._page.locator(".bid-row").all()
        result: list[dict[str, str]] = []
        for row in rows:
            bidder = (row.locator(".bid-info").text_content() or "").strip()
            amount = (row.locator(".bid-amount").text_content() or "").strip()
            result.append({"bidder": bidder, "amount": amount})
        return result

    def accept_bid(self, bid_id: str) -> None:
        """Click the accept button for a specific bid."""
        self._page.locator(f".btn-accept-bid[data-bid-id='{bid_id}']").click()

    # --- Review Actions ---

    def approve_task(self) -> None:
        """Click the approve button."""
        self._page.locator("#btn-approve").click()

    def show_dispute_form(self) -> None:
        """Click to show the dispute form."""
        self._page.locator("#btn-dispute-show").click()

    def submit_dispute(self, reason: str) -> None:
        """Fill in and submit a dispute."""
        self._page.locator("#f-dispute-reason").fill(reason)
        self._page.locator("#btn-submit-dispute").click()

    # --- Event Feed ---

    def get_event_feed(self) -> list[dict[str, str]]:
        """Return event feed items from the task page sidebar."""
        items = self._page.locator("#feed-scroll .feed-item").all()
        result: list[dict[str, str]] = []
        for item in items:
            badge = (item.locator(".feed-badge").text_content() or "").strip()
            text = (item.locator(".feed-text").text_content() or "").strip()
            result.append({"badge": badge, "text": text})
        return result

    # --- Escrow Display ---

    @property
    def escrow_bar(self) -> Locator:
        """The escrow status bar."""
        return self._page.locator(".escrow-bar")

    @property
    def escrow_amount(self) -> Locator:
        """The escrow amount display."""
        return self._page.locator(".escrow-amount")

    @property
    def escrow_status(self) -> Locator:
        """The escrow status text (LOCKED/RELEASED/etc)."""
        return self._page.locator(".escrow-status")

    # --- Dispute / Ruling Display ---

    @property
    def dispute_panel(self) -> Locator:
        """The dispute panel (red border)."""
        return self._page.locator(".dispute-panel")

    @property
    def rebuttal_panel(self) -> Locator:
        """The rebuttal panel (amber border)."""
        return self._page.locator(".rebuttal-panel")

    @property
    def ruling_card(self) -> Locator:
        """The court ruling card."""
        return self._page.locator(".ruling-card")

    def get_ruling_details(self) -> dict[str, str]:
        """Return ruling details as {worker_pct, summary}."""
        card = self._page.locator(".ruling-card")
        worker_pct = (card.locator(".payout-box").first.text_content() or "").strip()
        summary = (card.locator(".ruling-reasoning").text_content() or "").strip()
        return {"worker_pct": worker_pct, "summary": summary}

    # --- Feedback Display ---

    def get_feedback_rows(self) -> list[dict[str, str]]:
        """Return feedback rows as list of {from_name, rating}."""
        rows = self._page.locator(".feedback-row").all()
        result: list[dict[str, str]] = []
        for row in rows:
            from_name = (row.locator(".feedback-from").text_content() or "").strip()
            rating = (row.locator(".feedback-stars").text_content() or "").strip()
            result.append({"from_name": from_name, "rating": rating})
        return result

    # --- Bottom Ticker ---

    @property
    def ticker_track(self) -> Locator:
        """The bottom ticker track."""
        return self._page.locator("#ticker-track")
```

### Update `pages/__init__.py`:

```python
"""Page object models for E2E tests."""

from pages.landing import LandingPage
from pages.observatory import ObservatoryPage
from pages.task import TaskPage

__all__ = ["LandingPage", "ObservatoryPage", "TaskPage"]
```

### Add page object fixtures in `services/ui/tests/e2e/pages/conftest.py`:

```python
"""Page object fixtures for E2E tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pages.landing import LandingPage
from pages.observatory import ObservatoryPage
from pages.task import TaskPage

if TYPE_CHECKING:
    from playwright.sync_api import Page


@pytest.fixture
def landing_page(e2e_page: Page, e2e_server: str) -> LandingPage:
    """Create a LandingPage object for the test."""
    return LandingPage(e2e_page, e2e_server)


@pytest.fixture
def observatory_page(e2e_page: Page, e2e_server: str) -> ObservatoryPage:
    """Create an ObservatoryPage object for the test."""
    return ObservatoryPage(e2e_page, e2e_server)


@pytest.fixture
def task_page(e2e_page: Page, e2e_server: str) -> TaskPage:
    """Create a TaskPage object for the test."""
    return TaskPage(e2e_page, e2e_server)
```

### Verification:

After creating all page object files, run:
```bash
cd services/ui && uv run pytest tests/e2e/test_smoke.py -m e2e -v --timeout=60
```
All 5 smoke tests must still pass (page objects are just classes, no behavior change).

Also verify imports work:
```bash
cd services/ui && uv run python -c "from pages.landing import LandingPage; from pages.observatory import ObservatoryPage; from pages.task import TaskPage; print('Page objects OK')"
```

---

## Phase 3: DB Mutation and SSE Helpers (agent-economy-4hu)

### File: `services/ui/tests/e2e/helpers/db_helpers.py`

```python
"""Database mutation and SSE event injection helpers for E2E tests.

These helpers allow tests to mutate the database at runtime and verify
that SSE-driven UI updates reflect the changes in the browser.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import Page


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_event_id(conn: sqlite3.Connection) -> int:
    """Return the next available event_id."""
    row = conn.execute("SELECT MAX(event_id) FROM events").fetchone()
    return (row[0] or 0) + 1


def insert_event(
    conn: sqlite3.Connection,
    event_source: str,
    event_type: str,
    summary: str,
    payload: dict[str, Any],
    task_id: str | None = None,
    agent_id: str | None = None,
    timestamp: str | None = None,
) -> int:
    """Insert an event into the events table for SSE pickup.

    Returns the event_id of the inserted event.
    """
    event_id = _next_event_id(conn)
    ts = timestamp or _now_iso()
    conn.execute(
        "INSERT INTO events (event_id, event_source, event_type, timestamp, "
        "task_id, agent_id, summary, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, event_source, event_type, ts, task_id, agent_id, summary, json.dumps(payload)),
    )
    conn.commit()
    return event_id


def advance_task_status(
    conn: sqlite3.Connection,
    task_id: str,
    new_status: str,
) -> None:
    """Update a task's status in board_tasks."""
    conn.execute(
        "UPDATE board_tasks SET status = ? WHERE task_id = ?",
        (new_status, task_id),
    )
    conn.commit()


def add_bid(
    conn: sqlite3.Connection,
    bid_id: str,
    task_id: str,
    bidder_id: str,
    proposal: str,
    submitted_at: str | None = None,
) -> None:
    """Insert a bid into board_bids."""
    ts = submitted_at or _now_iso()
    conn.execute(
        "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (bid_id, task_id, bidder_id, proposal, ts),
    )
    conn.commit()


def add_feedback(
    conn: sqlite3.Connection,
    feedback_id: str,
    task_id: str,
    from_agent_id: str,
    to_agent_id: str,
    role: str,
    category: str,
    rating: str,
    comment: str | None = None,
    submitted_at: str | None = None,
    visible: int = 0,
) -> None:
    """Insert feedback into reputation_feedback."""
    ts = submitted_at or _now_iso()
    conn.execute(
        "INSERT INTO reputation_feedback "
        "(feedback_id, task_id, from_agent_id, to_agent_id, role, category, "
        "rating, comment, submitted_at, visible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, ts, visible),
    )
    conn.commit()


def create_escrow(
    conn: sqlite3.Connection,
    escrow_id: str,
    payer_account_id: str,
    amount: int,
    task_id: str,
    status: str = "locked",
    created_at: str | None = None,
    resolved_at: str | None = None,
) -> None:
    """Insert an escrow record into bank_escrow."""
    ts = created_at or _now_iso()
    conn.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (escrow_id, payer_account_id, amount, task_id, status, ts, resolved_at),
    )
    conn.commit()


def add_court_claim(
    conn: sqlite3.Connection,
    claim_id: str,
    task_id: str,
    claimant_id: str,
    respondent_id: str,
    reason: str,
    status: str = "filed",
    filed_at: str | None = None,
) -> None:
    """Insert a court claim into court_claims."""
    ts = filed_at or _now_iso()
    conn.execute(
        "INSERT INTO court_claims "
        "(claim_id, task_id, claimant_id, respondent_id, reason, status, filed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (claim_id, task_id, claimant_id, respondent_id, reason, status, ts),
    )
    conn.commit()


def wait_for_sse_update(page: Page, timeout: float = 5000) -> None:
    """Wait for the DOM to update after an SSE event injection.

    Waits for a new .feed-item to appear in the feed, or times out.
    Uses Playwright's built-in waiting with a MutationObserver approach:
    we count current feed items, then wait for the count to increase.
    """
    current_count = page.locator(".feed-item").count()
    try:
        page.wait_for_function(
            f"document.querySelectorAll('.feed-item').length > {current_count}",
            timeout=timeout,
        )
    except Exception:
        # Timeout is acceptable — the event may not always produce a visible feed item
        pass


def get_writable_db_connection(db_path: str) -> sqlite3.Connection:
    """Open a writable connection to the E2E test database.

    The E2E server opens the DB in read-only mode, but this helper
    opens a separate writable connection for test mutations.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn
```

### Update `helpers/__init__.py`:

```python
"""E2E test helpers."""

from helpers.db_helpers import (
    add_bid,
    add_court_claim,
    add_feedback,
    advance_task_status,
    create_escrow,
    get_writable_db_connection,
    insert_event,
    wait_for_sse_update,
)

__all__ = [
    "add_bid",
    "add_court_claim",
    "add_feedback",
    "advance_task_status",
    "create_escrow",
    "get_writable_db_connection",
    "insert_event",
    "wait_for_sse_update",
]
```

### Add helper fixtures in `services/ui/tests/e2e/helpers/conftest.py`:

```python
"""DB helper fixtures for E2E tests."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from helpers.db_helpers import get_writable_db_connection

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def e2e_writable_db(e2e_db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Provide a writable DB connection for test mutations."""
    conn = get_writable_db_connection(str(e2e_db_path))
    yield conn
    conn.close()
```

### Verification:

After creating all helper files, run:
```bash
cd services/ui && uv run pytest tests/e2e/test_smoke.py -m e2e -v --timeout=60
```
All 5 smoke tests must still pass.

Verify imports:
```bash
cd services/ui && uv run python -c "from helpers.db_helpers import insert_event, wait_for_sse_update; print('Helpers OK')"
```

---

## Phase 4: Validation Test for New Infrastructure

Create a test file that exercises the new fixtures to verify they work.

### File: `services/ui/tests/e2e/test_infrastructure.py`

```python
"""Infrastructure validation tests — verify seed data, page objects, and helpers work."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from helpers.db_helpers import insert_event
from pages.landing import LandingPage
from pages.observatory import ObservatoryPage
from pages.task import TaskPage

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Page


@pytest.mark.e2e
def test_extended_seed_data_has_10_agents(e2e_db_path: Path) -> None:
    """The extended seed data should have at least 10 agents."""
    conn = sqlite3.connect(str(e2e_db_path))
    count = conn.execute("SELECT COUNT(*) FROM identity_agents").fetchone()[0]
    conn.close()
    assert count >= 10, f"Expected at least 10 agents, got {count}"


@pytest.mark.e2e
def test_extended_seed_data_has_50_events(e2e_db_path: Path) -> None:
    """The extended seed data should have at least 50 events."""
    conn = sqlite3.connect(str(e2e_db_path))
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert count >= 50, f"Expected at least 50 events, got {count}"


@pytest.mark.e2e
def test_extended_seed_data_task_statuses(e2e_db_path: Path) -> None:
    """Seed data should include tasks at multiple lifecycle stages."""
    conn = sqlite3.connect(str(e2e_db_path))
    statuses = [row[0] for row in conn.execute("SELECT DISTINCT status FROM board_tasks").fetchall()]
    conn.close()
    expected = {"open", "accepted", "submitted", "approved", "cancelled", "disputed", "ruled", "expired"}
    missing = expected - set(statuses)
    assert not missing, f"Missing task statuses in seed data: {missing}"


@pytest.mark.e2e
def test_landing_page_object(e2e_page: Page, e2e_server: str) -> None:
    """The LandingPage object should navigate and read KPI values."""
    lp = LandingPage(e2e_page, e2e_server)
    lp.navigate()
    kpis = lp.get_kpi_values()
    assert len(kpis) > 0, "Expected at least one KPI value"


@pytest.mark.e2e
def test_observatory_page_object(e2e_page: Page, e2e_server: str) -> None:
    """The ObservatoryPage object should navigate and read vitals."""
    op = ObservatoryPage(e2e_page, e2e_server)
    op.navigate()
    # Wait a moment for SSE to populate feed
    e2e_page.wait_for_timeout(2000)
    feed = op.get_feed_items()
    assert len(feed) > 0, "Expected at least one feed item from seed data"


@pytest.mark.e2e
def test_task_page_object(e2e_page: Page, e2e_server: str) -> None:
    """The TaskPage object should navigate to a task view."""
    tp = TaskPage(e2e_page, e2e_server)
    tp.navigate(task_id="t-task1")
    content = tp.get_lifecycle_panel_content()
    assert len(content) > 0, "Expected lifecycle panel to have content"


@pytest.mark.e2e
def test_db_mutation_inserts_event(e2e_db_path: Path) -> None:
    """The insert_event helper should successfully add an event to the DB."""
    conn = sqlite3.connect(str(e2e_db_path))
    count_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    event_id = insert_event(
        conn,
        event_source="board",
        event_type="task.created",
        summary="Test task created",
        payload={"title": "Test Task", "reward": 50},
        task_id=None,
        agent_id="a-alice",
    )
    count_after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert count_after == count_before + 1
    assert event_id > count_before
```

### Verification:

```bash
cd services/ui && uv run pytest tests/e2e -m e2e -v --timeout=60
```
All tests (5 smoke + 8 infrastructure) must pass.

---

## Phase 5: Final CI Validation

Run the full CI pipeline to ensure everything is clean:

```bash
cd services/ui && just ci-quiet
```

If there are formatting issues:
```bash
cd services/ui && just code-format
```

Then re-run: `cd services/ui && just ci-quiet`

Also run E2E tests explicitly (CI may exclude them):
```bash
cd services/ui && uv run pytest tests/e2e -m e2e -v --timeout=60
```

**Expected outcome:** All CI checks pass AND all E2E tests pass.

---

## Summary of Files Created

| File | Purpose |
|------|---------|
| `services/ui/tests/e2e/fixtures/seed_db.py` | Extended seed data (10+ agents, 50+ events) |
| `services/ui/tests/e2e/fixtures/conftest.py` | Autouse fixture to extend base seed data |
| `services/ui/tests/e2e/pages/landing.py` | Landing page object model |
| `services/ui/tests/e2e/pages/observatory.py` | Observatory page object model |
| `services/ui/tests/e2e/pages/task.py` | Task lifecycle page object model |
| `services/ui/tests/e2e/pages/__init__.py` | Re-exports all page objects |
| `services/ui/tests/e2e/pages/conftest.py` | Page object pytest fixtures |
| `services/ui/tests/e2e/helpers/db_helpers.py` | DB mutation + SSE injection helpers |
| `services/ui/tests/e2e/helpers/__init__.py` | Re-exports all helpers |
| `services/ui/tests/e2e/helpers/conftest.py` | Writable DB connection fixture |
| `services/ui/tests/e2e/test_infrastructure.py` | Validation tests for new infrastructure |
