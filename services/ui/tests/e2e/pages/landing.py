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
        return [(step.text_content() or "").strip() for step in steps]

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
