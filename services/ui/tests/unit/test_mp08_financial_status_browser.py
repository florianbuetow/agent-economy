"""Real task.js must show receipt-backed settlement independently of task status."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect, sync_playwright

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, Error, Page, Route

pytestmark = pytest.mark.unit
_TASK_ID = "t-mp08-browser"
_OPERATION_ID = "op-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_TASK_SCRIPT = Path(__file__).resolve().parents[2] / "data/web/assets/task.js"
_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<div id="panel-title"></div><div id="task-status"></div>
<div id="phase-strip"><button class="phase-step" data-phase="0">Post</button></div>
<main id="phase-content"></main><div id="feed-scroll"></div><div id="ticker-track"></div>
<script src="/assets/task.js"></script></body></html>"""


@pytest.fixture(scope="module")
def settlement_browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def settlement_page(settlement_browser: Browser) -> Generator[Page, None, None]:
    context = settlement_browser.new_context()
    try:
        yield context.new_page()
    finally:
        context.close()


def _effect(
    state: str, receipt: str, outcome: str | None, projection_pending: bool
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "settlement_state": state,
        "receipt_state": receipt,
        "outcome": outcome,
        "operation_id": None if receipt == "historical" or state == "locked" else _OPERATION_ID,
        "receipt_reference": _OPERATION_ID if receipt == "confirmed" else None,
        "business_projection_pending": projection_pending,
        "pending_target_status": "approved" if projection_pending else None,
    }


def _open_task(
    page: Page,
    status: str,
    effect: dict[str, object] | None,
    extra: dict[str, object] | None = None,
) -> dict[str, Any]:
    page_errors: list[str] = []

    def capture_page_error(error: Error) -> None:
        page_errors.append(str(error))

    page.on("pageerror", capture_page_error)
    task: dict[str, Any] = {
        "task_id": _TASK_ID,
        "title": "Financial recovery status",
        "spec": "Display the confirmed financial outcome.",
        "reward": 100,
        "status": status,
        "lifecycle_version": 3,
        "poster": {"agent_id": "a-poster", "name": "Poster"},
        "worker": {"agent_id": "a-worker", "name": "Worker"},
        "bid_count": 0,
        "bids": [],
        "assets": [],
        "feedback": [{"from_agent_name": "Poster", "rating": "satisfied", "comment": "Recorded"}],
        "dispute": None,
        "deadlines": {
            "bidding_deadline": None,
            "execution_deadline": None,
            "review_deadline": None,
        },
        "timestamps": {
            "created_at": "2026-10-22T00:00:00Z",
            "accepted_at": None,
            "submitted_at": None,
            "approved_at": None,
        },
    }
    if effect is not None:
        task["financial_effect"] = effect
    task.update(extra or {})
    if status == "ruled":
        task["dispute"] = {
            "ruling": {"worker_pct": 60, "summary": "Split the fixed reward as ruled."}
        }
    page.add_init_script("""
        window.ATE = {
          TASK_STATUS_BADGE: {},
          escapeHTML: value => String(value).replace(/[&<>"']/g, c =>
            ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c])),
          agentColor: () => '#00ffff', fetchMetrics: async () => ({}),
          fetchAgents: async () => [], buildBottomTicker: () => {},
          startMetricsPolling: () => {}, fetchEvents: async () => ({events: []}),
          connectSSE: () => ({close: () => {}})
        };
    """)

    def respond(route: Route) -> None:
        url = route.request.url
        if url.endswith("/assets/task.js"):
            route.fulfill(
                status=200, content_type="application/javascript", body=_TASK_SCRIPT.read_text()
            )
        elif url.endswith(f"/api/tasks/{_TASK_ID}"):
            route.fulfill(status=200, json=task)
        elif url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-observer"})
        elif "/task.html?" in url:
            route.fulfill(status=200, content_type="text/html", body=_HTML)
        else:
            route.abort()

    page.route("**/*", respond)
    page.goto(f"http://mp08-ui.test/task.html?task_id={_TASK_ID}")
    expect(page.locator(".escrow-bar")).to_be_visible()
    assert page_errors == [], (
        "Task rendering must reach settlement assertions without script errors"
    )
    return task


