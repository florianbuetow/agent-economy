"""Deterministic rapid-activation checks for task mutation controls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import sync_playwright
from test_q_task_overview_browser import (
    FAR_FUTURE,
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


@pytest.fixture(scope="module")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _bid() -> dict[str, Any]:
    return {
        "bid_id": "bid-1",
        "bidder": {"agent_id": "a-worker", "name": "Worker", "delivery_quality": None},
        "amount": 20,
        "submitted_at": "2026-11-04T12:00:00Z",
        "accepted": False,
    }


def _asset() -> dict[str, Any]:
    return {
        "asset_id": "asset-1",
        "uploader_id": OPERATOR,
        "filename": "answer.txt",
        "content_type": "text/plain",
        "size_bytes": 6,
        "content_hash": "a" * 64,
        "uploaded_at": "2026-11-04T12:00:00Z",
        "role": "output",
    }


def _case(name: str) -> tuple[dict[str, Any], str]:
    poster = {"agent_id": OPERATOR, "name": "Operator"}
    worker = {"agent_id": OPERATOR, "name": "Operator"}
    submitted = _task("submitted", poster=poster, worker={"agent_id": "a-worker", "name": "Worker"})
    cases: dict[str, tuple[dict[str, Any], str]] = {
        "accept": (_task("open", poster=poster, bid_count=1, bids=[_bid()]), ".btn-accept-bid"),
        "approve": (submitted, "#btn-approve"),
        "dispute": (submitted, "#btn-submit-dispute"),
        "cancel": (_task("open", poster=poster), "#btn-cancel-task"),
        "bid": (_task("open"), "#btn-submit-bid"),
        "upload": (_task("accepted", worker=worker, assets=[_asset()]), "#btn-upload-asset"),
        "submit": (
            _task(
                "accepted",
                worker=worker,
                assets=[_asset()],
            ),
            "#btn-submit-deliverable",
        ),
        "rebuttal": (
            _task(
                "disputed",
                worker=worker,
                dispute={
                    "claim_id": "disp-1",
                    "reason": "Wrong output.",
                    "filed_at": "2026-11-04T12:00:00Z",
                    "rebuttal_deadline": FAR_FUTURE,
                    "rebuttal": None,
                    "ruling": None,
                },
            ),
            "#btn-submit-rebuttal",
        ),
        "feedback": (
            _task(
                "approved",
                poster=poster,
                worker={"agent_id": "a-worker", "name": "Worker"},
            ),
            "#btn-submit-feedback",
        ),
    }
    try:
        return cases[name]
    except KeyError as exc:
        raise AssertionError(f"Unknown mutation case: {name}") from exc


def _prepare(page: Page, name: str) -> None:
    if name == "dispute":
        page.locator("#btn-dispute-show").click()
        page.locator("#f-dispute-reason").fill("Wrong output.")
    elif name == "bid":
        page.locator("#f-bid-amount").fill("20")
    elif name == "upload":
        page.locator("#f-upload-file").set_input_files(
            {"name": "answer.txt", "mimeType": "text/plain", "buffer": b"answer"}
        )
    elif name == "rebuttal":
        page.locator("#f-rebuttal").fill("The delivery met the terms.")


def _install_delayed_posts(page: Page) -> None:
    page.add_init_script(
        """
        window.__mutationPosts = [];
        window.__releaseMutations = [];
        const originalFetch = window.fetch;
        window.fetch = function(url, options) {
          if (options && String(options.method || '').toUpperCase() === 'POST') {
            window.__mutationPosts.push({url: String(url), body: options.body || null});
            return new Promise(function(resolve) { window.__releaseMutations.push(resolve); });
          }
          return originalFetch.apply(this, arguments);
        };
        """
    )


def _release_delayed_posts(page: Page) -> None:
    page.evaluate(
        """() => window.__releaseMutations.splice(0).forEach(function(resolve) {
          resolve(new Response(JSON.stringify({
            error: 'dependency_unavailable', message: 'Injected delayed response.', details: {}
          }), {status: 503, headers: {'Content-Type': 'application/json'}}));
        })"""
    )


@pytest.mark.parametrize(
    "name",
    ["accept", "approve", "dispute", "cancel", "bid", "upload", "submit", "rebuttal", "feedback"],
)
def test_rapid_activation_sends_one_request_and_keeps_focus(browser: Browser, name: str) -> None:
    """Synchronous disabling gates duplicate clicks while one request is delayed."""
    task, selector = _case(name)
    page = _open(browser, _Harness(task))
    try:
        _install_delayed_posts(page)
        if name == "accept":
            page.route(
                "**/api/proxy/tasks/t-job/bids",
                lambda route: route.fulfill(
                    status=200, json={"task_id": "t-job", "bids": [_bid()]}
                ),
            )
        page.reload()
        page.locator("#task-orientation").wait_for()
        _prepare(page, name)

        page.evaluate(
            """selector => {
              const button = document.querySelector(selector);
              button.focus();
              button.click();
              button.click();
            }""",
            selector,
        )

        page.wait_for_function("window.__mutationPosts.length === 1")
        assert page.evaluate("window.__mutationPosts.length") == 1
        assert page.locator(selector).is_disabled()
        assert page.locator(
            ".btn-accept-bid, #btn-approve, #btn-dispute-show, #btn-submit-dispute, "
            "#btn-cancel-task, #btn-submit-bid, #btn-upload-asset, #btn-submit-deliverable, "
            "#btn-submit-rebuttal, #btn-submit-feedback"
        ).evaluate_all("elements => elements.every(element => element.disabled)")
        notice = page.locator("#action-error")
        assert page.evaluate("document.activeElement === document.querySelector('#action-error')")
        assert notice.get_attribute("role") == "status"
        assert "action-pending" in (notice.get_attribute("class") or "")
        assert "Sending" in notice.inner_text()
        _release_delayed_posts(page)
        durable = {"accept", "approve", "dispute", "cancel", "bid", "submit"}
        if name in durable:
            recovery = page.locator("[data-operation-recovery]")
            recovery.wait_for()
            assert recovery.evaluate("element => document.activeElement === element")
            assert page.locator(".operation-recovery").get_attribute("role") == "alert"
        else:
            page.wait_for_function(
                "document.querySelector('#action-error').getAttribute('role') === 'alert'"
            )
            assert page.evaluate(
                "document.activeElement === document.querySelector('#action-error')"
            )
            assert "action-pending" not in (notice.get_attribute("class") or "")
    finally:
        page.close()


def test_pending_approval_blocks_conflicting_dispute_but_exact_retry_remains_available(
    browser: Browser,
) -> None:
    """A saved approval cannot be silently reused by a later dispute click."""
    task, _ = _case("approve")
    page = _open(browser, _Harness(task))
    try:
        _install_delayed_posts(page)
        page.reload()
        page.locator("#btn-dispute-show").click()
        page.locator("#f-dispute-reason").fill("Wrong output.")
        page.locator("#btn-approve").click()
        page.wait_for_function("window.__mutationPosts.length === 1")

        posts = page.evaluate(
            """async () => {
              document.querySelector('#btn-submit-dispute').click();
              await Promise.resolve();
              await Promise.resolve();
              return window.__mutationPosts;
            }"""
        )
        assert len(posts) == 1
        assert posts[0]["url"].endswith("/approve")
        assert page.locator("#btn-submit-dispute").is_disabled()

        _release_delayed_posts(page)
        recovery = page.locator("[data-operation-recovery]")
        recovery.wait_for()
        recovery.click()
        page.wait_for_function("window.__mutationPosts.length === 2")
        replay = page.evaluate("window.__mutationPosts")
        assert replay[1] == replay[0]
        _release_delayed_posts(page)
    finally:
        page.close()


def test_pending_bid_acceptance_blocks_different_bid(browser: Browser) -> None:
    """A second bid control cannot replay the first bid's saved acceptance."""
    poster = {"agent_id": OPERATOR, "name": "Operator"}
    bids = [
        _bid(),
        {
            **_bid(),
            "bid_id": "bid-2",
            "amount": 30,
            "bidder": {**_bid()["bidder"], "agent_id": "a-worker-2"},
        },
    ]
    harness = _Harness(_task("open", poster=poster, bid_count=2, bids=bids))
    page = _open(browser, harness)
    try:
        _install_delayed_posts(page)
        page.route(
            "**/api/proxy/tasks/t-job/bids",
            lambda route: route.fulfill(status=200, json={"task_id": "t-job", "bids": bids}),
        )
        harness.task["bids"] = bids
        page.reload()
        buttons = page.locator(".btn-accept-bid")
        buttons.first.wait_for()
        assert buttons.count() == 2
        buttons.first.click()
        page.wait_for_function("window.__mutationPosts.length === 1")

        posts = page.evaluate(
            """async () => {
              document.querySelectorAll('.btn-accept-bid')[1].click();
              await Promise.resolve();
              await Promise.resolve();
              return window.__mutationPosts;
            }"""
        )
        assert len(posts) == 1
        assert posts[0]["url"].endswith("/bids/bid-1/accept")
        assert buttons.nth(1).is_disabled()
        _release_delayed_posts(page)
    finally:
        page.close()


