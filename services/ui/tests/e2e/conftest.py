"""E2E test configuration — Playwright fixtures for browser-based UI tests."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import sync_playwright

E2E_SUPPORT = Path(__file__).resolve().parent
if str(E2E_SUPPORT) not in sys.path:
    sys.path.insert(0, str(E2E_SUPPORT))

from _fixture_support import start_controlled_ui_server  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = PROJECT_ROOT / "docs" / "specifications" / "schema.sql"
INTEGRATION_HELPERS = Path(__file__).resolve().parents[1] / "integration"

# Reuse the integration seed helpers
if str(INTEGRATION_HELPERS) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_HELPERS))


# ---------------------------------------------------------------------------
# Session-scoped: database, server, browser
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def e2e_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a seeded SQLite database for the E2E test session."""
    from helpers import insert_seed_data  # noqa: PLC0415

    db_dir = tmp_path_factory.mktemp("e2e_db")
    db_file = db_dir / "economy.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("PRAGMA journal_mode=WAL")
    insert_seed_data(conn)
    conn.close()
    return db_file


@pytest.fixture(scope="session")
def e2e_server(
    e2e_db_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[str, None, None]:
    """Start a uvicorn server for the E2E session and yield its base URL."""
    yield from start_controlled_ui_server(
        e2e_db_path,
        tmp_path_factory,
        "e2e_server",
        frozen_clock=False,
    )


# Tables in child-to-parent FK order, safe to DELETE-then-reinsert every test.
# (foreign_keys is turned OFF for the delete pass regardless, but keeping the
# order correct documents the real dependency graph from schema.sql.)
_MUTABLE_SEED_TABLES = (
    "events",
    "court_rulings",
    "court_rebuttals",
    "court_claims",
    "reputation_feedback",
    "board_assets",
    "board_bids",
    "board_task_contracts",
    "board_tasks",
    "bank_escrow",
    "bank_transactions",
    "bank_accounts",
    "identity_agents",
)


@pytest.fixture(autouse=True)
def e2e_reset_seed_data(e2e_db_path: Path) -> None:
    """Reset the shared E2E database to pristine seed state before every test.

    T-107 root cause: ``e2e_db_path``/``e2e_server`` below are session-scoped
    (one seeded SQLite DB, one uvicorn process, shared across the whole
    ~270-test session) so we don't pay browser+server startup cost per test.
    Only the browser context/page are function-scoped. Many tests mutate
    shared rows through a writable connection (``advance_task_status``,
    ``add_bid``, ``insert_event``, ``create_escrow``, ``add_feedback``, ad
    hoc ``board_tasks`` inserts in test_observatory.py) or implicitly assume
    an untouched seed (exact counts/totals). Without a reset, one test's
    mutation becomes a later test's "pristine" starting condition:
    test_landing.py permanently moving t-task-e3 to 'approved' shifted
    test_observatory.py's O30 GDP bucket counts; the growing event volume
    from accumulated mutations also widened the window during which
    task.js's SSE catch-up (``connectTaskSSE`` always replays from
    ``event_id=0`` — see data/web/assets/task.js) could race a test's
    manual phase-strip navigation and silently reset it back to the task's
    current phase, producing the "assert 'Settle' == 'Contract'"-style
    failures in test_task_lifecycle.py that only appeared in full-suite runs.

    Fix approach (DB reset, not self-isolation): truncate every mutable seed
    table and re-run the exact same seed functions the session fixtures use,
    via a *separate* connection to the same WAL-mode DB file the running
    server already has open read-only. In WAL mode a fresh statement on a
    separate connection sees the latest committed writer state without
    restarting the server, so this is safe and — empirically, an
    add/delete of a few hundred rows — cheap enough to run before all ~270
    tests without a meaningful runtime hit. This also makes the extended
    seed data (agents/tasks used by test_landing.py, test_observatory.py,
    test_task_lifecycle.py, test_infrastructure.py) deterministically
    present from the very first test, instead of depending on whichever
    test file's ad hoc ``_ensure_extended_seed_data`` happened to run first
    alphabetically.
    """
    from helpers import insert_seed_data  # noqa: PLC0415

    from .fixtures.seed_db import extend_seed_data  # noqa: PLC0415

    conn = sqlite3.connect(str(e2e_db_path))
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in _MUTABLE_SEED_TABLES:
            conn.execute(f"DELETE FROM {table}")  # nosec B608 - fixed internal table list
        conn.commit()
        insert_seed_data(conn)
        extend_seed_data(conn)
    finally:
        conn.close()


@pytest.fixture(scope="session")
def e2e_playwright() -> Generator[Playwright, None, None]:
    """Launch Playwright for the session."""
    pw = sync_playwright().start()
    yield pw
    pw.stop()


@pytest.fixture(scope="session")
def e2e_browser(e2e_playwright: Playwright) -> Generator[Browser, None, None]:
    """Launch a headless Chromium browser for the session."""
    browser = e2e_playwright.chromium.launch(headless=True)
    yield browser
    browser.close()


# ---------------------------------------------------------------------------
# Function-scoped: context, page
# ---------------------------------------------------------------------------
@pytest.fixture
def e2e_context(e2e_browser: Browser) -> Generator[BrowserContext, None, None]:
    """Create a fresh browser context for each test."""
    context = e2e_browser.new_context(
        viewport={"width": 1280, "height": 720},
    )
    yield context
    context.close()


@pytest.fixture
def e2e_page(e2e_context: BrowserContext, e2e_server: str) -> Generator[Page, None, None]:
    """Create a new page in the test context."""
    page = e2e_context.new_page()
    page.set_default_timeout(10_000)
    page.set_default_navigation_timeout(15_000)
    # Store base URL on the page for easy access
    page.base_url = e2e_server  # type: ignore[attr-defined]
    yield page
    page.close()


@pytest.fixture
def screenshot_on_failure(
    request: pytest.FixtureRequest,
    e2e_page: Page,
) -> Generator[None, None, None]:
    """Capture a screenshot if the test fails."""
    yield
    if request.node.rep_call and request.node.rep_call.failed:  # type: ignore[union-attr]
        screenshot_dir = Path("reports") / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        name = request.node.name.replace("/", "_").replace("::", "_")
        e2e_page.screenshot(path=str(screenshot_dir / f"{name}.png"))


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):  # noqa: ARG001
    """Store test outcome on the item for screenshot_on_failure fixture."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
