"""Cross-page and edge-case E2E tests (X01-X14)."""

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

from .pages.landing import LandingPage
from .pages.observatory import ObservatoryPage
from .pages.task import TaskPage

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = PROJECT_ROOT / "docs" / "specifications" / "schema.sql"
REAL_WEB = PROJECT_ROOT / "services" / "ui" / "data" / "web"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(port: int, timeout: float = 15.0) -> None:
    import httpx  # noqa: PLC0415

    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.25)
    msg = f"Server on port {port} did not become healthy within {timeout}s"
    raise TimeoutError(msg)


def _seed_single_agent(conn: sqlite3.Connection) -> None:
    """Seed a minimal one-agent economy."""
    conn.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        (
            "a-solo",
            "Solo",
            "ed25519:U09MT1NPTE9TT0xPU09MT1NPTE9TT0xPU09MT1NPTE8=",
            "2026-03-02T00:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
        ("a-solo", 958, "2026-03-02T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("tx-solo-1", "a-solo", "escrow_lock", 42, 958, "esc-solo-1", "2026-03-02T00:00:40Z"),
    )
    conn.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("esc-solo-1", "a-solo", 42, "t-solo-1", "locked", "2026-03-02T00:00:40Z", None),
    )
    conn.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, execution_deadline, "
        "review_deadline, escrow_id, worker_id, accepted_bid_id, dispute_reason, ruling_id, "
        "worker_pct, ruling_summary, created_at, accepted_at, submitted_at, approved_at, "
        "cancelled_at, disputed_at, ruled_at, expired_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "t-solo-1",
            "a-solo",
            "Solo Task",
            "Single agent bootstrap task",
            42,
            "open",
            86400,
            604800,
            172800,
            "2026-03-02T06:30:00Z",
            None,
            None,
            "esc-solo-1",
            None,
            None,
            None,
            None,
            None,
            None,
            "2026-03-02T00:00:00Z",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    )
    conn.execute(
        "INSERT INTO events "
        "(event_id, event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            "identity",
            "agent.registered",
            "2026-03-02T00:00:00Z",
            None,
            "a-solo",
            "Solo registered",
            '{"agent_name":"Solo"}',
        ),
    )
    conn.execute(
        "INSERT INTO events "
        "(event_id, event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            2,
            "board",
            "task.created",
            "2026-03-02T00:00:00Z",
            "t-solo-1",
            "a-solo",
            "Solo posted Solo Task",
            '{"title":"Solo Task","reward":42}',
        ),
    )
    conn.commit()


