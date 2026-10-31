"""MP-02 browser proofs for stored content on every consuming UI view."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Page


pytestmark = [pytest.mark.e2e, pytest.mark.integration]

_PAYLOAD = (
    '<img data-mp02-xss="stored" src="/mp02-missing-image" '
    'onerror="window.__mp02Xss=(window.__mp02Xss||0)+1">'
)


def _seed_hostile_stored_strings(db_path: Path) -> None:
    """Put harmless active-markup markers in every UI-controlled string class."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE identity_agents SET name = ?", (_PAYLOAD,))
        conn.execute("UPDATE board_tasks SET title = ?", (_PAYLOAD,))
        conn.execute("UPDATE reputation_feedback SET comment = ?", (_PAYLOAD,))
        conn.execute("UPDATE events SET summary = ?", (_PAYLOAD,))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "path, ready_selector",
    [
        ("/", ".lb-name"),
        ("/task.html?task_id=t-task1", ".feedback-row"),
        ("/agent.html?agent_id=a-alice", ".profile-name"),
        ("/quarterly-report.html?quarter=2026-Q1", ".notable-row"),
        ("/observatory.html", ".feed-item"),
    ],
)
def test_stored_strings_are_inert_on_every_browser_view(
    e2e_page: Page,
    e2e_server: str,
    e2e_db_path: Path,
    path: str,
    ready_selector: str,
) -> None:
    """Names, titles, comments and summaries render only as literal text."""
    _seed_hostile_stored_strings(e2e_db_path)
    e2e_page.add_init_script("window.__mp02Xss = 0")

    e2e_page.goto(f"{e2e_server}{path}")
    e2e_page.wait_for_selector(ready_selector, timeout=10_000)
    e2e_page.wait_for_timeout(250)

    assert e2e_page.locator('[data-mp02-xss="stored"]').count() == 0
    assert e2e_page.evaluate("window.__mp02Xss") == 0
    assert _PAYLOAD in e2e_page.locator("body").inner_text()


def test_new_sse_summary_is_literal_after_history_load(e2e_page, e2e_server, e2e_db_path):
    e2e_page.add_init_script("window.__mp02Xss = 0")
    with e2e_page.expect_response(lambda response: "/api/events/stream?" in response.url) as stream:
        e2e_page.goto(f"{e2e_server}/observatory.html")
    assert stream.value.status == 200
    e2e_page.locator(".feed-item").first.wait_for()
    summary = "MP02 live " + _PAYLOAD
    with sqlite3.connect(str(e2e_db_path)) as connection:
        connection.execute(
            "INSERT INTO events (event_source, event_type, timestamp, summary, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            ("board", "task.created", datetime.now(UTC).isoformat(), summary, "{}"),
        )
    live_item = e2e_page.locator(".feed-item").filter(has_text="MP02 live ")
    live_item.wait_for()
    assert live_item.locator('[data-mp02-xss="stored"]').count() == 0
    assert summary in live_item.inner_text()
    assert e2e_page.evaluate("window.__mp02Xss") == 0


def test_task_identifier_is_literal_text(e2e_page, e2e_server, e2e_db_path):
    task_id = "t-<br>"
    with sqlite3.connect(str(e2e_db_path)) as connection:
        connection.execute(
            "UPDATE board_tasks SET task_id = ? WHERE task_id = 't-task6'", (task_id,)
        )
        connection.execute(
            "UPDATE board_bids SET task_id = ? WHERE task_id = 't-task6'", (task_id,)
        )
    e2e_page.goto(f"{e2e_server}/task.html?task_id={quote(task_id, safe='')}")
    label = e2e_page.locator("#phase-content .card-label").first
    label.wait_for()
    assert task_id in label.text_content()
    assert label.locator("br").count() == 0


