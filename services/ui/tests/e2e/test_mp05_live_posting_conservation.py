"""Real-stack browser proof for MP-05 duplicate and lost-response conservation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import BrowserContext, Route

from mp05_live_posting_conservation import (
    assert_completion_marker,
    assert_conserved_publication,
    operator_balance,
    read_single_posting,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.isolated_test_runtime import IsolatedRuntime  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

_REWARD = 137
_TERMS = {
    "title": "MP-05 live browser conservation proof",
    "spec": (
        "One browser-owned intent remains one task and escrow under lost and duplicate responses."
    ),
    "reward": _REWARD,
    "bidding_deadline_seconds": 3_600,
    "acceptance_window_seconds": 3_600,
    "execution_deadline_seconds": 7_200,
    "review_deadline_seconds": 3_600,
}


@pytest.fixture
def live_runtime(tmp_path: Path) -> Generator[IsolatedRuntime, None, None]:
    """Start and provision a disposable seven-service runtime owned by this test."""
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path / "mp05-live-browser-runtime",
    )
    try:
        runtime.start()
        assert runtime.provision() == 0
        yield runtime
    finally:
        runtime.stop()


def _fill_form(page: Any) -> None:
    values = {
        "#f-title": _TERMS["title"],
        "#f-spec": _TERMS["spec"],
        "#f-reward": str(_TERMS["reward"]),
        "#f-bid-dl": str(_TERMS["bidding_deadline_seconds"]),
        "#f-accept-window": str(_TERMS["acceptance_window_seconds"]),
        "#f-exec-dl": str(_TERMS["execution_deadline_seconds"]),
        "#f-rev-dl": str(_TERMS["review_deadline_seconds"]),
    }
    for selector, value in values.items():
        page.locator(selector).fill(value)


def _recover_in_two_tabs(
    *,
    first: Any,
    context: Any,
    ui_url: str,
    operation_id: str,
    record: dict[str, Any],
) -> Any:
    status_pattern = f"**/api/proxy/task-postings/{operation_id}/status"
    first.route(status_pattern, lambda route: route.abort("failed"))
    first.reload()
    first.get_by_role("button", name="Retry exact intent").wait_for(state="visible")
    recovered_operation_id, recovered_record = read_single_posting(first)
    assert recovered_operation_id == operation_id
    assert recovered_record == record

    second = context.new_page()
    second.route(status_pattern, lambda route: route.abort("failed"))
    second.goto(f"{ui_url}/task.html")
    second.get_by_role("button", name="Retry exact intent").wait_for(state="visible")
    second_operation_id, second_record = read_single_posting(second)
    assert second_operation_id == operation_id
    assert second_record == record
    return second


def _retry_from_both_tabs(first: Any, second: Any, operation_id: str) -> None:
    submit_pattern = f"**/api/proxy/task-postings/{operation_id}/submit"
    status_pattern = f"**/api/proxy/task-postings/{operation_id}/status"
    lost_retry_responses: list[str] = []

    def lose_committed_retry_response(route: Route) -> None:
        response = route.fetch()
        assert response.status == 201
        assert response.json()["publication"]["publication_state"] == "published"
        route.abort("failed")
        lost_retry_responses.append(response.url)

    def wait_for_lost_retries(page: Any, expected: int) -> None:
        for _attempt in range(200):
            if len(lost_retry_responses) == expected:
                return
            page.wait_for_timeout(50)
        assert len(lost_retry_responses) == expected

    first.route(submit_pattern, lose_committed_retry_response)
    second.route(submit_pattern, lose_committed_retry_response)
    first.get_by_role("button", name="Retry exact intent").click()
    wait_for_lost_retries(first, 1)
    second.get_by_role("button", name="Retry exact intent").click()
    wait_for_lost_retries(second, 2)

    for page in (first, second):
        page.unroute(submit_pattern, lose_committed_retry_response)
        page.unroute(status_pattern)
    first.get_by_role("button", name="Check status").click()
    first.get_by_text("Posting completed.").wait_for(state="visible")
    second.get_by_role("button", name="Check status").click()
    second.get_by_text("Posting completed.").wait_for(state="visible")


def test_lost_committed_response_and_two_tab_retry_conserve_one_publication(
    e2e_context: BrowserContext,
    live_runtime: IsolatedRuntime,
) -> None:
    """The real browser/proxy/Board/Bank path conserves one logical posting."""
    ui_url = live_runtime.service_url("ui")
    database_path = live_runtime.run_dir / "data" / "economy.db"
    context = e2e_context
    submit_requests: list[tuple[str, str | None]] = []
    committed: dict[str, object] = {}

    def capture_request(request: Any) -> None:
        if request.url.endswith("/submit") and "/api/proxy/task-postings/" in request.url:
            submit_requests.append((request.url, request.post_data))

    context.on("request", capture_request)
    first = context.new_page()
    first.goto(f"{ui_url}/task.html")
    first.locator("#btn-post-task").wait_for(state="visible")

    identity = first.request.get(f"{ui_url}/api/proxy/identity")
    assert identity.status == 200
    operator_id = identity.json()["agent_id"]
    initial_balance = operator_balance(database_path, operator_id)

    submit_pattern = "**/api/proxy/task-postings/*/submit"

    def lose_first_committed_response(route: Route) -> None:
        response = route.fetch()
        assert response.status == 201
        committed.update(response.json())
        route.abort("failed")

    first.route(submit_pattern, lose_first_committed_response)
    _fill_form(first)
    first.locator("#btn-post-task").click()
    first.get_by_role("button", name="Retry exact intent").wait_for(state="visible")
    operation_id, record = read_single_posting(first)
    assert committed["publication"]["operation_id"] == operation_id  # type: ignore[index]
    assert committed["publication"]["publication_state"] == "published"  # type: ignore[index]
    assert_conserved_publication(database_path, record, initial_balance)

    first.unroute(submit_pattern, lose_first_committed_response)
    second = _recover_in_two_tabs(
        first=first,
        context=context,
        ui_url=ui_url,
        operation_id=operation_id,
        record=record,
    )
    _retry_from_both_tabs(first, second, operation_id)

    assert len(submit_requests) == 3
    expected_url = f"{ui_url}/api/proxy/task-postings/{operation_id}/submit"
    assert [url for url, _body in submit_requests] == [expected_url, expected_url, expected_url]
    bodies = [json.loads(body) for _url, body in submit_requests if body is not None]
    assert bodies == [record, record, record]
    assert_completion_marker(first, record)
    assert_completion_marker(second, record)
    assert_conserved_publication(database_path, record, initial_balance)