def test_failed_upload_restores_each_controls_original_eligibility(browser: Browser) -> None:
    """A request failure must not enable a control that was already unavailable."""
    task, _ = _case("upload")
    page = _open(browser, _Harness(task))
    try:
        _install_delayed_posts(page)
        page.reload()
        _prepare(page, "upload")
        page.locator("#btn-submit-deliverable").evaluate("element => { element.disabled = true; }")
        page.locator("#btn-upload-asset").click()
        page.wait_for_function("window.__mutationPosts.length === 1")
        _release_delayed_posts(page)
        page.wait_for_function(
            "document.querySelector('#action-error').getAttribute('role') === 'alert'"
        )

        assert page.locator("#btn-upload-asset").is_enabled()
        assert page.locator("#btn-submit-deliverable").is_disabled()
    finally:
        page.close()


class _ApprovalHarness(_Harness):
    def write_reply(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path.endswith("/approve"):
            self.task = {
                **self.task,
                "status": "approved",
                "lifecycle_version": body["expected_lifecycle_version"] + 1,
                "worker_payment": 60,
                "poster_refund": 40,
            }
            reply = _operation("approve_task", "submitted", "approved", "bank_split")
            return 200, _with_operation_id(reply, body)
        return super().write_reply(path, body)


def test_approval_success_explains_payment_and_unused_reward_refund(browser: Browser) -> None:
    """Approval confirmation distinguishes the accepted price from the offered reward."""
    poster = {"agent_id": OPERATOR, "name": "Operator"}
    task = _task(
        "submitted",
        poster=poster,
        worker={"agent_id": "a-worker", "name": "Worker"},
        reward=100,
        accepted_price=60,
    )
    page = _open(browser, _ApprovalHarness(task))
    try:
        page.locator("#btn-approve").click()
        notice = page.locator("#action-error")
        page.wait_for_function(
            "document.querySelector('#action-error').classList.contains('action-success')"
        )
        assert notice.get_attribute("role") == "status"
        assert notice.inner_text() == (
            "Delivery approved. The accepted price is paid to the worker and the unused reward "
            "is returned to the poster."
        )
    finally:
        page.close()
