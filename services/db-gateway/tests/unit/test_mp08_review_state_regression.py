"""Review regression for explicit Gateway application-state ownership."""

from __future__ import annotations

import pytest

from db_gateway_service.core.state import get_app_state, reset_app_state

pytestmark = pytest.mark.unit


def test_get_app_state_fails_before_explicit_initialization() -> None:
    reset_app_state()
    with pytest.raises(RuntimeError, match="not initialized"):
        get_app_state()
