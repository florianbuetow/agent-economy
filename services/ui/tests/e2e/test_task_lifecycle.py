"""Task lifecycle page E2E tests (tickets T01-T98)."""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .fixtures.seed_db import extend_seed_data
from .helpers.db_helpers import (
    add_bid,
    add_feedback,
    advance_task_status,
    get_writable_db_connection,
    insert_event,
    wait_for_sse_update,
)
from .pages.task import TaskPage

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Page


def _ensure_extended_seed_data(db_path: Path) -> None:
    """Ensure extended E2E seed data has been applied to the fixture DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM identity_agents").fetchone()[0]
        if count < 10:
            extend_seed_data(conn)
    finally:
        conn.close()


@pytest.fixture(scope="module", autouse=True)
def _ensure_task_seed_data(e2e_db_path: Path) -> None:
    """Autouse fixture to guarantee task tests run on extended fixture data."""
    _ensure_extended_seed_data(e2e_db_path)


def _request_json(e2e_page: Page, e2e_server: str, path: str) -> dict[str, Any]:
    """GET JSON payload from the running E2E server."""
    resp = e2e_page.request.get(f"{e2e_server}{path}")
    assert resp.status == 200
    return resp.json()


def _now_iso() -> str:
    """Return UTC timestamp in project ISO format."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_create_mode(task_page: TaskPage, e2e_page: Page) -> None:
    """Open task page in create mode and wait for form controls."""
    task_page.navigate()
    e2e_page.wait_for_function(
        "document.querySelector('#f-title') && document.querySelector('#btn-post-task')",
    )


def _open_task_view(task_page: TaskPage, e2e_page: Page, task_id: str) -> None:
    """Open task page for a task_id and wait for panel to render."""
    task_page.navigate(task_id=task_id)
    e2e_page.wait_for_function(
        "document.querySelector('#phase-content') && "
        "document.querySelector('#phase-content').textContent.trim().length > 0",
    )


def _active_phase(e2e_page: Page) -> int:
    """Return active phase from the phase strip data attribute."""
    active = e2e_page.locator(".phase-step.active")
    if active.count() == 0:
        return -1
    phase_attr = active.first.get_attribute("data-phase") or "0"
    return int(phase_attr)


def _phase_labels(task_page: TaskPage) -> list[str]:
    """Extract normalized phase labels from the strip."""
    labels = []
    for item in task_page.get_phase_strip():
        text = item["label"]
        cleaned = " ".join(text.split())
        labels.append(cleaned)
    return labels


def _parse_int(text: str) -> int:
    """Extract first integer from a text string."""
    match = re.search(r"-?\d+", text.replace(",", ""))
    assert match is not None, f"Expected integer in text: {text!r}"
    return int(match.group(0))


def _task_events(e2e_page: Page, e2e_server: str, task_id: str) -> list[dict[str, Any]]:
    """Return recent events for a task from API."""
    payload = _request_json(e2e_page, e2e_server, "/api/events?limit=50")
    return [event for event in payload["events"] if event["task_id"] == task_id]


def _read_task_feed(task_page: TaskPage) -> list[dict[str, str]]:
    """Read right-side event feed entries."""
    return task_page.get_event_feed()


def _check_t47(task_page: TaskPage, _content: str, _ruling: dict[str, Any]) -> None:
    assert (task_page.panel_title.text_content() or "").strip() == "Ruling"


def _check_t48(task_page: TaskPage, _content: str, _ruling: dict[str, Any]) -> None:
    assert task_page.ruling_card.is_visible()


def _check_t49(_task_page: TaskPage, content: str, ruling: dict[str, Any]) -> None:
    assert f"{ruling['worker_pct']}%" in content


def _check_t50(_task_page: TaskPage, content: str, ruling: dict[str, Any]) -> None:
    assert f"{100 - ruling['worker_pct']}%" in content


def _check_t51(_task_page: TaskPage, content: str, ruling: dict[str, Any]) -> None:
    assert ruling["summary"] in content


def _check_t55(_task_page: TaskPage, content: str, _ruling: dict[str, Any]) -> None:
    assert "Court Ruling" in content


def _check_t56(task_page: TaskPage, _content: str, _ruling: dict[str, Any]) -> None:
    assert (task_page.task_status.text_content() or "").strip() == "RULED"


