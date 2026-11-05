"""Pending settlement never appears as finished work or enables feedback."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect
from test_mp08_financial_status_browser import (
    _effect,
    _open_task,
    settlement_browser,
    settlement_page,
)

pytestmark = pytest.mark.unit

# Imported fixtures are intentionally exposed to this module's tests.
__all__ = ["settlement_browser", "settlement_page"]


def test_pre_receipt_pending_settlement_is_not_success_or_feedback_ready(
    settlement_page: Page,
) -> None:
    """REV-06: a delayed Bank acknowledgment remains visibly provisional."""
    _open_task(settlement_page, "submitted", _effect("pending", "absent", None, False))
    expect(settlement_page.locator(".escrow-bar")).to_contain_text("SETTLEMENT PENDING")
    expect(settlement_page.locator(".escrow-bar")).not_to_contain_text("PAID")
    assert settlement_page.locator("#f-feedback-rating").count() == 0


def test_confirmed_receipt_with_pending_task_projection_stays_provisional(
    settlement_page: Page,
) -> None:
    """REV-06: receipt truth is shown without claiming the task update finished."""
    _open_task(
        settlement_page,
        "submitted",
        _effect("pending", "confirmed", "worker_paid", True),
    )
    expect(settlement_page.locator(".escrow-bar")).to_contain_text("PAID · TASK UPDATE PENDING")
    assert settlement_page.locator("#f-feedback-rating").count() == 0
