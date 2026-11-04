"""Visible navigation and task controls remain usable at narrow widths."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Page

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (390, 844)])
@pytest.mark.parametrize(
    "path", ["/", "/task.html", "/agent.html", "/observatory.html", "/quarterly-report.html"]
)
def test_navigation_and_content_fit(
    e2e_page: Page, e2e_server: str, tmp_path: Path, path: str, width: int, height: int
) -> None:
    """Every visible navigation destination fits and content is not clipped."""
    e2e_page.set_viewport_size({"width": width, "height": height})
    e2e_page.goto(e2e_server + path)
    e2e_page.locator("nav").wait_for()
    if path == "/task.html":
        e2e_page.locator("#f-title").wait_for()
    if path == "/":
        e2e_page.locator("#kpi-strip .kpi-cell").first.wait_for()
    elif path == "/agent.html":
        e2e_page.locator(".lb-row").first.wait_for()
    elif path == "/observatory.html":
        e2e_page.locator("#gdp-panel").wait_for()
    elif path == "/quarterly-report.html":
        expect(e2e_page.locator("#qr-quarter-label")).not_to_have_text("—")
    e2e_page.screenshot(path=str(tmp_path / "viewport.png"), full_page=True, animations="disabled")
    for link in e2e_page.locator("nav a, nav button").all():
        box = link.bounding_box()
        assert box is not None
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
    if path == "/task.html":
        for control in e2e_page.locator(".form-input, .form-textarea").all():
            box = control.bounding_box()
            assert box is not None
            assert box["width"] >= 200
            assert box["x"] + box["width"] <= width + 1


def test_posting_requirements_and_keyboard_navigation(e2e_page: Page, e2e_server: str) -> None:
    """Required fields are announced and the home link works with a keyboard."""
    e2e_page.goto(e2e_server + "/task.html")
    expect(e2e_page.get_by_label("Title", exact=True)).to_have_attribute("required", "")
    expect(e2e_page.get_by_label("Specification", exact=True)).to_have_attribute("required", "")
    brand = e2e_page.get_by_role("link", name="ATE Agent Task Economy")
    brand.focus()
    brand.press("Enter")
    expect(e2e_page).to_have_url(e2e_server + "/")


def test_task_orientation_and_phase_keyboard(e2e_page: Page, e2e_server: str) -> None:
    """A task identifies itself and its current actor before the detailed history."""
    e2e_page.goto(e2e_server + "/task.html?task_id=t-task1")
    expect(e2e_page.get_by_role("heading", name="Build Login Page", exact=True)).to_be_visible()
    expect(e2e_page.locator("#task-orientation")).to_contain_text("Acting as")
    expect(e2e_page.locator("#task-orientation")).to_contain_text("What happens now")
    phase = e2e_page.locator('[data-phase="0"]')
    expect(phase).to_have_attribute("role", "button")
    phase.focus()
    phase.press("Enter")
    expect(phase).to_have_attribute("aria-current", "step")


@pytest.mark.parametrize(
    "path,ready",
    [
        ("/", ".kpi-cell"),
        ("/task.html?task_id=t-task1", "#task-orientation"),
        ("/agent.html?agent_id=a-alice", ".profile-name"),
        ("/observatory.html", ".gdp-panel .gdp-big"),
        ("/quarterly-report.html?quarter=2026-Q1", ".metric-cell"),
    ],
)
@pytest.mark.parametrize("width,height", [(1440, 900), (768, 1024), (390, 844), (720, 450)])
def test_populated_content_reflows(
    e2e_page: Page,
    e2e_server: str,
    tmp_path: Path,
    path: str,
    ready: str,
    width: int,
    height: int,
) -> None:
    """Populated content remains in its viewport, including the 200% layout size."""
    e2e_page.set_viewport_size({"width": width, "height": height})
    e2e_page.emulate_media(reduced_motion="reduce")
    e2e_page.goto(e2e_server + path)
    e2e_page.locator(ready).first.wait_for()
    e2e_page.screenshot(path=str(tmp_path / "populated.png"), full_page=True, animations="disabled")
    assert e2e_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for control in e2e_page.locator(
        "button:visible, input:visible, select:visible, textarea:visible"
    ).all():
        box = control.bounding_box()
        assert box is not None
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1


def test_input_files_are_not_presented_as_outputs(e2e_page: Page, e2e_server: str) -> None:
    """An input is downloadable once and never described as delivered work."""
    task = e2e_page.request.get(e2e_server + "/api/tasks/t-task1").json()
    asset = {**task["assets"][0], "filename": "request.txt", "role": "input"}
    task["assets"] = [asset]
    e2e_page.route("**/api/tasks/t-task1", lambda route: route.fulfill(json=task))
    e2e_page.route(
        "**/api/proxy/tasks/t-task1/inputs",
        lambda route: route.fulfill(
            json={
                "input_manifest": {"files": [{**asset, "content_sha256": "a" * 64}]},
            }
        ),
    )
    e2e_page.goto(e2e_server + "/task.html?task_id=t-task1")
    expect(e2e_page.get_by_text("request.txt", exact=True)).to_have_count(1)
    expect(e2e_page.locator("#task-section-3")).not_to_contain_text("request.txt")
    expect(
        e2e_page.locator("#task-section-4 .task-detail-key", has_text="request.txt")
    ).to_have_count(0)


def test_monitoring_selection_is_announced(e2e_page: Page, e2e_server: str) -> None:
    """Keyboard activation exposes the selected monitoring filters."""
    e2e_page.goto(e2e_server + "/observatory.html")
    selected = e2e_page.locator('[data-filter="ALL"]')
    expect(selected).to_have_attribute("aria-pressed", "true")
    other = e2e_page.locator('[data-filter="TASK"]')
    other.focus()
    other.press("Enter")
    expect(e2e_page.locator('[data-filter="TASK"]')).to_have_attribute("aria-pressed", "true")
    expect(e2e_page.locator('[data-filter="ALL"]')).to_have_attribute("aria-pressed", "false")
    e2e_page.locator("#tab-posters").focus()
    e2e_page.locator("#tab-posters").press("Enter")
    expect(e2e_page.locator("#tab-posters")).to_have_attribute("aria-pressed", "true")
    expect(e2e_page.locator("#tab-workers")).to_have_attribute("aria-pressed", "false")


def test_agent_network_failure_is_not_missing_identity(e2e_page: Page, e2e_server: str) -> None:
    """A failed profile request provides recovery instead of a false not-found claim."""
    e2e_page.route("**/api/agents/a-alice", lambda route: route.abort("failed"))
    e2e_page.goto(e2e_server + "/agent.html?agent_id=a-alice")
    expect(e2e_page.get_by_role("alert")).to_contain_text("could not be loaded")
    expect(e2e_page.get_by_role("link", name="Reload profile")).to_be_visible()


def test_open_task_explains_bidder_role(e2e_page: Page, e2e_server: str) -> None:
    """A person who can bid is not described as only an observer."""
    e2e_page.goto(e2e_server + "/task.html?task_id=t-task6")
    expect(e2e_page.locator("#btn-submit-bid")).to_be_visible()
    expect(e2e_page.locator("#task-orientation")).to_contain_text("Potential bidder")


def test_terminal_participant_is_prompted_for_feedback(e2e_page: Page, e2e_server: str) -> None:
    """A participant's next step matches the available feedback form."""
    task = e2e_page.request.get(e2e_server + "/api/tasks/t-task1").json()
    task["feedback"] = []
    e2e_page.route("**/api/tasks/t-task1", lambda route: route.fulfill(json=task))
    e2e_page.route(
        "**/api/proxy/identity", lambda route: route.fulfill(json={"agent_id": "a-alice"})
    )
    e2e_page.goto(e2e_server + "/task.html?task_id=t-task1")
    expect(e2e_page.locator("#btn-submit-feedback")).to_be_visible()
    expect(e2e_page.locator("#task-orientation")).to_contain_text("Leave feedback")