_T47_RULED_CHECKS = {
    "T47": _check_t47,
    "T48": _check_t48,
    "T49": _check_t49,
    "T50": _check_t50,
    "T51": _check_t51,
    "T55": _check_t55,
    "T56": _check_t56,
}


def _check_t52(before_phase: int, _content: str, _dispute: dict[str, Any], e2e_page: Page) -> None:
    assert before_phase == 4
    assert _active_phase(e2e_page) == 4


def _check_t53(_before_phase: int, content: str, dispute: dict[str, Any], _e2e_page: Page) -> None:
    assert dispute["reason"] in content


def _check_t54(_before_phase: int, content: str, dispute: dict[str, Any], _e2e_page: Page) -> None:
    rebuttal = dispute["rebuttal"]
    assert rebuttal is not None
    assert rebuttal["content"] in content


_T47_DISPUTED_CHECKS = {
    "T52": _check_t52,
    "T53": _check_t53,
    "T54": _check_t54,
}


def _task_id_for_t69_ticket(ticket: str) -> str:
    if ticket in {"T69", "T70", "T72", "T76", "T77", "T78"}:
        return "t-task1"
    if ticket == "T71":
        return "t-task6"
    return "t-task2"


def _check_t69(task_page: TaskPage, _e2e_page: Page) -> None:
    task_page.click_phase(0)
    assert (task_page.panel_title.text_content() or "").strip() == "Post"


def _check_t70(task_page: TaskPage, e2e_page: Page) -> None:
    before = _active_phase(e2e_page)
    task_page.click_phase(before)
    assert _active_phase(e2e_page) == before


def _check_t71(task_page: TaskPage, e2e_page: Page) -> None:
    before = _active_phase(e2e_page)
    task_page.click_phase(3)
    e2e_page.wait_for_timeout(120)
    assert _active_phase(e2e_page) == before == 1


def _check_t72(task_page: TaskPage, _e2e_page: Page) -> None:
    labels = _phase_labels(task_page)
    assert all(any(word in label for label in labels) for word in ["Post", "Bid", "Settle"])


def _check_t73(_task_page: TaskPage, e2e_page: Page) -> None:
    assert e2e_page.locator("#btn-next, .btn-next").count() == 0
    assert e2e_page.get_by_text("Next").count() == 0


def _check_t74(_task_page: TaskPage, e2e_page: Page) -> None:
    assert e2e_page.locator("#btn-prev, .btn-prev").count() == 0
    assert e2e_page.get_by_text("Prev").count() == 0


def _check_t75(_task_page: TaskPage, e2e_page: Page) -> None:
    assert e2e_page.locator("#btn-auto, .btn-auto").count() == 0
    assert e2e_page.get_by_text("Auto").count() == 0


def _check_t76(task_page: TaskPage, _e2e_page: Page) -> None:
    task_page.click_phase(2)
    assert (task_page.panel_title.text_content() or "").strip() == "Contract"


def _check_t77(task_page: TaskPage, e2e_page: Page) -> None:
    task_page.click_phase(6)
    assert _active_phase(e2e_page) == 6
    assert (task_page.panel_title.text_content() or "").strip() == "Settle"


def _check_t78(task_page: TaskPage, _e2e_page: Page) -> None:
    expected_titles = {
        0: "Post",
        1: "Bid",
        4: "Review",
        5: "Ruling",
        6: "Settle",
    }
    for phase in [4, 1, 5, 0, 6]:
        task_page.click_phase(phase)
        assert (task_page.panel_title.text_content() or "").strip() == expected_titles[phase]


