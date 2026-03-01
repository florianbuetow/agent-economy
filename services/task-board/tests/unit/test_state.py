"""Unit tests for AppState lifecycle helpers."""

from __future__ import annotations

import time

import pytest

from task_board_service.core.state import AppState, get_app_state, init_app_state, reset_app_state


@pytest.mark.unit
def test_app_state_init() -> None:
    """AppState initializes with default dependency fields."""
    state = AppState()
    assert state.task_manager is None
    assert state.identity_client is None
    assert state.central_bank_client is None
    assert state.platform_signer is None
    assert state.escrow_coordinator is None
    assert state.token_validator is None
    assert state.asset_manager is None


@pytest.mark.unit
def test_app_state_uptime() -> None:
    """uptime_seconds increases after initialization."""
    state = AppState()
    time.sleep(0.001)
    assert state.uptime_seconds > 0


@pytest.mark.unit
def test_app_state_started_at() -> None:
    """started_at returns a UTC ISO timestamp."""
    state = AppState()
    assert state.started_at.endswith("Z")
    assert "T" in state.started_at


@pytest.mark.unit
def test_get_app_state_uninitialized() -> None:
    """get_app_state raises RuntimeError before initialization."""
    reset_app_state()
    with pytest.raises(RuntimeError):
        _state = get_app_state()


@pytest.mark.unit
def test_init_app_state() -> None:
    """init_app_state creates and stores an AppState instance."""
    reset_app_state()
    state = init_app_state()
    assert isinstance(state, AppState)
    assert get_app_state() is state
    reset_app_state()


@pytest.mark.unit
def test_reset_app_state() -> None:
    """reset_app_state clears the global state container."""
    _state = init_app_state()
    reset_app_state()
    with pytest.raises(RuntimeError):
        _state = get_app_state()