@pytest.mark.parametrize(
    ("state", "label"),
    [("pending", "PENDING"), ("attention_required", "ATTENTION"), ("conflict", "CONFLICT")],
)
def test_approved_business_status_never_claims_unconfirmed_money(
    settlement_page: Page, state: str, label: str
) -> None:
    _open_task(settlement_page, "approved", _effect(state, "absent", None, False))
    bar = settlement_page.locator(".escrow-bar")
    text = bar.inner_text()
    assert "SETTLED" not in text and "RELEASED" not in text and "PAID" not in text
    assert label in text


@pytest.mark.parametrize(
    ("status", "outcome", "label"),
    [
        ("approved", "worker_paid", "PAID"),
        ("cancelled", "poster_refunded", "REFUNDED"),
        ("ruled", "split", "SPLIT"),
    ],
)
def test_confirmed_outcome_is_visible_even_outside_the_settlement_phase(
    settlement_page: Page, status: str, outcome: str, label: str
) -> None:
    _open_task(settlement_page, status, _effect("complete", "confirmed", outcome, False))
    expect(settlement_page.locator(".escrow-bar")).to_contain_text(label)
    settlement_page.locator('.phase-step[data-phase="0"]').click()
    expect(settlement_page.locator(".escrow-bar")).to_contain_text(label)


def test_paid_receipt_with_unfinished_projection_is_labeled_explicitly(
    settlement_page: Page,
) -> None:
    _open_task(settlement_page, "submitted", _effect("pending", "confirmed", "worker_paid", True))
    expect(settlement_page.locator(".escrow-bar")).to_contain_text("PAID · TASK UPDATE PENDING")


def test_pre_receipt_history_does_not_invent_modern_confirmation(settlement_page: Page) -> None:
    _open_task(
        settlement_page, "approved", _effect("historical", "historical", "worker_paid", False)
    )
    expect(settlement_page.locator(".escrow-bar")).to_contain_text("HISTORICAL")


@pytest.mark.parametrize("malformation", ["missing", "wrong_receipt", "receipt_without_outcome"])
def test_missing_or_inconsistent_financial_projection_does_not_claim_payment(
    settlement_page: Page, malformation: str
) -> None:
    effect = _effect("complete", "confirmed", "worker_paid", False)
    if malformation == "wrong_receipt":
        effect["receipt_reference"] = "op-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    elif malformation == "receipt_without_outcome":
        effect["outcome"] = None
    _open_task(settlement_page, "approved", None if malformation == "missing" else effect)
    text = settlement_page.locator(".escrow-bar").inner_text()
    assert "PAID" not in text and "SETTLED" not in text and "RELEASED" not in text


def test_unsettled_open_task_remains_visibly_locked(settlement_page: Page) -> None:
    _open_task(settlement_page, "open", _effect("locked", "absent", None, False))
    expect(settlement_page.locator(".escrow-bar")).to_contain_text("LOCKED")


def test_unconfirmed_ruling_does_not_display_settled_payout_amounts(settlement_page: Page) -> None:
    _open_task(settlement_page, "ruled", _effect("attention_required", "absent", None, False))
    expect(settlement_page.locator(".payout-box")).to_have_count(0)


def test_confirmed_split_rounds_worker_share_down_and_preserves_total(
    settlement_page: Page,
) -> None:
    task = _open_task(settlement_page, "ruled", _effect("complete", "confirmed", "split", False))
    task["reward"] = 101
    task["dispute"]["ruling"]["worker_pct"] = 50
    settlement_page.reload()
    expect(settlement_page.locator(".payout-box")).to_have_count(2)
    amounts = settlement_page.locator(".payout-amount").all_text_contents()
    assert amounts == ["51 ©", "50 ©"]


def test_ruling_settlement_shows_the_receipt_amounts_on_the_accepted_price(
    settlement_page: Page,
) -> None:
    """Q-M2: a 60% ruling on an accepted price of 90 pays 54 and refunds 46 of 100."""
    _open_task(
        settlement_page,
        "ruled",
        _effect("complete", "confirmed", "split", False),
        {"accepted_price": 90, "worker_payment": 54, "poster_refund": 46},
    )
    settlement_page.locator(".phase-step").last.click()
    card = settlement_page.locator(".settlement-card")
    expect(card).to_contain_text("54")
    expect(card).to_contain_text("46")
    expect(card).not_to_contain_text("60 ")
