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
        self._page.wait_for_load_state("domcontentloaded")

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
        self._page.locator("#filter-btns .feed-btn").filter(has_text=filter_type).first.click()

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