def _start_ui_server_with_db(
    db_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    name: str,
) -> Generator[str, None, None]:
    port = _free_port()
    work_dir = tmp_path_factory.mktemp(name)

    web_dir = work_dir / "web"
    shutil.copytree(str(REAL_WEB), str(web_dir))

    log_dir = work_dir / "logs"
    log_dir.mkdir()

    config_path = work_dir / "config.yaml"
    config_content = (
        'service:\n  name: "ui"\n  version: "0.1.0"\n'
        f'server:\n  host: "127.0.0.1"\n  port: {port}\n  log_level: "warning"\n'
        f'logging:\n  level: "WARNING"\n  directory: "{log_dir}"\n'
        f'database:\n  path: "{db_path}"\n'
        "sse:\n  poll_interval_seconds: 1\n  keepalive_interval_seconds: 15\n  batch_size: 50\n"
        f'frontend:\n  web_root: "{web_dir}"\n'
        "request:\n  max_body_size: 1572864\n"
        'user_agent:\n  agent_config_path: "../../agents/config.yaml"\n'
    )
    config_path.write_text(config_content)

    env = {**os.environ, "CONFIG_PATH": str(config_path)}
    process = subprocess.Popen(
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
        process.kill()
        raise

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _build_db(
    tmp_path_factory: pytest.TempPathFactory,
    name: str,
    seed_fn: Callable[[sqlite3.Connection], None] | None,
) -> Path:
    db_dir = tmp_path_factory.mktemp(name)
    db_path = db_dir / "economy.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute("PRAGMA journal_mode=WAL")
        if seed_fn is not None:
            seed_fn(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture(scope="session")
def empty_e2e_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    """A separate UI server backed by schema-only empty DB."""
    db_path = _build_db(tmp_path_factory, "e2e_empty_db", seed_fn=None)
    yield from _start_ui_server_with_db(db_path, tmp_path_factory, "e2e_empty_server")


@pytest.fixture(scope="session")
def single_agent_e2e_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    """A separate UI server backed by a single-agent DB."""
    db_path = _build_db(tmp_path_factory, "e2e_single_agent_db", seed_fn=_seed_single_agent)
    yield from _start_ui_server_with_db(db_path, tmp_path_factory, "e2e_single_agent_server")


class TestCrossPageEmptyDB:
    def test_x01_empty_db_landing_renders_zero_state(
        self,
        e2e_page: Page,
        e2e_server: str,  # required shared fixture
        empty_e2e_server: str,
    ) -> None:
        _ = e2e_server
        landing = LandingPage(e2e_page, empty_e2e_server)
        landing.navigate()
        e2e_page.wait_for_function("document.querySelectorAll('.kpi-cell').length === 5")
        values = landing.get_kpi_values()
        assert values["Economy GDP"] == "0 \u00a9"
        assert values["Active Agents"] == "0"
        assert values["Tasks Completed"] == "0+"
        assert values["Spec Quality"] == "0%"
        assert values["Economy Phase"] == "STALLED"

    def test_x02_empty_db_observatory_handles_no_feed(
        self,
        e2e_page: Page,
        e2e_server: str,  # required shared fixture
        empty_e2e_server: str,
    ) -> None:
        _ = e2e_server
        observatory = ObservatoryPage(e2e_page, empty_e2e_server)
        observatory.navigate()
        e2e_page.wait_for_function("document.querySelectorAll('.vital-item').length >= 7")
        vitals = observatory.get_vitals()
        assert vitals["Active Agents"]["value"] == "0"
        assert vitals["Open Tasks"]["value"] == "0"
        assert observatory.feed_scroll.locator(".feed-item").count() == 0
        assert observatory.get_leaderboard_rows() == []

    def test_x03_empty_db_task_page_stays_in_create_mode(
        self,
        e2e_page: Page,
        e2e_server: str,  # required shared fixture
        empty_e2e_server: str,
    ) -> None:
        _ = e2e_server
        task = TaskPage(e2e_page, empty_e2e_server)
        task.navigate()
        e2e_page.wait_for_function(
            "document.querySelector('#phase-content') && "
            "document.querySelector('#phase-content').textContent.length > 0",
        )
        assert "Post a New Task" in (task.panel_title.text_content() or "")
        assert (task.task_status.text_content() or "").strip() == "DRAFT"
        assert e2e_page.locator("#btn-post-task").count() == 1

    def test_x04_empty_db_api_endpoints_return_safe_defaults(
        self,
        e2e_page: Page,
        e2e_server: str,  # required shared fixture
        empty_e2e_server: str,
    ) -> None:
        _ = e2e_server
        metrics_resp = e2e_page.request.get(f"{empty_e2e_server}/api/metrics")
        events_resp = e2e_page.request.get(f"{empty_e2e_server}/api/events")
        agents_resp = e2e_page.request.get(f"{empty_e2e_server}/api/agents")
        assert metrics_resp.status == 200
        assert events_resp.status == 200
        assert agents_resp.status == 200
        metrics = metrics_resp.json()
        events = events_resp.json()
        agents = agents_resp.json()
        assert metrics["agents"]["total_registered"] == 0
        assert metrics["tasks"]["total_created"] == 0
        assert events["events"] == []
        assert agents["agents"] == []


class TestCrossPageSingleAgent:
    def test_x05_single_agent_landing_kpis_reflect_singleton(
        self,
        e2e_page: Page,
        e2e_server: str,  # required shared fixture
        single_agent_e2e_server: str,
    ) -> None:
        _ = e2e_server
        landing = LandingPage(e2e_page, single_agent_e2e_server)
        landing.navigate()
        e2e_page.wait_for_function("document.querySelectorAll('.kpi-cell').length === 5")
        values = landing.get_kpi_values()
        assert values["Active Agents"] == "1"
        assert values["Economy GDP"] == "0 \u00a9"
        active_note = (
            e2e_page.locator(
                ".kpi-cell:has(.kpi-label:text-is('Active Agents')) .kpi-note"
            ).text_content()
            or ""
        )
        assert "of 1 registered" in active_note

    def test_x06_single_agent_landing_leaderboard_handles_one_agent(
        self,
        e2e_page: Page,
        e2e_server: str,  # required shared fixture
        single_agent_e2e_server: str,
    ) -> None:
        _ = e2e_server
        landing = LandingPage(e2e_page, single_agent_e2e_server)
        landing.navigate()
        e2e_page.wait_for_function(
            "document.querySelectorAll('#lb-container .lb-panel').length === 2"
        )
        worker_rows = e2e_page.locator("#lb-container .lb-panel").nth(0).locator(".lb-row").count()
        poster_panel = e2e_page.locator("#lb-container .lb-panel").nth(1)
        poster_rows = poster_panel.locator(".lb-row").count()
        assert worker_rows == 0
        assert poster_rows == 1
        assert "Solo" in (
            (poster_panel.locator(".lb-row .lb-name").first.text_content() or "").strip()
        )

    def test_x07_single_agent_observatory_renders_single_poster(
        self,
        e2e_page: Page,
        e2e_server: str,  # required shared fixture
        single_agent_e2e_server: str,
    ) -> None:
        _ = e2e_server
        observatory = ObservatoryPage(e2e_page, single_agent_e2e_server)
        observatory.navigate()
        e2e_page.wait_for_function("document.querySelectorAll('.vital-item').length >= 7")
        vitals = observatory.get_vitals()
        assert vitals["Active Agents"]["value"] == "1"
        assert vitals["Open Tasks"]["value"] == "1"
        observatory.click_tab("posters")
        e2e_page.wait_for_timeout(250)
        rows = e2e_page.locator("#lb-scroll .lb-row")
        assert rows.count() == 1
        assert "Solo" in ((rows.first.locator(".lb-name").text_content() or "").strip())


class TestCrossPageNavigation:
    def test_x08_landing_to_observatory_navigation(
        self,
        e2e_page: Page,
        e2e_server: str,
    ) -> None:
        landing = LandingPage(e2e_page, e2e_server)
        landing.navigate()
        landing.click_observatory()
        e2e_page.wait_for_url("**/observatory.html")
        assert e2e_page.url.endswith("/observatory.html")

    def test_x09_observatory_to_task_to_home_navigation(
        self,
        e2e_page: Page,
        e2e_server: str,
    ) -> None:
        observatory = ObservatoryPage(e2e_page, e2e_server)
        observatory.navigate()
        e2e_page.locator(".topnav-link:has-text('Task Lifecycle')").click()
        e2e_page.wait_for_url("**/task.html")
        e2e_page.locator(".topnav-link:has-text('Home')").click()
        e2e_page.wait_for_url("**/")
        assert e2e_page.url == f"{e2e_server}/"

    def test_x10_task_to_observatory_navigation(
        self,
        e2e_page: Page,
        e2e_server: str,
    ) -> None:
        task = TaskPage(e2e_page, e2e_server)
        task.navigate(task_id="t-task1")
        e2e_page.wait_for_function(
            "document.querySelector('#phase-content') && "
            "document.querySelector('#phase-content').textContent.trim().length > 0",
        )
        e2e_page.locator(".topnav-link:has-text('Observatory')").click()
        e2e_page.wait_for_url("**/observatory.html")
        assert e2e_page.url.endswith("/observatory.html")


class TestCrossPageBrowserEdgeCases:
    def test_x11_back_forward_navigation_keeps_pages_usable(
        self,
        e2e_page: Page,
        e2e_server: str,
    ) -> None:
        landing = LandingPage(e2e_page, e2e_server)
        landing.navigate()
        landing.click_observatory()
        e2e_page.wait_for_url("**/observatory.html")
        e2e_page.go_back(wait_until="domcontentloaded")
        e2e_page.wait_for_url("**/")
        e2e_page.go_forward(wait_until="domcontentloaded")
        e2e_page.wait_for_url("**/observatory.html")
        assert e2e_page.locator("#vitals-bar").is_visible()

    def test_x12_resize_desktop_to_mobile_keeps_layout_operable(
        self,
        e2e_page: Page,
        e2e_server: str,
    ) -> None:
        landing = LandingPage(e2e_page, e2e_server)
        landing.navigate()
        assert landing.hero_section.is_visible()
        e2e_page.set_viewport_size({"width": 390, "height": 844})
        e2e_page.reload(wait_until="domcontentloaded")
        e2e_page.wait_for_function("document.querySelectorAll('.kpi-cell').length === 5")
        assert landing.hero_section.is_visible()
        observatory = ObservatoryPage(e2e_page, e2e_server)
        observatory.navigate()
        assert observatory.vitals_bar.is_visible()

    def test_x13_tab_switch_visibility_change_does_not_break_feed(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_context,
    ) -> None:
        observatory = ObservatoryPage(e2e_page, e2e_server)
        observatory.navigate()
        e2e_page.wait_for_function("document.querySelectorAll('.feed-btn').length > 0")
        other_tab = e2e_context.new_page()
        try:
            other_tab.goto("about:blank")
            other_tab.bring_to_front()
            e2e_page.bring_to_front()
            observatory.click_filter("TASK")
            assert observatory.get_active_filter() == "TASK"
            assert observatory.live_dot.count() == 1
        finally:
            other_tab.close()

    def test_x14_reload_preserves_task_route_and_content(
        self,
        e2e_page: Page,
        e2e_server: str,
    ) -> None:
        task = TaskPage(e2e_page, e2e_server)
        task.navigate(task_id="t-task1")
        e2e_page.wait_for_function(
            "document.querySelector('#phase-content') && "
            "document.querySelector('#phase-content').textContent.trim().length > 0",
        )
        assert "task_id=t-task1" in e2e_page.url
        before_status = (task.task_status.text_content() or "").strip()
        e2e_page.reload(wait_until="domcontentloaded")
        e2e_page.wait_for_function(
            "document.querySelector('#phase-content') && "
            "document.querySelector('#phase-content').textContent.trim().length > 0",
        )
        after_status = (task.task_status.text_content() or "").strip()
        assert "task_id=t-task1" in e2e_page.url
        assert before_status != ""
        assert after_status != ""
