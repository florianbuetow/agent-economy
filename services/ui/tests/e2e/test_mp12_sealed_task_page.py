"""MP-12 browser coverage for poster-authorized open-task bids."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page, Route

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


def test_open_task_poster_sees_loading_before_authorized_bids(
    e2e_page: Page,
    e2e_server: str,
) -> None:
    e2e_page.add_init_script(
        """(() => {
            const originalFetch = window.fetch.bind(window);
            window.fetch = (input, options) => {
                const url = String(input);
                if (!url.includes('/api/proxy/tasks/t-task6/bids')) {
                    return originalFetch(input, options);
                }
                return new Promise((resolve) => {
                    window.__finishMp12BidFetch = () => resolve(new Response(JSON.stringify({
                        task_id: 't-task6',
                        bids: [{
                            bid_id: 'bid-browser-loading',
                            bidder: {
                                agent_id: 'a-bob',
                                name: 'Bob',
                                delivery_quality: {
                                    extremely_satisfied: 1,
                                    satisfied: 1,
                                    dissatisfied: 0
                                }
                            },
                            amount: 73,
                            submitted_at: '2026-03-02T00:15:55Z',
                            accepted: false
                        }]
                    }), {status: 200, headers: {'Content-Type': 'application/json'}}));
                });
            };
        })();"""
    )
    e2e_page.route(
        "**/api/proxy/identity",
        lambda route: route.fulfill(json={"agent_id": "a-alice"}),
    )
    e2e_page.route("**/api/events/stream?**", lambda route: route.fulfill(status=204, body=""))

    e2e_page.goto(f"{e2e_server}/task.html?task_id=t-task6")
    loading = e2e_page.locator(".bid-load-status")
    loading.wait_for()

    assert "Loading authorized bid details" in loading.inner_text()
    assert "1 bid received" in e2e_page.locator("#phase-content").inner_text()
    assert e2e_page.locator(".btn-accept-bid").count() == 0

    e2e_page.evaluate("window.__finishMp12BidFetch()")
    row = e2e_page.locator(".bid-row")
    row.wait_for()
    assert row.locator(".bid-name").inner_text() == "Bob"
    assert row.locator(".bid-amount").inner_text() == "73 ©"


def test_open_task_poster_loads_authorized_bid_amounts(
    e2e_page: Page,
    e2e_server: str,
) -> None:
    e2e_page.route(
        "**/api/proxy/identity",
        lambda route: route.fulfill(json={"agent_id": "a-alice"}),
    )
    e2e_page.route(
        "**/api/proxy/tasks/t-task6/bids",
        lambda route: route.fulfill(
            json={
                "task_id": "t-task6",
                "bids": [
                    {
                        "bid_id": "bid-browser-authorized",
                        "bidder": {
                            "agent_id": "a-bob",
                            "name": "Bob",
                            "delivery_quality": {
                                "extremely_satisfied": 1,
                                "satisfied": 1,
                                "dissatisfied": 0,
                            },
                        },
                        "amount": 73,
                        "submitted_at": "2026-03-02T00:15:55Z",
                        "accepted": False,
                    }
                ],
            }
        ),
    )

    e2e_page.goto(f"{e2e_server}/task.html?task_id=t-task6")
    row = e2e_page.locator(".bid-row")
    row.wait_for()

    assert row.locator(".bid-name").inner_text() == "Bob"
    assert "Quality: 100%" in row.locator(".bid-meta").inner_text()
    assert row.locator(".bid-amount").inner_text() == "73 ©"
    assert row.locator(".btn-accept-bid").get_attribute("data-bid-id") == ("bid-browser-authorized")


def test_open_task_nonposter_never_requests_authorized_bids(
    e2e_page: Page,
    e2e_server: str,
) -> None:
    requests: list[str] = []

    def record_unexpected_request(route: Route) -> None:
        requests.append(route.request.url)
        route.fulfill(status=403, json={"error": "forbidden"})

    e2e_page.route(
        "**/api/proxy/identity",
        lambda route: route.fulfill(json={"agent_id": "a-eve"}),
    )
    e2e_page.route("**/api/proxy/tasks/t-task6/bids", record_unexpected_request)

    e2e_page.goto(f"{e2e_server}/task.html?task_id=t-task6")
    e2e_page.locator("#phase-content .card").wait_for()

    assert requests == []
    assert e2e_page.locator(".phase-step.active").get_attribute("data-phase") == "1"
    assert "1 bid received" in e2e_page.locator("#phase-content").inner_text()
    assert e2e_page.locator(".bid-row").count() == 0
    assert "Bid details are sealed to the poster" in e2e_page.locator("#phase-content").inner_text()


@pytest.mark.parametrize("failure_mode", ["forbidden", "server", "malformed", "network"])
def test_open_task_poster_sees_explicit_bid_load_failure(
    e2e_page: Page,
    e2e_server: str,
    failure_mode: str,
) -> None:
    def fail_authorized_bid_request(route: Route) -> None:
        if failure_mode == "forbidden":
            route.fulfill(status=403, json={"error": "forbidden"})
        elif failure_mode == "server":
            route.fulfill(status=502, json={"error": "bid_listing_failed"})
        elif failure_mode == "malformed":
            route.fulfill(status=200, json={"task_id": "t-task6", "bids": "invalid"})
        else:
            route.abort("failed")

    e2e_page.route(
        "**/api/proxy/identity",
        lambda route: route.fulfill(json={"agent_id": "a-alice"}),
    )
    e2e_page.route("**/api/proxy/tasks/t-task6/bids", fail_authorized_bid_request)
    e2e_page.route("**/api/events/stream?**", lambda route: route.fulfill(status=204, body=""))

    e2e_page.goto(f"{e2e_server}/task.html?task_id=t-task6")
    e2e_page.locator("#phase-content .card").wait_for()
    e2e_page.locator(".bid-load-error").wait_for()

    assert e2e_page.locator(".phase-step.active").get_attribute("data-phase") == "1"
    assert "1 bid received" in e2e_page.locator("#phase-content").inner_text()
    assert e2e_page.locator(".bid-load-error").count() == 1
    assert "unavailable" in e2e_page.locator(".bid-load-error").inner_text().lower()
    assert e2e_page.locator(".bid-row").count() == 0
    assert e2e_page.locator(".btn-accept-bid").count() == 0
