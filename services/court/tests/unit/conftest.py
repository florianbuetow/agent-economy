"""Unit test fixtures."""

from __future__ import annotations

import os

import pytest

from court_service.config import clear_settings_cache
from court_service.core.state import reset_app_state


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear settings cache and app state between tests."""
    clear_settings_cache()
    reset_app_state()
    yield
    clear_settings_cache()
    reset_app_state()
    os.environ.pop("CONFIG_PATH", None)
