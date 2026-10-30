"""Real-browser failing proofs for the MP-05 local posting journal."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

import pytest
from mp05_posting_browser_fixtures import (
    COMPLETION_KEY,
    OPERATION_ID,
    POSTING_KEY,
    RECORD,
    SECOND_OPERATION_ID,
    SECOND_POSTING_KEY,
    SECOND_RECORD,
    completion_marker,
    events,
    fill_form,
    goto_form,
    install_trace,
    observation,
    stored,
)
from service_auth.task_postings import TaskPostingIntent

if TYPE_CHECKING:
    from playwright.sync_api import Page, Route

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _json(route: Route, value: object, *, status: int = 200) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(value))


def _route_protocol(
    page: Page,
    *,
    submit_state: str = "awaiting_lock",
    abort_submit: bool = False,
) -> None:
    page.route("**/api/proxy/task-postings/prepare", lambda route: _json(route, RECORD))
    if abort_submit:
        page.route(f"**/api/proxy/task-postings/{OPERATION_ID}/submit", lambda route: route.abort())
    else:
        page.route(
            f"**/api/proxy/task-postings/{OPERATION_ID}/submit",
            lambda route: _json(
                route,
                observation(submit_state, kind="submit"),
                status=201 if submit_state == "published" else 202,
            ),
        )
    page.route(
        f"**/api/proxy/task-postings/{OPERATION_ID}/status",
        lambda route: _json(route, observation(submit_state, kind="status")),
    )
    page.route("**/api/proxy/tasks", lambda route: _json(route, {"legacy": True}, status=410))


def _click_post(page: Page) -> None:
    page.locator("#btn-post-task").click()
    page.wait_for_function("() => document.getElementById('btn-post-task').disabled === false")


def test_local_storage_round_trip_control(e2e_page: Page, e2e_server: str) -> None:
    e2e_page.goto(f"{e2e_server}/task.html")
    value = {"record_version": 1, "operation_id": OPERATION_ID}

    returned = e2e_page.evaluate(
        """([key, record]) => {
          const raw = JSON.stringify(record);
          localStorage.setItem(key, raw);
          return JSON.parse(localStorage.getItem(key));
        }""",
        [POSTING_KEY, value],
    )

    assert returned == value


def test_browser_security_still_blocks_unmarked_posting_write(
    e2e_page: Page, e2e_server: str
) -> None:
    e2e_page.goto(f"{e2e_server}/task.html")
    status = e2e_page.evaluate(
        """async () => (await fetch('/api/proxy/task-postings/prepare', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
        })).status"""
    )
    assert status == 403


def test_full_prepared_record_is_read_back_before_effectful_submit(
    e2e_page: Page, e2e_server: str
) -> None:
    install_trace(e2e_page)
    _route_protocol(e2e_page)
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page)

    _click_post(e2e_page)

    trace = events(e2e_page)
    prepare_index = next(
        index for index, item in enumerate(trace) if str(item.get("url", "")).endswith("/prepare")
    )
    store_index = next(index for index, item in enumerate(trace) if item.get("key") == POSTING_KEY)
    submit_index = next(
        index for index, item in enumerate(trace) if str(item.get("url", "")).endswith("/submit")
    )
    assert prepare_index < store_index < submit_index
    assert stored(e2e_page, POSTING_KEY) == RECORD
    assert json.loads(str(trace[submit_index]["body"])) == RECORD


def test_storage_failure_prevents_every_effectful_post(e2e_page: Page, e2e_server: str) -> None:
    install_trace(e2e_page, fail_prefix="ate.task-posting:v1:")
    _route_protocol(e2e_page)
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page)

    _click_post(e2e_page)

    fetched = [str(item.get("url", "")) for item in events(e2e_page) if item["kind"] == "fetch"]
    assert any(url.endswith("/prepare") for url in fetched)
    assert not any(url.endswith("/submit") or url.endswith("/api/proxy/tasks") for url in fetched)
    assert stored(e2e_page, POSTING_KEY) is None


@pytest.mark.parametrize("outcome", ["pending", "timeout"])
def test_pending_or_timeout_record_survives_reload_and_checks_status(
    e2e_page: Page, e2e_server: str, outcome: str
) -> None:
    install_trace(e2e_page)
    _route_protocol(e2e_page, abort_submit=outcome == "timeout")
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page)
    _click_post(e2e_page)

    e2e_page.reload()
    e2e_page.wait_for_function(
        "() => (window.__mp05Events || []).some(e => "
        "e.kind === 'fetch' && e.url.endsWith('/status'))"
    )

    assert stored(e2e_page, POSTING_KEY) == RECORD
    assert stored(e2e_page, COMPLETION_KEY) is None


def test_browser_context_restart_recovers_saved_record_with_status(
    e2e_page: Page, e2e_server: str
) -> None:
    install_trace(e2e_page)
    _route_protocol(e2e_page)
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page)
    _click_post(e2e_page)
    storage_state = e2e_page.context.storage_state()
    browser = e2e_page.context.browser
    assert browser is not None

    restarted_context = browser.new_context(storage_state=storage_state)
    restarted = restarted_context.new_page()
    try:
        restarted.set_default_timeout(1_500)
        install_trace(restarted)
        _route_protocol(restarted)
        restarted.goto(f"{e2e_server}/task.html")
        restarted.wait_for_function(
            "() => (window.__mp05Events || []).some(e => "
            "e.kind === 'fetch' && e.url.endsWith('/status'))"
        )
        assert stored(restarted, POSTING_KEY) == RECORD
    finally:
        restarted_context.close()


def test_completion_marker_write_failure_retains_intent_and_status_recovery(
    e2e_page: Page, e2e_server: str
) -> None:
    install_trace(e2e_page, fail_prefix="ate.task-posting-completion:v1:")
    _route_protocol(e2e_page, submit_state="published")
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page)
    _click_post(e2e_page)

    assert stored(e2e_page, POSTING_KEY) == RECORD
    assert stored(e2e_page, COMPLETION_KEY) is None
    assert e2e_page.get_by_text("Check status", exact=False).count() >= 1


def test_stale_tab_202_cannot_overwrite_published_completion(
    e2e_page: Page, e2e_server: str
) -> None:
    install_trace(e2e_page)
    pending = json.dumps(observation("awaiting_lock", kind="submit"))
    e2e_page.add_init_script(
        """(() => {
          const originalFetch = window.fetch;
          const pendingBody = __PENDING__;
          window.fetch = function(input, init) {
            const url = typeof input === 'string' ? input : input.url;
            if (url.endsWith('/submit')) {
              return new Promise(resolve => setTimeout(() => {
                window.__mp05StaleSubmitReleased = true;
                resolve(new Response(JSON.stringify(pendingBody), {
                  status: 202, headers: {'Content-Type': 'application/json'}
                }));
              }, 100));
            }
            return originalFetch.call(this, input, init);
          };
        })();""".replace("__PENDING__", pending),
    )
    _route_protocol(e2e_page, submit_state="awaiting_lock")
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page)
    e2e_page.locator("#btn-post-task").click()
    e2e_page.wait_for_function(
        "key => localStorage.getItem(key) !== null",
        arg=POSTING_KEY,
    )
    marker = completion_marker()
    e2e_page.evaluate(
        "([key, value]) => localStorage.setItem(key, JSON.stringify(value))",
        [COMPLETION_KEY, marker],
    )

    e2e_page.wait_for_function("() => window.__mp05StaleSubmitReleased === true")

    assert stored(e2e_page, COMPLETION_KEY) == marker
    assert e2e_page.get_by_text("completed", exact=False).count() >= 1


def test_form_edits_do_not_change_exact_retry_and_separate_action_allocates_new_intent(
    e2e_page: Page, e2e_server: str
) -> None:
    install_trace(e2e_page)
    prepare_count = 0

    def prepare(route: Route) -> None:
        nonlocal prepare_count
        prepare_count += 1
        value = copy.deepcopy(RECORD if prepare_count == 1 else SECOND_RECORD)
        _json(route, value)

    e2e_page.route("**/api/proxy/task-postings/prepare", prepare)
    e2e_page.route(
        f"**/api/proxy/task-postings/{OPERATION_ID}/submit",
        lambda route: _json(route, observation("awaiting_lock", kind="submit"), status=202),
    )
    e2e_page.route(
        f"**/api/proxy/task-postings/{SECOND_OPERATION_ID}/submit",
        lambda route: _json(
            route,
            {
                "observation": "submit",
                "status_code": 202,
                "publication": {
                    **observation("awaiting_lock", kind="submit")["publication"],
                    "operation_id": SECOND_OPERATION_ID,
                    "task_id": SECOND_RECORD["intent"]["task_id"],
                    "request_sha256": SECOND_RECORD["request_sha256"],
                    "lock_operation_id": SECOND_RECORD["intent"]["lock_operation_id"],
                },
            },
            status=202,
        ),
    )
    e2e_page.route("**/api/proxy/tasks", lambda route: _json(route, {}, status=410))
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page)
    _click_post(e2e_page)
    e2e_page.locator("#f-reward").fill("999")

    assert e2e_page.get_by_text("Try posting again", exact=False).count() == 1
    assert e2e_page.get_by_text("Post as a new task", exact=False).count() == 1
    e2e_page.get_by_text("Try posting again", exact=False).click()
    e2e_page.get_by_text("Post as a new task", exact=False).click()
    e2e_page.wait_for_function("() => document.getElementById('btn-post-task').disabled === false")

    trace = events(e2e_page)
    submit_bodies = [
        json.loads(str(item["body"]))
        for item in trace
        if str(item.get("url", "")).endswith("/submit")
    ]
    assert submit_bodies == [RECORD, RECORD, SECOND_RECORD]
    assert prepare_count == 2
    assert stored(e2e_page, SECOND_POSTING_KEY) == SECOND_RECORD


def test_prepared_terms_must_match_the_form_before_persistence_or_submit(
    e2e_page: Page, e2e_server: str
) -> None:
    install_trace(e2e_page)
    _route_protocol(e2e_page)
    e2e_page.route("**/api/proxy/task-postings/prepare", lambda route: _json(route, SECOND_RECORD))
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page)

    _click_post(e2e_page)

    assert stored(e2e_page, SECOND_POSTING_KEY) is None
    assert not any(str(item.get("url", "")).endswith("/submit") for item in events(e2e_page))
    assert e2e_page.locator("#post-error").text_content()


@pytest.mark.parametrize("outcome", ["pending", "timeout"])
def test_repeated_form_submit_does_not_allocate_another_uncertain_posting(
    e2e_page: Page, e2e_server: str, outcome: str
) -> None:
    install_trace(e2e_page)
    prepared = 0
    second = copy.deepcopy(SECOND_RECORD)
    second["intent"]["reward"] = RECORD["intent"]["reward"]
    second["request_sha256"] = TaskPostingIntent.from_json(second["intent"]).request_sha256

    def prepare(route: Route) -> None:
        nonlocal prepared
        prepared += 1
        _json(route, RECORD if prepared == 1 else second)

    def submit(route: Route) -> None:
        if outcome == "timeout":
            route.abort()
            return
        record = route.request.post_data_json
        value = observation("awaiting_lock", kind="submit")
        value["publication"].update(
            operation_id=record["intent"]["operation_id"],
            task_id=record["intent"]["task_id"],
            lock_operation_id=record["intent"]["lock_operation_id"],
            request_sha256=record["request_sha256"],
        )
        _json(route, value, status=202)

    e2e_page.route("**/api/proxy/task-postings/prepare", prepare)
    e2e_page.route("**/api/proxy/task-postings/*/submit", submit)
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page)
    _click_post(e2e_page)
    if e2e_page.locator("#btn-post-task").is_enabled():
        _click_post(e2e_page)

    assert prepared == 1
    assert stored(e2e_page, POSTING_KEY) == RECORD
    assert stored(e2e_page, SECOND_POSTING_KEY) is None
    sent = [
        json.loads(str(item["body"]))
        for item in events(e2e_page)
        if str(item.get("url", "")).endswith("/submit")
    ]
    assert sent and all(record == RECORD for record in sent)


@pytest.mark.parametrize(
    ("selector", "value"),
    [
        ("#f-reward", "1.5"),
        ("#f-reward", "1e2"),
        ("#f-reward", "12coins"),
        ("#f-reward", "9007199254740992"),
        ("#f-bid-dl", "9223372036855"),
        ("#f-accept-window", "-1"),
        ("#f-exec-dl", "0"),
    ],
)
def test_numeric_input_requires_decimal_safe_positive_integer_bounds(
    e2e_page: Page, e2e_server: str, selector: str, value: str
) -> None:
    install_trace(e2e_page)
    _route_protocol(e2e_page)
    goto_form(e2e_page, e2e_server)
    fill_form(e2e_page, {selector: value})

    e2e_page.locator("#btn-post-task").click()

    fetched = [
        str(item.get("url", ""))
        for item in events(e2e_page)
        if item.get("kind") == "fetch"
        and (
            "/api/proxy/task-postings/" in str(item.get("url", ""))
            or str(item.get("url", "")).endswith("/api/proxy/tasks")
        )
    ]
    assert fetched == []
    assert e2e_page.locator("#post-error").text_content()
