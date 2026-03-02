# E2E Playwright Test Infrastructure — Codex Execution Plan

## Overview

Set up the foundational E2E test infrastructure for browser-based Playwright tests against the UI service. This covers ticket `agent-economy-x3e` — Playwright pytest plugin configuration and browser fixtures.

## Pre-Flight

Read these files FIRST before doing anything:
1. `AGENTS.md` — project conventions (CRITICAL: uv run, no pip, no hardcoded defaults)
2. This file — the execution plan
3. `services/ui/pyproject.toml` — current dependencies and tool config
4. `services/ui/tests/integration/conftest.py` — existing test patterns to follow
5. `services/ui/tests/integration/helpers.py` — existing seed data helpers
6. `docs/specifications/schema.sql` — database schema

## Rules

- Use `uv run` for all Python execution — never raw python, python3, or pip install
- Do NOT modify any existing test files
- All config must come from config.yaml, never hardcoded
- Commit after each phase completes
- Run `cd services/ui && just ci-quiet` after all phases

---

## Phase 1: Add Playwright Dependencies to pyproject.toml

### Files to modify:
- `services/ui/pyproject.toml`

### Steps:

1. Edit `services/ui/pyproject.toml`:

   In the `[project.optional-dependencies]` `dev` list, add these THREE entries after `"httpx>=0.28.0"`:
   ```
       "pytest-playwright>=0.6.2",
       "playwright>=1.49.0",
       "pytest-base-url>=2.1.0",
   ```

   In `[tool.deptry.per_rule_ignores]` `DEP002` list, add these entries to the end of the list (before the closing `]`):
   ```
       "pytest-playwright",
       "playwright",
       "pytest-base-url",
   ```

   In `[tool.pytest.ini_options]` `markers` list, add this new marker:
   ```
       "e2e: End-to-end browser tests (require Playwright)",
   ```

2. Run: `cd services/ui && uv sync --all-extras`

3. Install Playwright browsers: `cd services/ui && uv run playwright install chromium`

4. Verify: `cd services/ui && uv run python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"`
   Expected: `Playwright OK`

5. Commit: `git add services/ui/pyproject.toml services/ui/uv.lock && git commit -m "feat: add pytest-playwright dependencies for E2E testing"`

---

## Phase 2: Create E2E Test Directory Structure

### Files to create:
- `services/ui/tests/e2e/__init__.py`
- `services/ui/tests/e2e/conftest.py`
- `services/ui/tests/e2e/fixtures/__init__.py`
- `services/ui/tests/e2e/pages/__init__.py`
- `services/ui/tests/e2e/helpers/__init__.py`

### Steps:

1. Create all `__init__.py` files as empty files with the standard docstring:

   `services/ui/tests/e2e/__init__.py`:
   ```python
   """E2E browser tests for the UI service."""
   ```

   `services/ui/tests/e2e/fixtures/__init__.py`:
   ```python
   """E2E test fixtures."""
   ```

   `services/ui/tests/e2e/pages/__init__.py`:
   ```python
   """Page object models for E2E tests."""
   ```

   `services/ui/tests/e2e/helpers/__init__.py`:
   ```python
   """E2E test helpers."""
   ```

2. Create `services/ui/tests/e2e/conftest.py` with this EXACT content:

```python
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
from typing import Generator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

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
    proc = subprocess.Popen(  # noqa: S603
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
    import pluggy  # noqa: PLC0415

    outcome: pluggy.Result = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
```

3. Commit:
```
git add services/ui/tests/e2e/ && git commit -m "feat: add E2E test directory structure and Playwright conftest"
```

---

## Phase 3: Create a Smoke Test

Create a basic smoke test to verify the infrastructure works end-to-end.

### Files to create:
- `services/ui/tests/e2e/test_smoke.py`

### Steps:

1. Create `services/ui/tests/e2e/test_smoke.py` with this EXACT content:

```python
"""Smoke tests — verify E2E infrastructure is wired correctly."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page


@pytest.mark.e2e
def test_landing_page_loads(e2e_page: Page) -> None:
    """The landing page should load and contain a title."""
    e2e_page.goto(f"{e2e_page.base_url}/")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")
    assert e2e_page.title() != ""


@pytest.mark.e2e
def test_observatory_page_loads(e2e_page: Page) -> None:
    """The observatory page should load."""
    e2e_page.goto(f"{e2e_page.base_url}/observatory.html")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")
    assert e2e_page.title() != ""


@pytest.mark.e2e
def test_task_page_loads(e2e_page: Page) -> None:
    """The task lifecycle page should load."""
    e2e_page.goto(f"{e2e_page.base_url}/task.html")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")
    assert e2e_page.title() != ""


@pytest.mark.e2e
def test_health_endpoint(e2e_page: Page) -> None:
    """The /health API endpoint should return status ok."""
    resp = e2e_page.request.get(f"{e2e_page.base_url}/health")  # type: ignore[attr-defined]
    assert resp.status == 200
    body = resp.json()
    assert body["status"] == "ok"


@pytest.mark.e2e
def test_metrics_api(e2e_page: Page) -> None:
    """The /api/metrics endpoint should return economy metrics."""
    resp = e2e_page.request.get(f"{e2e_page.base_url}/api/metrics")  # type: ignore[attr-defined]
    assert resp.status == 200
    body = resp.json()
    assert "gdp" in body
    assert "agent_count" in body
```

2. Verify the tests run:
```
cd services/ui && uv run pytest tests/e2e/test_smoke.py -m e2e -v --timeout=60
```

   Expected: All 5 tests pass. If Playwright browsers are not installed, run:
   ```
   cd services/ui && uv run playwright install chromium
   ```
   Then re-run the tests.

3. Commit:
```
git add services/ui/tests/e2e/test_smoke.py && git commit -m "test: add E2E smoke tests verifying Playwright infrastructure"
```

---

## Phase 4: Run CI

### Steps:

1. Run: `cd services/ui && just ci-quiet`
2. If there are failures, fix them. Common issues:
   - Ruff formatting: run `cd services/ui && just code-format`
   - Import ordering: run `cd services/ui && just code-format`
   - Mypy type errors: mypy only checks `src/`, so test files should not trigger mypy errors
   - Spelling errors: fix the spelling or add to codespell ignore list at `../../config/codespell/ignore.txt`
   - Deptry errors about unused deps: add to `DEP002` list in pyproject.toml
   - Pyright errors: pyright checks against `pyrightconfig.json`, may need adjustments
3. After fixing, re-run: `cd services/ui && just ci-quiet`
4. Expected: all checks pass

   IMPORTANT: The CI pipeline (`just ci-quiet`) runs unit and integration tests but does NOT automatically run E2E tests. E2E tests are run separately with `-m e2e`. This is intentional — E2E tests require Playwright browsers which may not be available in all CI environments.

5. Also verify E2E tests still pass: `cd services/ui && uv run pytest tests/e2e -m e2e -v --timeout=60`

6. If CI passes, commit any fixes:
```
git add -A && git commit -m "fix: resolve CI issues from E2E infrastructure setup"
```

---

## Phase 5: Final Verification

1. Run: `just ci-all-quiet` (from project root `/Users/flo/Developer/github/agent-economy`)
2. Expected: all checks pass for all services
3. If there are failures in other services (e.g., if uv.lock changes affect other services), fix them.
4. Final E2E verification: `cd services/ui && uv run pytest tests/e2e -m e2e -v --timeout=60`