def test_bid_identifier_stays_in_one_data_attribute(e2e_page, e2e_server, e2e_db_path):
    bid_id = 'bid-8" data-mp02-id="marker'
    with sqlite3.connect(str(e2e_db_path)) as connection:
        connection.execute("UPDATE board_bids SET bid_id = ? WHERE bid_id = 'bid-8'", (bid_id,))
    e2e_page.route(
        "**/api/proxy/identity", lambda route: route.fulfill(json={"agent_id": "a-alice"})
    )
    e2e_page.goto(f"{e2e_server}/task.html?task_id=t-task6")
    button = e2e_page.locator(".btn-accept-bid").first
    button.wait_for()
    assert button.get_attribute("data-bid-id") == bid_id
    assert e2e_page.locator("[data-mp02-id]").count() == 0


def test_task_identifier_is_one_encoded_request_segment(e2e_page, e2e_server, e2e_db_path):
    task_id = "t-task6?note=1"
    with sqlite3.connect(str(e2e_db_path)) as connection:
        connection.execute(
            "UPDATE board_tasks SET task_id = ? WHERE task_id = 't-task6'", (task_id,)
        )
        connection.execute(
            "UPDATE board_bids SET task_id = ? WHERE task_id = 't-task6'", (task_id,)
        )
    with e2e_page.expect_response(
        lambda response: response.url.startswith(f"{e2e_server}/api/tasks/")
    ) as pending:
        e2e_page.goto(f"{e2e_server}/task.html?task_id={quote(task_id, safe='')}")
    assert pending.value.url == f"{e2e_server}/api/tasks/{quote(task_id, safe='')}"
    assert pending.value.status == 200


def test_quarterly_no_data_label_is_literal(e2e_page, e2e_server):
    label = '<em data-mp02-quarter="text">missing</em>'
    e2e_page.route(
        "**/api/quarterly-report?**",
        lambda route: route.fulfill(status=404, json={"details": {"quarter": label}}),
    )
    e2e_page.goto(f"{e2e_server}/quarterly-report.html?quarter=2026-Q1")
    body = e2e_page.locator("#qr-content")
    body.locator(".notable-empty").wait_for()
    assert label in body.inner_text()
    assert e2e_page.locator("[data-mp02-quarter]").count() == 0


@pytest.mark.parametrize(
    "target,method",
    [
        ("https://example.invalid/api/proxy/tasks", "POST"),
        ("/outside-proxy", "POST"),
        ("/api/proxy/tasks", "GET"),
    ],
)
def test_proxy_helper_rejects_targets_before_fetch(e2e_page, e2e_server, target, method):
    e2e_page.goto(e2e_server)
    result = e2e_page.evaluate(
        """async ({target, method}) => {
        const original = window.fetch;
        const calls = [];
        window.fetch = async (url) => { calls.push(String(url)); return {
            ok: true, json: async () => ({
                csrf_header_name:'X-ATE-CSRF', csrf_header_value:'local-operator'
            })
        }; };
        let rejected = false;
        try { await window.ATE.proxyFetch(target, {method}); } catch (_) { rejected = true; }
        finally { window.fetch = original; }
        return {rejected, calls};
    }""",
        {"target": target, "method": method},
    )
    assert result == {"rejected": True, "calls": []}


def test_proxy_helper_preserves_same_origin_write(e2e_page, e2e_server):
    e2e_page.goto(e2e_server)
    result = e2e_page.evaluate("""async () => {
        const original = window.fetch;
        const calls = [];
        window.fetch = async (url, options) => { calls.push({
            url: new URL(url, location.origin).href,
            method: options && options.method,
            body: options && options.body,
            headers: Object.fromEntries(new Headers(options && options.headers))
        }); return {ok: true, json: async () => ({
            csrf_header_name:'X-ATE-CSRF', csrf_header_value:'local-operator'
        })}; };
        try { await window.ATE.proxyFetch('/api/proxy/tasks', {
            method:'POST', headers:{'Content-Type':'application/json'}, body:'{"title":"Example"}'
        }); } finally { window.fetch = original; }
        return calls;
    }""")
    assert len(result) == 2
    assert result[0]["url"] == f"{e2e_server}/api/proxy/browser-config"
    assert result[1] == {
        "url": f"{e2e_server}/api/proxy/tasks",
        "method": "POST",
        "body": '{"title":"Example"}',
        "headers": {"content-type": "application/json", "x-ate-csrf": "local-operator"},
    }
