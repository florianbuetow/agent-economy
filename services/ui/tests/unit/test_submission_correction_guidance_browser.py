"""Browser coverage for the frozen-submission recovery contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect, sync_playwright
from test_q_task_overview_browser import OPERATOR, _Harness, _open, _task

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


@pytest.mark.parametrize("role", ["poster", "worker"])
def test_submitted_task_explains_frozen_work_and_dispute_recovery(
    browser: Browser,
    role: str,
) -> None:
    """Neither actor is led to expect an unsupported resubmission control."""
    task = _task(
        "submitted",
        poster={
            "agent_id": OPERATOR if role == "poster" else "a-poster",
            "name": "Poster",
        },
        worker={
            "agent_id": OPERATOR if role == "worker" else "a-worker",
            "name": "Worker",
        },
    )
    page = _open(browser, _Harness(task))
    try:
        orientation = page.locator("#task-orientation")
        expect(orientation).to_contain_text("submitted files are frozen")
        expect(orientation).to_contain_text("cannot be replaced")
        expect(orientation).to_contain_text("approve or dispute")
        expect(orientation).to_contain_text("does not verify worker suitability")
        expect(page.locator("#btn-upload-asset")).to_have_count(0)
        expect(page.locator("#btn-submit-deliverable")).to_have_count(0)
    finally:
        page.close()
