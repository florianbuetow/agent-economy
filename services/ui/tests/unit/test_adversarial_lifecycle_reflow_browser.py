"""Lifecycle-wide deep-link, history, reload, and 200% reflow coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect, sync_playwright
from test_q_task_overview_browser import FAR_FUTURE, _Harness, _open, _task

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _phase_task(status: str) -> dict[str, Any]:
    worker = {"agent_id": "a-worker", "name": "Worker"}
    common: dict[str, Any] = {
        "worker": worker if status not in {"open", "cancelled", "expired"} else None,
        "accepted_price": 60 if status not in {"open", "cancelled", "expired"} else None,
        "deadlines": {
            "bidding_deadline": FAR_FUTURE,
            "acceptance_deadline": FAR_FUTURE,
            "execution_deadline": FAR_FUTURE,
            "review_deadline": FAR_FUTURE,
        },
    }
    if status in {"disputed", "ruled"}:
        common["dispute"] = {
            "claim_id": "disp-1",
            "reason": "Wrong output.",
            "filed_at": "2026-11-04T12:00:00Z",
            "rebuttal_deadline": FAR_FUTURE,
            "rebuttal": {
                "content": "Worker response.",
                "submitted_at": "2026-11-04T12:05:00Z",
            },
            "ruling": (
                {
                    "ruling_id": "disp-1",
                    "worker_pct": 50,
                    "summary": "Split ruling.",
                    "ruled_at": "2026-11-04T12:10:00Z",
                }
                if status == "ruled"
                else None
            ),
        }
    return _task(status, **common)


@pytest.mark.parametrize(
    "status",
    ["open", "accepted", "submitted", "disputed", "approved", "ruled", "cancelled", "expired"],
)
def test_every_lifecycle_phase_survives_history_reload_and_200_percent_reflow(
    browser: Browser,
    status: str,
) -> None:
    """Task context and controls remain readable in every lifecycle phase."""
    page = _open(browser, _Harness(_phase_task(status)))
    status_label = "ACTIVE" if status == "accepted" else status.upper()
    try:
        page.set_viewport_size({"width": 720, "height": 450})
        page.reload()
        expect(page.locator("#task-orientation")).to_contain_text("What happens now")
        expect(page.locator("#task-status")).to_have_text(status_label)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        page.goto("http://task.test/task.html?task_id=t-job#documents")
        page.go_back()
        page.go_forward()
        page.reload()

        expect(page.locator("#task-orientation")).to_be_visible()
        expect(page.locator("#task-status")).to_have_text(status_label)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        for button in page.locator("button:visible").all():
            box = button.bounding_box()
            assert box is not None
            assert box["x"] >= 0
            assert box["x"] + box["width"] <= 721
    finally:
        page.close()