def test_input_alone_does_not_advance_delivery(e2e_page: Page, e2e_server: str) -> None:
    """An accepted job with only input files is still waiting for worker output."""
    task = e2e_page.request.get(e2e_server + "/api/tasks/t-task1").json()
    task["status"] = "accepted"
    task["assets"] = [{**task["assets"][0], "role": "input"}]
    e2e_page.route("**/api/tasks/t-task1", lambda route: route.fulfill(json=task))
    e2e_page.goto(e2e_server + "/task.html?task_id=t-task1")
    expect(e2e_page.locator("#task-orientation")).to_be_visible()
    expect(e2e_page.locator('[data-phase="3"]')).to_have_attribute("tabindex", "-1")


def test_normal_approval_refund_is_not_a_court_ruling(e2e_page: Page, e2e_server: str) -> None:
    """A paid accepted price and unused budget refund do not imply a dispute."""
    task = e2e_page.request.get(e2e_server + "/api/tasks/t-task5").json()
    task["status"] = "approved"
    task["dispute"] = None
    e2e_page.route("**/api/tasks/t-task5", lambda route: route.fulfill(json=task))
    e2e_page.goto(e2e_server + "/task.html?task_id=t-task5")
    expect(e2e_page.locator(".payment-status")).to_contain_text("worker")
    expect(e2e_page.locator(".payment-status")).not_to_contain_text("court ruling")


def test_posting_error_is_announced_and_preserves_values(e2e_page: Page, e2e_server: str) -> None:
    """A correctable posting error retains the draft and is announced."""
    e2e_page.goto(e2e_server + "/task.html")
    e2e_page.get_by_label("Title", exact=True).fill("Preserve this draft")
    e2e_page.locator("#btn-post-task").focus()
    e2e_page.locator("#btn-post-task").press("Enter")
    expect(e2e_page.locator("#post-error")).not_to_be_empty()
    expect(e2e_page.locator("#post-error")).to_have_attribute("role", "alert")
    expect(e2e_page.get_by_label("Title", exact=True)).to_have_value("Preserve this draft")
