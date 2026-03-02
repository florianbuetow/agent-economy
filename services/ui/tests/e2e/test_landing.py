"""Landing page E2E tests (L01-L58)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from .fixtures.seed_db import extend_seed_data
from .helpers.db_helpers import advance_task_status
from .pages.landing import LandingPage

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Page

pytestmark = pytest.mark.e2e


@dataclass(frozen=True)
class LeaderboardAgent:
    """Expected leaderboard row data derived from the fixture DB."""

    name: str
    amount: int
    tasks_count: int
    streak: int


@dataclass(frozen=True)
class LandingSnapshot:
    """Expected landing metrics derived from the fixture DB."""

    gdp_total: int
    active_agents: int
    total_agents: int
    tasks_open: int
    tasks_in_execution: int
    tasks_disputed: int
    tasks_completed_all_time: int
    escrow_locked: int
    spec_quality_percent: int
    workers: list[LeaderboardAgent]
    posters: list[LeaderboardAgent]


def _ensure_extended_seed_data(db_path: Path) -> None:
    """Apply the extended seed data once if not already present."""
    conn = sqlite3.connect(str(db_path))
    try:
        agent_count = int(conn.execute("SELECT COUNT(*) FROM identity_agents").fetchone()[0])
        if agent_count < 10:
            extend_seed_data(conn)
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _ensure_extended_seed(e2e_db_path: Path) -> None:
    """Ensure all landing tests run against the full E2E fixture DB."""
    _ensure_extended_seed_data(e2e_db_path)


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int_from_text(value: str) -> int:
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else 0


def _accelerate_intervals(page: Page) -> None:
    """Speed up setInterval timers for interval-driven UI tests."""
    page.add_init_script(
        """
        (() => {
          const originalSetInterval = window.setInterval;
          window.setInterval = (fn, ms, ...args) => {
            const clamped = Math.min(ms, 1000);
            return originalSetInterval(fn, clamped, ...args);
          };
        })();
        """,
    )


def _load_landing(
    e2e_page: Page,
    e2e_server: str,
    *,
    accelerate_intervals: bool = False,
) -> LandingPage:
    """Open the landing page and wait for all data-driven sections."""
    if accelerate_intervals:
        _accelerate_intervals(e2e_page)
    landing_page = LandingPage(e2e_page, e2e_server)
    landing_page.navigate()
    e2e_page.wait_for_function(
        "document.querySelectorAll('#ticker-track .ticker-item').length >= 24"
    )
    e2e_page.wait_for_function("document.querySelectorAll('.kpi-cell').length >= 5")
    e2e_page.wait_for_function("document.querySelectorAll('.board-cell').length >= 15")
    e2e_page.wait_for_function("document.querySelectorAll('#news-track .bt-item').length >= 16")
    if accelerate_intervals:
        e2e_page.wait_for_timeout(3000)
    else:
        e2e_page.wait_for_function(
            "Array.from(document.querySelectorAll('.kpi-value'))."
            "every((el) => !el.classList.contains('counting'))",
        )
    return landing_page


def _top_ticker_pairs(page: Page) -> list[dict[str, str]]:
    return page.evaluate(
        """
        () => Array.from(document.querySelectorAll('#ticker-track .ticker-item')).map((item) => {
          const spans = item.querySelectorAll('span');
          const symEl = item.querySelector('.sym');
          const chgEl = item.querySelector('.chg');
          return {
            sym: symEl ? symEl.textContent.trim() : '',
            val: spans.length > 1 ? spans[1].textContent.trim() : '',
            chg: chgEl ? chgEl.textContent.trim() : '',
            cls: chgEl ? chgEl.className : ''
          };
        })
        """,
    )


def _top_ticker_value(page: Page, symbol: str) -> str:
    return page.evaluate(
        """
        (sym) => {
          const items = Array.from(document.querySelectorAll('#ticker-track .ticker-item'));
          const item = items.find((node) => {
            const symNode = node.querySelector('.sym');
            return symNode && symNode.textContent.trim() === sym;
          });
          if (!item) return '';
          const spans = item.querySelectorAll('span');
          return spans.length > 1 ? spans[1].textContent.trim() : '';
        }
        """,
        symbol,
    )


def _board_cells_by_label(landing_page: LandingPage) -> dict[str, dict[str, str]]:
    cells = landing_page.get_exchange_cells()
    return {cell["label"]: cell for cell in cells if cell["label"]}


def _leaderboard_panel(page: Page, index: int):
    return page.locator("#lb-container .lb-panel").nth(index)


def _clean_agent_name(value: str) -> str:
    return value.split("\U0001f525", maxsplit=1)[0].strip()


def _insert_open_task(conn: sqlite3.Connection, task_id: str, reward: int) -> None:
    now_iso = _now_iso()
    conn.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, execution_deadline, "
        "review_deadline, escrow_id, worker_id, accepted_bid_id, dispute_reason, ruling_id, "
        "worker_pct, ruling_summary, created_at, accepted_at, submitted_at, approved_at, "
        "cancelled_at, disputed_at, ruled_at, expired_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            "a-alice",
            "Live Inserted Task",
            "Live mutation task spec",
            reward,
            "open",
            86400,
            604800,
            172800,
            now_iso,
            None,
            None,
            f"esc-{task_id}",
            None,
            None,
            None,
            None,
            None,
            None,
            now_iso,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    )
    conn.commit()


def _snapshot_from_db(db_path: Path) -> LandingSnapshot:
    conn = sqlite3.connect(str(db_path))
    try:
        now = datetime.now(tz=UTC)
        since_30d = (now - timedelta(days=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )

        gdp_approved = int(
            conn.execute(
                "SELECT COALESCE(SUM(reward), 0) FROM board_tasks WHERE status = 'approved'",
            ).fetchone()[0],
        )
        gdp_ruled = int(
            conn.execute(
                "SELECT COALESCE(SUM(reward * worker_pct / 100), 0) "
                "FROM board_tasks WHERE status = 'ruled' AND worker_pct IS NOT NULL",
            ).fetchone()[0],
        )
        gdp_total = gdp_approved + gdp_ruled

        active_agents = int(
            conn.execute(
                "SELECT COUNT(DISTINCT agent_id) FROM ("
                "  SELECT poster_id AS agent_id FROM board_tasks "
                "  WHERE created_at >= ? OR accepted_at >= ? OR "
                "submitted_at >= ? OR approved_at >= ? "
                "  UNION "
                "  SELECT worker_id AS agent_id FROM board_tasks "
                "  WHERE worker_id IS NOT NULL "
                "  AND (created_at >= ? OR accepted_at >= ? OR "
                "submitted_at >= ? OR approved_at >= ?)"
                ")",
                (
                    since_30d,
                    since_30d,
                    since_30d,
                    since_30d,
                    since_30d,
                    since_30d,
                    since_30d,
                    since_30d,
                ),
            ).fetchone()[0],
        )
        total_agents = int(conn.execute("SELECT COUNT(*) FROM identity_agents").fetchone()[0])
        tasks_open = int(
            conn.execute("SELECT COUNT(*) FROM board_tasks WHERE status = 'open'").fetchone()[0],
        )
        tasks_in_execution = int(
            conn.execute(
                "SELECT COUNT(*) FROM board_tasks WHERE status IN ('accepted', 'submitted')",
            ).fetchone()[0],
        )
        tasks_disputed = int(
            conn.execute(
                "SELECT COUNT(*) FROM board_tasks WHERE status IN ('disputed', 'ruled')",
            ).fetchone()[0],
        )
        tasks_completed_all_time = int(
            conn.execute("SELECT COUNT(*) FROM board_tasks WHERE status = 'approved'").fetchone()[
                0
            ],
        )
        escrow_locked = int(
            conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM bank_escrow WHERE status = 'locked'",
            ).fetchone()[0],
        )

        spec_total = int(
            conn.execute(
                "SELECT COUNT(*) FROM reputation_feedback "
                "WHERE category = 'spec_quality' AND visible = 1",
            ).fetchone()[0],
        )
        spec_es = int(
            conn.execute(
                "SELECT COUNT(*) FROM reputation_feedback "
                "WHERE category = 'spec_quality' AND visible = 1 "
                "AND rating = 'extremely_satisfied'",
            ).fetchone()[0],
        )
        spec_quality_percent = round((spec_es / spec_total) * 100) if spec_total > 0 else 0

        agents = conn.execute("SELECT agent_id, name FROM identity_agents").fetchall()
        workers: list[LeaderboardAgent] = []
        posters: list[LeaderboardAgent] = []

        for agent_id, name in agents:
            earned = int(
                conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM bank_transactions "
                    "WHERE account_id = ? AND type = 'escrow_release'",
                    (agent_id,),
                ).fetchone()[0],
            )
            spent = int(
                conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM bank_transactions "
                    "WHERE account_id = ? AND type = 'escrow_lock'",
                    (agent_id,),
                ).fetchone()[0],
            )
            tasks_completed = int(
                conn.execute(
                    "SELECT COUNT(*) FROM board_tasks WHERE worker_id = ? AND status = 'approved'",
                    (agent_id,),
                ).fetchone()[0],
            )
            tasks_posted = int(
                conn.execute(
                    "SELECT COUNT(*) FROM board_tasks WHERE poster_id = ?",
                    (agent_id,),
                ).fetchone()[0],
            )

            streak_rows = conn.execute(
                "SELECT status FROM board_tasks "
                "WHERE worker_id = ? "
                "AND status IN ('approved', 'disputed', 'ruled', 'cancelled') "
                "ORDER BY COALESCE(approved_at, submitted_at, created_at) DESC",
                (agent_id,),
            ).fetchall()
            streak = 0
            for row in streak_rows:
                if row[0] == "approved":
                    streak += 1
                else:
                    break

            if earned >= spent:
                workers.append(
                    LeaderboardAgent(
                        name=name,
                        amount=earned,
                        tasks_count=tasks_completed,
                        streak=streak,
                    ),
                )
            else:
                posters.append(
                    LeaderboardAgent(
                        name=name,
                        amount=spent,
                        tasks_count=tasks_posted,
                        streak=streak,
                    ),
                )

        workers.sort(key=lambda agent: agent.amount, reverse=True)
        posters.sort(key=lambda agent: agent.amount, reverse=True)

        return LandingSnapshot(
            gdp_total=gdp_total,
            active_agents=active_agents,
            total_agents=total_agents,
            tasks_open=tasks_open,
            tasks_in_execution=tasks_in_execution,
            tasks_disputed=tasks_disputed,
            tasks_completed_all_time=tasks_completed_all_time,
            escrow_locked=escrow_locked,
            spec_quality_percent=spec_quality_percent,
            workers=workers,
            posters=posters,
        )
    finally:
        conn.close()


class TestLandingNavigation:
    def test_l01_root_route_loads_landing(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert landing_page.hero_section.count() == 1
        assert e2e_page.url == f"{e2e_server}/"

    def test_l02_observatory_button_navigates(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        landing_page.click_observatory()
        e2e_page.wait_for_url("**/observatory.html")
        assert e2e_page.url.endswith("/observatory.html")

    def test_l03_enter_economy_button_navigates(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        landing_page.click_enter_economy()
        e2e_page.wait_for_url("**/observatory.html")
        assert e2e_page.url.endswith("/observatory.html")

    def test_l04_post_task_cta_navigates(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        post_task_button = e2e_page.locator("#cta button:has-text('Post a Task')")
        post_task_button.scroll_into_view_if_needed()
        post_task_button.click()
        e2e_page.wait_for_url("**/task.html")
        assert e2e_page.url.endswith("/task.html")


class TestLandingTopTicker:
    def test_l05_top_ticker_track_visible(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert landing_page.ticker_track.count() == 1
        assert landing_page.ticker_track.is_visible()

    def test_l06_top_ticker_has_24_items(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert len(landing_page.get_ticker_items()) == 24

    def test_l07_top_ticker_contains_expected_symbols(
        self, e2e_page: Page, e2e_server: str
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        symbols = [pair["sym"] for pair in _top_ticker_pairs(e2e_page)]
        assert "GDP/TOTAL" in symbols
        assert "TASK/OPEN" in symbols
        assert "SPEC/QUAL" in symbols
        assert "GDP/AGENT" in symbols

    def test_l08_top_ticker_open_tasks_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        assert _top_ticker_value(e2e_page, "TASK/OPEN") == str(snapshot.tasks_open)

    def test_l09_top_ticker_gdp_total_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        assert _top_ticker_value(e2e_page, "GDP/TOTAL") == f"{snapshot.gdp_total:,}"

    def test_l10_top_ticker_stream_is_doubled(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        symbols = [pair["sym"] for pair in _top_ticker_pairs(e2e_page)]
        assert len(symbols) == 24
        assert symbols[:12] == symbols[12:]


class TestLandingHero:
    def test_l11_hero_section_visible(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert landing_page.hero_section.is_visible()

    def test_l12_hero_title_copy(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        title = (landing_page.hero_title.text_content() or "").strip()
        assert "Watch an Economy of Agents" in title
        assert "Real Time" in title

    def test_l13_hero_subtitle_copy(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        subtitle = (landing_page.hero_subtitle.text_content() or "").strip()
        assert "Autonomous AI agents earn, spend, bid, and compete for work." in subtitle

    def test_l14_hero_cta_buttons_present(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        assert e2e_page.locator(".hero .cta-row button").count() == 2
        assert e2e_page.locator("text=Enter the Economy").count() >= 1
        assert e2e_page.locator("text=Watch Live Agents").count() == 1


class TestLandingKPIStrip:
    def test_l15_kpi_strip_visible(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert landing_page.kpi_strip.is_visible()

    def test_l16_kpi_strip_has_five_cells(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        assert e2e_page.locator(".kpi-cell").count() == 5

    def test_l17_kpi_labels_match_expected(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        labels = [
            value.strip() for value in e2e_page.locator(".kpi-cell .kpi-label").all_text_contents()
        ]
        assert labels == [
            "Economy GDP",
            "Active Agents",
            "Tasks Completed",
            "Spec Quality",
            "Economy Phase",
        ]

    def test_l18_kpi_gdp_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        values = landing_page.get_kpi_values()
        assert values["Economy GDP"] == f"{snapshot.gdp_total:,} \u00a9"

    def test_l19_kpi_active_agents_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        values = landing_page.get_kpi_values()
        assert values["Active Agents"] == str(snapshot.active_agents)

    def test_l20_kpi_tasks_completed_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        values = landing_page.get_kpi_values()
        assert values["Tasks Completed"] == f"{snapshot.tasks_completed_all_time}+"

    def test_l21_kpi_spec_quality_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        values = landing_page.get_kpi_values()
        assert values["Spec Quality"] == f"{snapshot.spec_quality_percent}%"

    def test_l22_kpi_phase_is_uppercase_text(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        values = landing_page.get_kpi_values()
        phase = values["Economy Phase"]
        assert phase
        assert phase == phase.upper()

    def test_l23_kpi_notes_present_for_all_cells(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        notes = [
            note.strip() for note in e2e_page.locator(".kpi-cell .kpi-note").all_text_contents()
        ]
        assert len(notes) == 5
        assert all(note for note in notes)


class TestLandingExchangeBoard:
    def test_l24_exchange_board_grid_visible(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert landing_page.board_grid.is_visible()

    def test_l25_exchange_board_has_15_cells(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert len(landing_page.get_exchange_cells()) == 15

    def test_l26_exchange_board_clock_in_utc(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        clock_value = (landing_page.board_clock.text_content() or "").strip()
        assert clock_value.endswith(" UTC")
        assert ":" in clock_value

    def test_l27_exchange_board_has_required_labels(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        labels = {cell["label"] for cell in landing_page.get_exchange_cells()}
        assert {
            "GDP Total",
            "Open Tasks",
            "In Execution",
            "Disputes Active",
            "Escrow Locked",
        } <= labels

    def test_l28_exchange_board_gdp_total_matches_seed(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        cells = _board_cells_by_label(landing_page)
        assert cells["GDP Total"]["value"] == f"{snapshot.gdp_total:,} \u00a9"

    def test_l29_exchange_board_open_tasks_matches_seed(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        cells = _board_cells_by_label(landing_page)
        assert cells["Open Tasks"]["value"] == str(snapshot.tasks_open)

    def test_l30_exchange_board_in_execution_matches_seed(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        cells = _board_cells_by_label(landing_page)
        assert cells["In Execution"]["value"] == str(snapshot.tasks_in_execution)

    def test_l31_exchange_board_disputes_active_matches_seed(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        cells = _board_cells_by_label(landing_page)
        assert cells["Disputes Active"]["value"] == str(snapshot.tasks_disputed)

    def test_l32_exchange_board_escrow_locked_matches_seed(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        cells = _board_cells_by_label(landing_page)
        assert cells["Escrow Locked"]["value"] == f"{snapshot.escrow_locked:,} \u00a9"

    def test_l33_exchange_board_each_cell_has_sparkline(
        self, e2e_page: Page, e2e_server: str
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        spark_counts = e2e_page.evaluate(
            """
            () => Array.from(document.querySelectorAll('.board-cell')).map((cell) => {
              return cell.querySelectorAll('.cell-spark .bar').length;
            })
            """,
        )
        assert len(spark_counts) == 15
        assert all(count > 0 for count in spark_counts)


class TestLandingHowItWorks:
    def test_l34_how_it_works_has_five_steps(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        assert e2e_page.locator(".how-step").count() == 5

    def test_l35_how_it_works_step_order(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        labels = [
            value.strip() for value in e2e_page.locator(".how-step .step-label").all_text_contents()
        ]
        assert labels == ["Post", "Bid", "Contract", "Deliver", "Judge"]

    def test_l36_how_it_works_descriptions_non_empty(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        descriptions = [
            value.strip() for value in e2e_page.locator(".how-step .step-desc").all_text_contents()
        ]
        assert len(descriptions) == 5
        assert all(description for description in descriptions)


class TestLandingMarketStory:
    def test_l37_story_text_visible(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert landing_page.story_text.is_visible()
        assert (landing_page.story_text.text_content() or "").strip()

    def test_l38_story_initial_copy_reflects_seed_counts(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        story = (landing_page.story_text.text_content() or "").strip()
        assert f"{snapshot.active_agents} agents competing" in story
        assert f"{snapshot.tasks_open} open tasks" in story

    def test_l39_story_title_present(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        title = (e2e_page.locator(".story-title").text_content() or "").strip()
        assert title == "Today's Market Story"

    def test_l40_story_link_scrolls_to_exchange_board(
        self, e2e_page: Page, e2e_server: str
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        e2e_page.locator(".story-link").click()
        e2e_page.wait_for_timeout(700)
        board_box = e2e_page.locator("#board").bounding_box()
        assert board_box is not None
        viewport = e2e_page.viewport_size
        assert viewport is not None
        assert board_box["y"] < viewport["height"]

    def test_l41_story_rotates_over_time(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server, accelerate_intervals=True)
        initial_story = (landing_page.story_text.text_content() or "").strip()
        e2e_page.wait_for_function(
            "(initial) => document.querySelector('#story-text') && "
            "document.querySelector('#story-text').textContent.trim() !== initial",
            arg=initial_story,
            timeout=5000,
        )
        updated_story = (landing_page.story_text.text_content() or "").strip()
        assert updated_story != initial_story


class TestLandingLeaderboard:
    def test_l42_leaderboard_container_visible(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert landing_page.lb_container.is_visible()

    def test_l43_leaderboard_has_two_panels(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        assert e2e_page.locator("#lb-container .lb-panel").count() == 2

    def test_l44_worker_panel_title(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        title = (
            _leaderboard_panel(e2e_page, 0).locator(".lb-panel-title").text_content() or ""
        ).strip()
        assert "Top Workers" in title

    def test_l45_poster_panel_title(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        title = (
            _leaderboard_panel(e2e_page, 1).locator(".lb-panel-title").text_content() or ""
        ).strip()
        assert "Top Posters" in title

    def test_l46_worker_row_count_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        assert _leaderboard_panel(e2e_page, 0).locator(".lb-row").count() == len(snapshot.workers)

    def test_l47_poster_row_count_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        assert _leaderboard_panel(e2e_page, 1).locator(".lb-row").count() == len(snapshot.posters)

    def test_l48_top_worker_name_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        name_text = (
            _leaderboard_panel(e2e_page, 0)
            .locator(".lb-row")
            .first.locator(".lb-name")
            .text_content()
            or ""
        )
        assert _clean_agent_name(name_text) == snapshot.workers[0].name

    def test_l49_top_worker_amount_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        amount_text = (
            _leaderboard_panel(e2e_page, 0)
            .locator(".lb-row")
            .first.locator(".amount")
            .text_content()
            or ""
        ).strip()
        assert amount_text == f"{snapshot.workers[0].amount:,} \u00a9"

    def test_l50_top_poster_name_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        name_text = (
            _leaderboard_panel(e2e_page, 1)
            .locator(".lb-row")
            .first.locator(".lb-name")
            .text_content()
            or ""
        )
        assert _clean_agent_name(name_text) == snapshot.posters[0].name

    def test_l51_top_poster_amount_matches_seed(
        self, e2e_page: Page, e2e_server: str, e2e_db_path: Path
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        amount_text = (
            _leaderboard_panel(e2e_page, 1)
            .locator(".lb-row")
            .first.locator(".amount")
            .text_content()
            or ""
        ).strip()
        assert amount_text == f"{snapshot.posters[0].amount:,} \u00a9"

    def test_l52_leaderboard_ranks_sequential(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        for panel_index in (0, 1):
            panel = _leaderboard_panel(e2e_page, panel_index)
            rank_texts = [value.strip() for value in panel.locator(".lb-rank").all_text_contents()]
            expected = [str(index) for index in range(1, len(rank_texts) + 1)]
            assert rank_texts == expected


class TestLandingBottomTicker:
    def test_l53_bottom_ticker_visible(self, e2e_page: Page, e2e_server: str) -> None:
        landing_page = _load_landing(e2e_page, e2e_server)
        assert landing_page.news_track.is_visible()

    def test_l54_bottom_ticker_has_16_items(self, e2e_page: Page, e2e_server: str) -> None:
        _load_landing(e2e_page, e2e_server)
        assert e2e_page.locator("#news-track .bt-item").count() == 16

    def test_l55_bottom_ticker_contains_seed_metrics(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        _load_landing(e2e_page, e2e_server)
        snapshot = _snapshot_from_db(e2e_db_path)
        text = " ".join(e2e_page.locator("#news-track .bt-item").all_text_contents())
        assert f"{snapshot.active_agents} agents active" in text
        assert f"{snapshot.tasks_open} open tasks awaiting bids" in text
        assert f"{snapshot.escrow_locked:,} \u00a9 locked" in text


class TestLandingLiveUpdates:
    def test_l56_kpi_updates_after_status_change(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        landing_page = _load_landing(e2e_page, e2e_server, accelerate_intervals=True)
        before = landing_page.get_kpi_values()
        before_completed = _int_from_text(before["Tasks Completed"])

        conn = sqlite3.connect(str(e2e_db_path))
        try:
            advance_task_status(conn, task_id="t-task-e3", new_status="approved")
        finally:
            conn.close()

        expected = before_completed + 1
        e2e_page.wait_for_function(
            "(target) => {"
            "  const el = document.getElementById('kpi-2');"
            "  if (!el) return false;"
            "  const value = parseInt((el.textContent || '').replace(/[^0-9]/g, ''), 10);"
            "  return value === target;"
            "}",
            arg=expected,
            timeout=8000,
        )
        after_text = (e2e_page.locator("#kpi-2").text_content() or "").strip()
        assert _int_from_text(after_text) == expected

    def test_l57_top_ticker_updates_after_new_open_task(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        _load_landing(e2e_page, e2e_server, accelerate_intervals=True)
        before_open = _int_from_text(_top_ticker_value(e2e_page, "TASK/OPEN"))

        conn = sqlite3.connect(str(e2e_db_path))
        try:
            _insert_open_task(conn, task_id="t-live-l57", reward=111)
        finally:
            conn.close()

        expected = before_open + 1
        e2e_page.wait_for_function(
            "(target) => {"
            "  const items = Array.from(document.querySelectorAll('#ticker-track .ticker-item'));"
            "  const item = items.find((node) => {"
            "    const sym = node.querySelector('.sym');"
            "    return sym && sym.textContent.trim() === 'TASK/OPEN';"
            "  });"
            "  if (!item) return false;"
            "  const spans = item.querySelectorAll('span');"
            "  if (spans.length < 2) return false;"
            "  const value = parseInt((spans[1].textContent || '').replace(/[^0-9]/g, ''), 10);"
            "  return value === target;"
            "}",
            arg=expected,
            timeout=8000,
        )
        assert _int_from_text(_top_ticker_value(e2e_page, "TASK/OPEN")) == expected

    def test_l58_leaderboard_updates_after_earnings_change(
        self,
        e2e_page: Page,
        e2e_server: str,
        e2e_db_path: Path,
    ) -> None:
        _load_landing(e2e_page, e2e_server, accelerate_intervals=True)
        top_worker_amount_text = (
            _leaderboard_panel(e2e_page, 0)
            .locator(".lb-row")
            .first.locator(".amount")
            .text_content()
            or ""
        )
        before_amount = _int_from_text(top_worker_amount_text)

        conn = sqlite3.connect(str(e2e_db_path))
        try:
            conn.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("tx-live-l58", "a-bob", "escrow_release", 500, 1700, "live_l58", _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

        expected_amount = before_amount + 500
        e2e_page.wait_for_function(
            "(target) => {"
            "  const panel = document.querySelectorAll('#lb-container .lb-panel')[0];"
            "  if (!panel) return false;"
            "  const amount = panel.querySelector('.lb-row .amount');"
            "  if (!amount) return false;"
            "  const value = parseInt((amount.textContent || '').replace(/[^0-9]/g, ''), 10);"
            "  return value === target;"
            "}",
            arg=expected_amount,
            timeout=8000,
        )
        amount_after_text = (
            _leaderboard_panel(e2e_page, 0)
            .locator(".lb-row")
            .first.locator(".amount")
            .text_content()
            or ""
        )
        assert _int_from_text(amount_after_text) == expected_amount
