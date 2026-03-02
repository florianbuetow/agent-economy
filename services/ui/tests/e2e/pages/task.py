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
        self._page.wait_for_load_state("domcontentloaded")

    # --- Phase Strip ---

    @property
    def phase_strip(self) -> Locator:
        """The 7-phase progress bar."""
        return self._page.locator("#phase-strip")

    def get_phase_strip(self) -> list[dict[str, str]]:
        """Return phase steps as list of {label, state}."""
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
        for index, step in enumerate(steps):
            classes = step.get_attribute("class") or ""
            if "active" in classes:
                return index
        return 0

    # --- Phase Navigation ---

    def click_phase(self, phase: int) -> None:
        """Click on a specific phase step (0-6)."""
        self._page.locator(f".phase-step[data-phase='{phase}']").click()

    def click_next(self) -> None:
        """Click the next phase button."""
        button = self._page.locator("#btn-next, .btn-next")
        if button.count() > 0:
            button.first.click()
            return
        self._page.get_by_text("Next").first.click()

    def click_prev(self) -> None:
        """Click the previous phase button."""
        button = self._page.locator("#btn-prev, .btn-prev")
        if button.count() > 0:
            button.first.click()
            return
        self._page.get_by_text("Prev").first.click()

    def click_auto(self) -> None:
        """Toggle auto-play mode."""
        button = self._page.locator("#btn-auto, .btn-auto")
        if button.count() > 0:
            button.first.click()
            return
        self._page.get_by_text("Auto").first.click()

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
        """Return displayed bids as list of {bidder, amount}."""
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