_T69_T78_CHECKS = {
    "T69": _check_t69,
    "T70": _check_t70,
    "T71": _check_t71,
    "T72": _check_t72,
    "T73": _check_t73,
    "T74": _check_t74,
    "T75": _check_t75,
    "T76": _check_t76,
    "T77": _check_t77,
    "T78": _check_t78,
}


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T01", id="T01"),
        pytest.param("T02", id="T02"),
        pytest.param("T03", id="T03"),
        pytest.param("T04", id="T04"),
        pytest.param("T05", id="T05"),
        pytest.param("T06", id="T06"),
    ],
)
def test_t01_t06_task_selection_routing(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Task selection and routing behavior."""
    task_page = TaskPage(e2e_page, e2e_server)

    if ticket == "T01":
        _open_create_mode(task_page, e2e_page)
        assert "/task.html" in e2e_page.url
        assert "task_id=" not in e2e_page.url
    elif ticket == "T02":
        _open_task_view(task_page, e2e_page, "t-task1")
        assert "task_id=t-task1" in e2e_page.url
        assert (task_page.task_status.text_content() or "").strip() == "APPROVED"
    elif ticket == "T03":
        task_page.navigate(task_id="t-does-not-exist")
        e2e_page.wait_for_load_state("domcontentloaded")
        assert "task_id=t-does-not-exist" in e2e_page.url
        # Invalid task id does not render a successful task drilldown panel.
        assert "Loading task..." not in task_page.get_lifecycle_panel_content()
    elif ticket == "T04":
        _open_task_view(task_page, e2e_page, "t-task2")
        status_accepted = (task_page.task_status.text_content() or "").strip()
        _open_task_view(task_page, e2e_page, "t-task8")
        status_submitted = (task_page.task_status.text_content() or "").strip()
        assert status_accepted == "ACTIVE"
        assert status_submitted == "SUBMITTED"
    elif ticket == "T05":
        _open_task_view(task_page, e2e_page, "t-task5")
        assert e2e_page.url.endswith("/task.html?task_id=t-task5")
    elif ticket == "T06":
        _open_task_view(task_page, e2e_page, "t-task1")
        nav_text = " ".join(e2e_page.locator(".topnav-links").all_text_contents())
        assert "Home" in nav_text
        assert "Observatory" in nav_text
        assert "Task Lifecycle" in nav_text
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T07", id="T07"),
        pytest.param("T08", id="T08"),
        pytest.param("T09", id="T09"),
        pytest.param("T10", id="T10"),
        pytest.param("T11", id="T11"),
        pytest.param("T12", id="T12"),
    ],
)
def test_t07_t12_phase_strip(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Phase strip rendering and transitions."""
    task_page = TaskPage(e2e_page, e2e_server)

    if ticket == "T07":
        _open_task_view(task_page, e2e_page, "t-task1")
        labels = _phase_labels(task_page)
        assert len(labels) == 7
        expected = ["Post", "Bid", "Contract", "Deliver", "Review", "Ruling", "Settle"]
        for index, phase_name in enumerate(expected):
            assert phase_name in labels[index]
    elif ticket == "T08":
        _open_task_view(task_page, e2e_page, "t-task1")
        assert _active_phase(e2e_page) == 6
    elif ticket == "T09":
        _open_task_view(task_page, e2e_page, "t-task1")
        completed = e2e_page.locator(".phase-step.completed").count()
        assert completed == 6
    elif ticket == "T10":
        _open_task_view(task_page, e2e_page, "t-task1")
        task_page.click_phase(2)
        assert (task_page.panel_title.text_content() or "").strip() == "Contract"
    elif ticket == "T11":
        _open_task_view(task_page, e2e_page, "t-task2")
        before = _active_phase(e2e_page)
        task_page.click_phase(6)
        e2e_page.wait_for_timeout(150)
        assert _active_phase(e2e_page) == before == 2
    elif ticket == "T12":
        _open_task_view(task_page, e2e_page, "t-task6")
        assert _active_phase(e2e_page) == 1
        assert (task_page.panel_title.text_content() or "").strip() == "Bid"
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T13", id="T13"),
        pytest.param("T14", id="T14"),
        pytest.param("T15", id="T15"),
        pytest.param("T16", id="T16"),
        pytest.param("T17", id="T17"),
        pytest.param("T18", id="T18"),
    ],
)
def test_t13_t18_post_phase(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Post phase and create-form behavior."""
    task_page = TaskPage(e2e_page, e2e_server)

    if ticket == "T13":
        _open_task_view(task_page, e2e_page, "t-task12")
        assert _active_phase(e2e_page) == 0
        assert (task_page.panel_title.text_content() or "").strip() == "Post"
    elif ticket == "T14":
        _open_task_view(task_page, e2e_page, "t-task12")
        assert task_page.escrow_bar.is_visible()
        assert "LOCKED" in (task_page.escrow_status.text_content() or "")
    elif ticket == "T15":
        _open_task_view(task_page, e2e_page, "t-task12")
        content = task_page.get_lifecycle_panel_content()
        api_task = _request_json(e2e_page, e2e_server, "/api/tasks/t-task12")
        assert api_task["title"] in content
        assert str(api_task["reward"]) in content
        assert api_task["spec"] in content
    elif ticket == "T16":
        _open_task_view(task_page, e2e_page, "t-task12")
        assert "WAITING FOR BIDS" in task_page.get_lifecycle_panel_content()
    elif ticket == "T17":
        _open_create_mode(task_page, e2e_page)
        task_page.submit_task()
        err = (task_page.post_error.text_content() or "").strip()
        assert err == "All fields are required."
    elif ticket == "T18":
        _open_create_mode(task_page, e2e_page)
        assert e2e_page.locator("#f-title").is_visible()
        assert e2e_page.locator("#f-spec").is_visible()
        assert e2e_page.locator("#f-reward").is_visible()
        assert e2e_page.locator("#f-bid-dl").is_visible()
        assert e2e_page.locator("#f-exec-dl").is_visible()
        assert e2e_page.locator("#f-rev-dl").is_visible()
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T19", id="T19"),
        pytest.param("T20", id="T20"),
        pytest.param("T21", id="T21"),
        pytest.param("T22", id="T22"),
        pytest.param("T23", id="T23"),
        pytest.param("T24", id="T24"),
        pytest.param("T25", id="T25"),
        pytest.param("T26", id="T26"),
        pytest.param("T27", id="T27"),
    ],
)
def test_t19_t27_bid_phase(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Bid phase behavior on open tasks with bids."""
    task_page = TaskPage(e2e_page, e2e_server)
    _open_task_view(task_page, e2e_page, "t-task6")
    api_task = _request_json(e2e_page, e2e_server, "/api/tasks/t-task6")

    if ticket == "T19":
        assert _active_phase(e2e_page) == 1
        assert (task_page.panel_title.text_content() or "").strip() == "Bid"
    elif ticket == "T20":
        assert (task_page.task_status.text_content() or "").strip() == "OPEN"
    elif ticket == "T21":
        bids_ui = task_page.get_bids()
        assert len(bids_ui) == len(api_task["bids"])
        assert len(bids_ui) > 0
    elif ticket == "T22":
        bids_ui = task_page.get_bids()
        bidder_names = [bid["bidder"]["name"] for bid in api_task["bids"]]
        assert any(any(name in row["bidder"] for name in bidder_names) for row in bids_ui)
    elif ticket == "T23":
        bid_amount_texts = [row["amount"] for row in task_page.get_bids()]
        assert bid_amount_texts
        assert all(f"{api_task['reward']}" in amount for amount in bid_amount_texts)
    elif ticket == "T24":
        # In E2E infra, my identity is usually unavailable, so accept actions should not render.
        assert e2e_page.locator(".btn-accept-bid").count() == 0
    elif ticket == "T25":
        assert "Quality:" in task_page.get_lifecycle_panel_content()
    elif ticket == "T26":
        assert "LOCKED" in (task_page.escrow_status.text_content() or "")
    elif ticket == "T27":
        task_page.click_phase(0)
        assert (task_page.panel_title.text_content() or "").strip() == "Post"
        assert "WAITING FOR BIDS" in task_page.get_lifecycle_panel_content()
        task_page.click_phase(1)
        assert (task_page.panel_title.text_content() or "").strip() == "Bid"
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T28", id="T28"),
        pytest.param("T29", id="T29"),
        pytest.param("T30", id="T30"),
        pytest.param("T31", id="T31"),
        pytest.param("T32", id="T32"),
        pytest.param("T33", id="T33"),
    ],
)
def test_t28_t33_contract_phase(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Contract phase for accepted tasks."""
    task_page = TaskPage(e2e_page, e2e_server)
    _open_task_view(task_page, e2e_page, "t-task2")
    api_task = _request_json(e2e_page, e2e_server, "/api/tasks/t-task2")

    if ticket == "T28":
        assert _active_phase(e2e_page) == 2
        assert (task_page.panel_title.text_content() or "").strip() == "Contract"
    elif ticket == "T29":
        assert (task_page.task_status.text_content() or "").strip() == "ACTIVE"
    elif ticket == "T30":
        assert "Contract Signed" in task_page.get_lifecycle_panel_content()
    elif ticket == "T31":
        content = task_page.get_lifecycle_panel_content()
        assert api_task["poster"]["name"] in content
        assert (api_task["worker"] or {})["name"] in content
    elif ticket == "T32":
        assert f"{api_task['reward']}" in task_page.get_lifecycle_panel_content()
    elif ticket == "T33":
        deadline = api_task["deadlines"]["execution_deadline"]
        assert deadline in task_page.get_lifecycle_panel_content()
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T34", id="T34"),
        pytest.param("T35", id="T35"),
        pytest.param("T36", id="T36"),
        pytest.param("T37", id="T37"),
        pytest.param("T38", id="T38"),
        pytest.param("T39", id="T39"),
    ],
)
def test_t34_t39_deliver_phase(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Deliver phase rendering for tasks with assets."""
    task_page = TaskPage(e2e_page, e2e_server)
    _open_task_view(task_page, e2e_page, "t-task1")
    api_task = _request_json(e2e_page, e2e_server, "/api/tasks/t-task1")
    task_page.click_phase(3)

    if ticket == "T34":
        assert (task_page.panel_title.text_content() or "").strip() == "Deliver"
    elif ticket == "T35":
        assert "Assets Delivered" in task_page.get_lifecycle_panel_content()
    elif ticket == "T36":
        content = task_page.get_lifecycle_panel_content()
        for asset in api_task["assets"]:
            assert asset["filename"] in content
    elif ticket == "T37":
        assert "KB" in task_page.get_lifecycle_panel_content()
    elif ticket == "T38":
        assert (
            "Worker is executing... no assets yet." not in task_page.get_lifecycle_panel_content()
        )
    elif ticket == "T39":
        assert "LOCKED" in (task_page.escrow_status.text_content() or "")
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T40", id="T40"),
        pytest.param("T41", id="T41"),
        pytest.param("T42", id="T42"),
        pytest.param("T43", id="T43"),
        pytest.param("T44", id="T44"),
        pytest.param("T45", id="T45"),
        pytest.param("T46", id="T46"),
    ],
)
def test_t40_t46_review_phase(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Review phase for submitted/disputed tasks."""
    task_page = TaskPage(e2e_page, e2e_server)

    if ticket in {"T40", "T41", "T42", "T43"}:
        _open_task_view(task_page, e2e_page, "t-task8")
        api_task = _request_json(e2e_page, e2e_server, "/api/tasks/t-task8")

        if ticket == "T40":
            assert _active_phase(e2e_page) == 4
            assert (task_page.panel_title.text_content() or "").strip() == "Review"
        elif ticket == "T41":
            assert (task_page.task_status.text_content() or "").strip() == "SUBMITTED"
        elif ticket == "T42":
            content = task_page.get_lifecycle_panel_content()
            for asset in api_task["assets"]:
                assert asset["filename"] in content
        elif ticket == "T43":
            assert e2e_page.locator("#btn-approve").count() == 0
            assert e2e_page.locator("#btn-dispute-show").count() == 0
    elif ticket in {"T44", "T45", "T46"}:
        _open_task_view(task_page, e2e_page, "t-task11")
        content = task_page.get_lifecycle_panel_content()
        dispute = _request_json(e2e_page, e2e_server, "/api/tasks/t-task11")["dispute"]
        assert dispute is not None
        if ticket == "T44":
            assert "Dispute" in content
            assert dispute["reason"] in content
        elif ticket == "T45":
            rebuttal = dispute["rebuttal"]
            assert rebuttal is not None
            assert rebuttal["content"] in content
        elif ticket == "T46":
            assert "FROZEN" in (task_page.escrow_status.text_content() or "")
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T47", id="T47"),
        pytest.param("T48", id="T48"),
        pytest.param("T49", id="T49"),
        pytest.param("T50", id="T50"),
        pytest.param("T51", id="T51"),
        pytest.param("T52", id="T52"),
        pytest.param("T53", id="T53"),
        pytest.param("T54", id="T54"),
        pytest.param("T55", id="T55"),
        pytest.param("T56", id="T56"),
    ],
)
def test_t47_t56_dispute_ruling(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Dispute and ruling phase details."""
    task_page = TaskPage(e2e_page, e2e_server)

    if ticket in _T47_RULED_CHECKS:
        _open_task_view(task_page, e2e_page, "t-task5")
        task_page.click_phase(5)
        api_task = _request_json(e2e_page, e2e_server, "/api/tasks/t-task5")
        ruling = api_task["dispute"]["ruling"]
        assert ruling is not None
        content = task_page.get_lifecycle_panel_content()
        _T47_RULED_CHECKS[ticket](task_page, content, ruling)
        return

    if ticket in _T47_DISPUTED_CHECKS:
        _open_task_view(task_page, e2e_page, "t-task11")
        before = _active_phase(e2e_page)
        task_page.click_phase(5)
        e2e_page.wait_for_timeout(120)
        content = task_page.get_lifecycle_panel_content()
        dispute = _request_json(e2e_page, e2e_server, "/api/tasks/t-task11")["dispute"]
        assert dispute is not None
        _T47_DISPUTED_CHECKS[ticket](before, content, dispute, e2e_page)
        return

    raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T57", id="T57"),
        pytest.param("T58", id="T58"),
        pytest.param("T59", id="T59"),
        pytest.param("T60", id="T60"),
        pytest.param("T61", id="T61"),
        pytest.param("T62", id="T62"),
    ],
)
def test_t57_t62_settlement_phase(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Settlement phase behavior and payout/feedback display."""
    task_page = TaskPage(e2e_page, e2e_server)

    if ticket in {"T57", "T58", "T59", "T61", "T62"}:
        _open_task_view(task_page, e2e_page, "t-task1")
        api_task = _request_json(e2e_page, e2e_server, "/api/tasks/t-task1")
        content = task_page.get_lifecycle_panel_content()
        if ticket == "T57":
            assert _active_phase(e2e_page) == 6
            assert (task_page.panel_title.text_content() or "").strip() == "Settle"
            assert (task_page.task_status.text_content() or "").strip() == "APPROVED"
        elif ticket == "T58":
            assert "RELEASED" in (task_page.escrow_amount.text_content() or "")
            assert "SETTLED" in (task_page.escrow_status.text_content() or "")
        elif ticket == "T59":
            assert "TASK LIFECYCLE COMPLETE" in content
        elif ticket == "T61":
            rows = task_page.get_feedback_rows()
            assert len(rows) == len(api_task["feedback"])
            assert len(rows) > 0
        elif ticket == "T62":
            rows = task_page.get_feedback_rows()
            assert rows
            assert all(row["rating"] != "" for row in rows)
    elif ticket == "T60":
        _open_task_view(task_page, e2e_page, "t-task5")
        expected_worker = 70
        expected_poster = 30
        content = task_page.get_lifecycle_panel_content()
        assert f"{expected_poster} \u00a9" in content
        assert f"{expected_worker} \u00a9" in content
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T63", id="T63"),
        pytest.param("T64", id="T64"),
        pytest.param("T65", id="T65"),
        pytest.param("T66", id="T66"),
        pytest.param("T67", id="T67"),
        pytest.param("T68", id="T68"),
    ],
)
def test_t63_t68_event_feed_panel(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
    e2e_db_path: Path,
) -> None:
    """Task page event feed content and SSE update behavior."""
    task_page = TaskPage(e2e_page, e2e_server)
    _open_task_view(task_page, e2e_page, "t-task1")
    feed = _read_task_feed(task_page)
    api_events = _task_events(e2e_page, e2e_server, "t-task1")

    if ticket == "T63":
        assert len(feed) > 0
    elif ticket == "T64":
        assert api_events
        assert feed[0]["text"] == api_events[0]["summary"]
    elif ticket == "T65":
        badges = {item["badge"] for item in feed}
        assert {"TASK", "BID", "CONTRACT", "SUBMIT", "PAYOUT"} & badges
    elif ticket == "T66":
        time_nodes = e2e_page.locator("#feed-scroll .feed-item .feed-time")
        times = [
            (time_nodes.nth(i).text_content() or "").strip() for i in range(time_nodes.count())
        ]
        assert times
        assert all(re.match(r"^\d{2}:\d{2}:\d{2}$", text) for text in times if text)
    elif ticket == "T67":
        text_blob = " ".join(item["text"] for item in feed)
        assert "Judy received salary" not in text_blob
    elif ticket == "T68":
        conn = get_writable_db_connection(str(e2e_db_path))
        summary = f"T68 live task update {int(time.time())}"
        try:
            insert_event(
                conn,
                event_source="board",
                event_type="task.submitted",
                summary=summary,
                payload={"task_id": "t-task1"},
                task_id="t-task1",
                agent_id="a-bob",
                timestamp=_now_iso(),
            )
        finally:
            conn.close()
        wait_for_sse_update(e2e_page, timeout=8_000)
        top = _read_task_feed(task_page)[0]
        assert summary in top["text"]
        assert top["badge"] == "SUBMIT"
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T69", id="T69"),
        pytest.param("T70", id="T70"),
        pytest.param("T71", id="T71"),
        pytest.param("T72", id="T72"),
        pytest.param("T73", id="T73"),
        pytest.param("T74", id="T74"),
        pytest.param("T75", id="T75"),
        pytest.param("T76", id="T76"),
        pytest.param("T77", id="T77"),
        pytest.param("T78", id="T78"),
    ],
)
def test_t69_t78_step_navigation_controls(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Phase-step navigation controls and expected non-controls."""
    task_page = TaskPage(e2e_page, e2e_server)
    _open_task_view(task_page, e2e_page, _task_id_for_t69_ticket(ticket))
    _T69_T78_CHECKS[ticket](task_page, e2e_page)


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket,key",
    [
        pytest.param("T79", "ArrowLeft", id="T79"),
        pytest.param("T80", "ArrowRight", id="T80"),
        pytest.param("T81", "Space", id="T81"),
        pytest.param("T82", "Enter", id="T82"),
        pytest.param("T83", "Home", id="T83"),
        pytest.param("T84", "End", id="T84"),
    ],
)
def test_t79_t84_keyboard_shortcuts(
    ticket: str,
    key: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Keyboard shortcut behavior (currently inert in task.js)."""
    task_page = TaskPage(e2e_page, e2e_server)
    _open_task_view(task_page, e2e_page, "t-task2")
    before = _active_phase(e2e_page)
    e2e_page.keyboard.press(key)
    e2e_page.wait_for_timeout(120)
    after = _active_phase(e2e_page)
    assert before == 2
    assert after == before, f"{ticket} ({key}) unexpectedly changed phase"


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T85", id="T85"),
        pytest.param("T86", id="T86"),
        pytest.param("T87", id="T87"),
        pytest.param("T88", id="T88"),
    ],
)
def test_t85_t88_auto_play(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Auto-play controls (currently absent) and non-transition behavior."""
    task_page = TaskPage(e2e_page, e2e_server)
    _open_task_view(task_page, e2e_page, "t-task2")

    if ticket == "T85":
        assert e2e_page.locator("#btn-auto, .btn-auto").count() == 0
    elif ticket == "T86":
        before = _active_phase(e2e_page)
        e2e_page.wait_for_timeout(3_500)
        assert _active_phase(e2e_page) == before
    elif ticket == "T87":
        with pytest.raises(PlaywrightTimeoutError):
            task_page.click_auto()
    elif ticket == "T88":
        before_text = (task_page.panel_title.text_content() or "").strip()
        e2e_page.wait_for_timeout(3_500)
        after_text = (task_page.panel_title.text_content() or "").strip()
        assert before_text == after_text == "Contract"
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T89", id="T89"),
        pytest.param("T90", id="T90"),
        pytest.param("T91", id="T91"),
        pytest.param("T92", id="T92"),
        pytest.param("T93", id="T93"),
    ],
)
def test_t89_t93_boundary_guards(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Boundary guards for invalid/blocked navigation paths."""
    task_page = TaskPage(e2e_page, e2e_server)

    if ticket == "T89":
        _open_create_mode(task_page, e2e_page)
        before = _active_phase(e2e_page)
        task_page.click_phase(1)
        e2e_page.wait_for_timeout(120)
        assert _active_phase(e2e_page) == before == 0
    elif ticket == "T90":
        _open_task_view(task_page, e2e_page, "t-task2")
        before = _active_phase(e2e_page)
        task_page.click_phase(6)
        e2e_page.wait_for_timeout(120)
        assert _active_phase(e2e_page) == before == 2
    elif ticket == "T91":
        task_page.navigate(task_id="t-not-found")
        e2e_page.wait_for_load_state("domcontentloaded")
        assert _active_phase(e2e_page) == 0
        assert (task_page.task_status.text_content() or "").strip() == "DRAFT"
    elif ticket == "T92":
        _open_task_view(task_page, e2e_page, "t-task1")
        assert e2e_page.locator(".phase-step[data-phase='99']").count() == 0
    elif ticket == "T93":
        _open_create_mode(task_page, e2e_page)
        assert (task_page.task_status.text_content() or "").strip() == "DRAFT"
        assert "Post a New Task" in (task_page.panel_title.text_content() or "")
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T94", id="T94"),
        pytest.param("T95", id="T95"),
        pytest.param("T96", id="T96"),
    ],
)
def test_t94_t96_db_mutation_live_updates(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
    e2e_db_path: Path,
) -> None:
    """DB mutation + SSE tests for live task view updates."""
    task_page = TaskPage(e2e_page, e2e_server)

    if ticket == "T94":
        _open_task_view(task_page, e2e_page, "t-task12")
        assert _active_phase(e2e_page) == 0
        conn = get_writable_db_connection(str(e2e_db_path))
        bid_id = f"bid-t94-{int(time.time())}"
        summary = f"T94 bid submitted {int(time.time())}"
        try:
            add_bid(conn, bid_id=bid_id, task_id="t-task12", bidder_id="a-bob", proposal="T94 bid")
            insert_event(
                conn,
                event_source="board",
                event_type="bid.submitted",
                summary=summary,
                payload={"bid_id": bid_id},
                task_id="t-task12",
                agent_id="a-bob",
                timestamp=_now_iso(),
            )
        finally:
            conn.close()
        wait_for_sse_update(e2e_page, timeout=8_000)
        e2e_page.wait_for_function(
            "document.querySelector('.phase-step.active') && "
            "document.querySelector('.phase-step.active').getAttribute('data-phase') === '1'",
        )
        assert _active_phase(e2e_page) == 1
    elif ticket == "T95":
        _open_task_view(task_page, e2e_page, "t-task2")
        assert _active_phase(e2e_page) == 2
        conn = get_writable_db_connection(str(e2e_db_path))
        summary = f"T95 status moved to submitted {int(time.time())}"
        try:
            advance_task_status(conn, task_id="t-task2", new_status="submitted")
            insert_event(
                conn,
                event_source="board",
                event_type="task.submitted",
                summary=summary,
                payload={"task_id": "t-task2"},
                task_id="t-task2",
                agent_id="a-carol",
                timestamp=_now_iso(),
            )
        finally:
            conn.close()
        wait_for_sse_update(e2e_page, timeout=8_000)
        e2e_page.wait_for_function(
            "document.querySelector('.phase-step.active') && "
            "document.querySelector('.phase-step.active').getAttribute('data-phase') === '4'",
        )
        assert _active_phase(e2e_page) == 4
    elif ticket == "T96":
        _open_task_view(task_page, e2e_page, "t-task1")
        before_count = len(task_page.get_feedback_rows())
        conn = get_writable_db_connection(str(e2e_db_path))
        feedback_id = f"fb-t96-{int(time.time())}"
        summary = f"T96 feedback revealed {int(time.time())}"
        try:
            add_feedback(
                conn,
                feedback_id=feedback_id,
                task_id="t-task1",
                from_agent_id="a-eve",
                to_agent_id="a-alice",
                role="poster",
                category="spec_quality",
                rating="satisfied",
                comment="T96 feedback",
                visible=1,
                submitted_at=_now_iso(),
            )
            insert_event(
                conn,
                event_source="reputation",
                event_type="feedback.revealed",
                summary=summary,
                payload={"feedback_id": feedback_id},
                task_id="t-task1",
                agent_id="a-alice",
                timestamp=_now_iso(),
            )
        finally:
            conn.close()
        wait_for_sse_update(e2e_page, timeout=8_000)
        e2e_page.wait_for_function(
            "document.querySelectorAll('.feedback-row').length >= 3",
            timeout=8_000,
        )
        after_count = len(task_page.get_feedback_rows())
        assert after_count == before_count + 1
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "ticket",
    [
        pytest.param("T97", id="T97"),
        pytest.param("T98", id="T98"),
    ],
)
def test_t97_t98_feedback_display(
    ticket: str,
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Feedback presentation in settlement views."""
    task_page = TaskPage(e2e_page, e2e_server)

    if ticket == "T97":
        _open_task_view(task_page, e2e_page, "t-task1")
        rows = task_page.get_feedback_rows()
        assert rows
        assert any("Alice" in row["from_name"] or "Bob" in row["from_name"] for row in rows)
    elif ticket == "T98":
        _open_task_view(task_page, e2e_page, "t-task5")
        rows = task_page.get_feedback_rows()
        assert rows
        content = task_page.get_lifecycle_panel_content()
        assert "Incomplete work" in content or "Vague requirements" in content
    else:
        raise AssertionError(f"Unhandled ticket: {ticket}")
