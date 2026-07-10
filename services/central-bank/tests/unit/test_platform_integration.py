"""Unit tests for platform agent integration behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from service_commons.exceptions import ServiceError

from central_bank_service.config import clear_settings_cache
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
def test_get_platform_agent_id_errors_when_no_platform_agent(
    _use_real_config: None,
) -> None:
    """With no registered PlatformAgent, resolving the platform id is an explicit
    not-ready error — there is no configured fallback (WP-03.1)."""
    reset_app_state()
    init_app_state()

    state = get_app_state()
    state.platform_agent = None

    with pytest.raises(ServiceError) as exc_info:
        get_platform_agent_id()
    assert exc_info.value.status_code == 503

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
