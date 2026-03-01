"""Unit tests for platform agent integration behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from central_bank_service.config import clear_settings_cache, get_settings
from central_bank_service.core.state import get_app_state, init_app_state, reset_app_state
from central_bank_service.routers.helpers import get_platform_agent_id


@pytest.fixture()
def _use_real_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point settings loader to this service's real config file."""
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.mark.unit
def test_get_platform_agent_id_returns_config_value_when_no_platform_agent(
    _use_real_config: None,
) -> None:
    """Falls back to config platform.agent_id when no runtime PlatformAgent exists."""
    reset_app_state()
    init_app_state()

    state = get_app_state()
    state.platform_agent = None

    assert get_platform_agent_id() == get_settings().platform.agent_id

    reset_app_state()


@pytest.mark.unit
def test_get_platform_agent_id_returns_runtime_platform_agent_id(_use_real_config: None) -> None:
    """Uses runtime PlatformAgent.agent_id when available."""

    @dataclass
    class MockPlatformAgent:
        agent_id: str | None

    reset_app_state()
    init_app_state()

    state = get_app_state()
    state.platform_agent = cast("Any", MockPlatformAgent(agent_id="a-platform-runtime"))

    assert get_platform_agent_id() == "a-platform-runtime"

    reset_app_state()
