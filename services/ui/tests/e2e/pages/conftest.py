"""Page object fixtures for E2E tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .landing import LandingPage
from .observatory import ObservatoryPage
from .task import TaskPage

if TYPE_CHECKING:
    from playwright.sync_api import Page


@pytest.fixture
def landing_page(e2e_page: Page, e2e_server: str) -> LandingPage:
    """Create a LandingPage object for the test."""
    return LandingPage(e2e_page, e2e_server)


@pytest.fixture
def observatory_page(e2e_page: Page, e2e_server: str) -> ObservatoryPage:
    """Create an ObservatoryPage object for the test."""
    return ObservatoryPage(e2e_page, e2e_server)


@pytest.fixture
def task_page(e2e_page: Page, e2e_server: str) -> TaskPage:
    """Create a TaskPage object for the test."""
    return TaskPage(e2e_page, e2e_server)
