"""Smoke tests — verify E2E infrastructure is wired correctly."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page


@pytest.mark.e2e
def test_landing_page_loads(e2e_page: Page) -> None:
    """The landing page should load and contain a title."""
    e2e_page.goto(f"{e2e_page.base_url}/")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")
    assert e2e_page.title() != ""


@pytest.mark.e2e
def test_observatory_page_loads(e2e_page: Page) -> None:
    """The observatory page should load."""
    e2e_page.goto(f"{e2e_page.base_url}/observatory.html")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("domcontentloaded")
    assert e2e_page.title() != ""


@pytest.mark.e2e
def test_task_page_loads(e2e_page: Page) -> None:
    """The task lifecycle page should load."""
    e2e_page.goto(f"{e2e_page.base_url}/task.html")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")
    assert e2e_page.title() != ""


@pytest.mark.e2e
def test_health_endpoint(e2e_page: Page) -> None:
    """The /health API endpoint should return status ok."""
    resp = e2e_page.request.get(f"{e2e_page.base_url}/health")  # type: ignore[attr-defined]
    assert resp.status == 200
    body = resp.json()
    assert body["status"] == "ok"


@pytest.mark.e2e
def test_metrics_api(e2e_page: Page) -> None:
    """The /api/metrics endpoint should return economy metrics."""
    resp = e2e_page.request.get(f"{e2e_page.base_url}/api/metrics")  # type: ignore[attr-defined]
    assert resp.status == 200
    body = resp.json()
    assert "gdp" in body
    assert "agents" in body
