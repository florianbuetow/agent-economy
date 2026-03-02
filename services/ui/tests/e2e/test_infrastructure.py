"""Infrastructure validation tests for E2E seed data and helper utilities."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from .fixtures.seed_db import extend_seed_data
from .helpers.db_helpers import insert_event
from .pages.landing import LandingPage
from .pages.observatory import ObservatoryPage
from .pages.task import TaskPage

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Page


def _ensure_extended_seed_data(db_path: Path) -> None:
    """Apply extended seed data once if the DB is still at base integration seed size."""
    conn = sqlite3.connect(str(db_path))
    agent_count = conn.execute("SELECT COUNT(*) FROM identity_agents").fetchone()[0]
    if agent_count < 10:
        extend_seed_data(conn)
    conn.close()


@pytest.mark.e2e
def test_extended_seed_data_has_10_agents(e2e_db_path: Path) -> None:
    """The extended seed data should have at least 10 agents."""
    _ensure_extended_seed_data(e2e_db_path)
    conn = sqlite3.connect(str(e2e_db_path))
    count = conn.execute("SELECT COUNT(*) FROM identity_agents").fetchone()[0]
    conn.close()
    assert count >= 10, f"Expected at least 10 agents, got {count}"


@pytest.mark.e2e
def test_extended_seed_data_has_50_events(e2e_db_path: Path) -> None:
    """The extended seed data should have at least 50 events."""
    _ensure_extended_seed_data(e2e_db_path)
    conn = sqlite3.connect(str(e2e_db_path))
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert count >= 50, f"Expected at least 50 events, got {count}"


@pytest.mark.e2e
def test_extended_seed_data_task_statuses(e2e_db_path: Path) -> None:
    """Seed data should include tasks at multiple lifecycle stages."""
    _ensure_extended_seed_data(e2e_db_path)
    conn = sqlite3.connect(str(e2e_db_path))
    statuses = [
        row[0] for row in conn.execute("SELECT DISTINCT status FROM board_tasks").fetchall()
    ]
    conn.close()
    expected = {
        "open",
        "accepted",
        "submitted",
        "approved",
        "cancelled",
        "disputed",
        "ruled",
        "expired",
    }
    missing = expected - set(statuses)
    assert not missing, f"Missing task statuses in seed data: {missing}"


@pytest.mark.e2e
def test_landing_page_object(e2e_page: Page, e2e_server: str) -> None:
    """The LandingPage object should navigate and read KPI values."""
    landing_page = LandingPage(e2e_page, e2e_server)
    landing_page.navigate()
    kpis = landing_page.get_kpi_values()
    assert len(kpis) > 0, "Expected at least one KPI value"


@pytest.mark.e2e
def test_observatory_page_object(e2e_page: Page, e2e_server: str) -> None:
    """The ObservatoryPage object should navigate and read feed items."""
    observatory_page = ObservatoryPage(e2e_page, e2e_server)
    observatory_page.navigate()
    e2e_page.wait_for_timeout(2000)
    feed = observatory_page.get_feed_items()
    assert len(feed) > 0, "Expected at least one feed item from seed data"


@pytest.mark.e2e
def test_task_page_object(e2e_page: Page, e2e_server: str) -> None:
    """The TaskPage object should navigate to a task view."""
    task_page = TaskPage(e2e_page, e2e_server)
    task_page.navigate(task_id="t-task1")
    e2e_page.wait_for_function(
        "document.querySelector('#phase-content') && "
        "document.querySelector('#phase-content').textContent.trim().length > 0",
    )
    content = task_page.get_lifecycle_panel_content()
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
