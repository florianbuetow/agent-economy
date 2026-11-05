"""Two-tab stale acceptance reconciliation through the task page."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect, sync_playwright
from test_q_task_overview_browser import (
    OPERATOR,
    _Harness,
    _open,
    _operation,
    _task,
    _with_operation_id,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, Page

pytestmark = pytest.mark.unit


def _bid(bid_id: str, agent_id: str, amount: int) -> dict[str, Any]:
    return {
        "bid_id": bid_id,
        "bidder": {"agent_id": agent_id, "name": agent_id, "delivery_quality": None},
        "amount": amount,
        "submitted_at": "2026-11-04T12:00:00Z",
        "accepted": False,
    }


class _AcceptanceHarness(_Harness):
    def __init__(self) -> None:
        self.bids = [_bid("bid-a", "a-worker-a", 60), _bid("bid-b", "a-worker-b", 70)]
        super().__init__(
            _task(
                "open",
                lifecycle_version=3,
                poster={"agent_id": OPERATOR, "name": "Operator"},
                bid_count=2,
                bids=list(self.bids),
            )
        )

    def write_reply(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path.endswith("/accept"):
            if self.task["status"] != "open":
                return 409, {
                    "error": "task_version_conflict",
                    "message": "Task lifecycle version differs from the command precondition",
                    "details": {
                        "expected_lifecycle_version": body["expected_lifecycle_version"],
                        "actual_lifecycle_version": self.task["lifecycle_version"],
                        "current_status": self.task["status"],
                    },
                }
            bid_id = path.rsplit("/", 2)[-2]
            bid = next(item for item in self.bids if item["bid_id"] == bid_id)
            self.task = {
                **self.task,
                "status": "accepted",
                "lifecycle_version": 4,
                "worker": {
                    "agent_id": bid["bidder"]["agent_id"],
                    "name": bid["bidder"]["name"],
                },
                "accepted_price": bid["amount"],
                "bids": [{**item, "accepted": item["bid_id"] == bid_id} for item in self.bids],
            }
            reply = _operation("accept_bid", "open", "accepted", "none")
            return 200, _with_operation_id(reply, body)
        return super().write_reply(path, body)


@pytest.fixture(scope="module")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _open_with_bids(browser: Browser, harness: _AcceptanceHarness) -> Page:
    page = _open(browser, harness)
    page.route(
        "**/api/proxy/tasks/t-job/bids",
        lambda route: route.fulfill(
            status=200,
            json={"task_id": "t-job", "bids": harness.bids},
        ),
    )
    page.reload()
    expect(page.locator(".btn-accept-bid")).to_have_count(2)
    return page


def test_two_tabs_accept_different_bids_and_stale_tab_reconciles(browser: Browser) -> None:
    """One explicit bid wins and the stale tab converges after typed conflict."""
    harness = _AcceptanceHarness()
    winner = _open_with_bids(browser, harness)
    stale = _open_with_bids(browser, harness)
    try:
        winner.locator(".btn-accept-bid").first.click()
        expect(winner.locator("#action-error")).to_contain_text("Bid accepted")
        expect(winner.locator("#phase-content")).to_contain_text("60 ©")

        stale.locator(".btn-accept-bid").nth(1).click()
        expect(stale.locator(".operation-recovery")).to_contain_text("Task lifecycle version")
        expect(stale.locator("[data-operation-rejection-reset]")).to_be_visible()
        stale.locator("[data-operation-rejection-reset]").click()

        expect(stale.locator(".btn-accept-bid")).to_have_count(0)
        expect(stale.locator("#phase-content")).to_contain_text("60 ©")
        assert harness.task["worker"]["agent_id"] == "a-worker-a"
        assert harness.task["accepted_price"] == 60
    finally:
        winner.close()
        stale.close()
