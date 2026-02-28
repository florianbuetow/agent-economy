"""Unit test fixtures — cache clearing."""

import pytest

from observatory_service.config import clear_settings_cache
from observatory_service.core.state import reset_app_state


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear settings cache and app state between tests."""
    clear_settings_cache()
    reset_app_state()
    yield
    clear_settings_cache()
    reset_app_state()
