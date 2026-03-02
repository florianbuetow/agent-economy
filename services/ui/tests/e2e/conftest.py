"""E2E test configuration — Playwright fixtures for browser-based UI tests."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import sync_playwright

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
# Helpers
# ---------------------------------------------------------------------------
def _free_port() -> int:
    """Find an available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 15.0) -> None:
    """Poll GET /health until it returns 200 or timeout."""
    import httpx  # noqa: PLC0415

    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.3)
    msg = f"Server on port {port} did not become healthy within {timeout}s"
    raise TimeoutError(msg)


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
    port = _free_port()
    work_dir = tmp_path_factory.mktemp("e2e_server")

    # Create minimal web directory with real frontend files
    web_dir = work_dir / "web"
    real_web = PROJECT_ROOT / "services" / "ui" / "data" / "web"
    if real_web.is_dir():
        shutil.copytree(str(real_web), str(web_dir))
    else:
        web_dir.mkdir()
        (web_dir / "index.html").write_text("<html><body>Test</body></html>")

    log_dir = work_dir / "logs"
    log_dir.mkdir()

    config_content = (
        f'service:\n  name: "ui"\n  version: "0.1.0"\n'
        f'server:\n  host: "127.0.0.1"\n  port: {port}\n  log_level: "warning"\n'
        f'logging:\n  level: "WARNING"\n  directory: "{log_dir}"\n'
        f'database:\n  path: "{e2e_db_path}"\n'
        f"sse:\n  poll_interval_seconds: 1\n  keepalive_interval_seconds: 15\n  batch_size: 50\n"
        f'frontend:\n  web_root: "{web_dir}"\n'
        f"request:\n  max_body_size: 1572864\n"
        f'user_agent:\n  agent_config_path: "../../agents/config.yaml"\n'
    )
    config_path = work_dir / "config.yaml"
    config_path.write_text(config_content)

    env = {**os.environ, "CONFIG_PATH": str(config_path)}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "ui_service.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(PROJECT_ROOT / "services" / "ui"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_server(port)
    except TimeoutError:
        proc.kill()
        raise

    base_url = f"http://127.0.0.1:{port}"
    yield base_url

    # Teardown
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


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
