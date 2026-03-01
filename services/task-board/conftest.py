"""Pytest fixture aliases used by unit test fixture helpers."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(name="_app")
def fixture_app_alias(app: Any) -> Any:
    """Compatibility alias for fixtures expecting `_app`."""
    return app
