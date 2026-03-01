"""Unit test fixtures."""

from __future__ import annotations

import pytest

from reputation_service.config import clear_settings_cache
from reputation_service.core.state import reset_app_state


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Clear settings cache and app state between tests."""
    clear_settings_cache()
    reset_app_state()
