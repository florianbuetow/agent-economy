"""The public Task Board reader survives Identity registration failure."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI

from ui_service.core import lifespan as lifespan_module
from ui_service.core.lifespan import lifespan
from ui_service.core.state import get_app_state, reset_app_state

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


class _Database:
    row_factory = None

    async def close(self) -> None:
        return None


class _RegistrationFailureAgent:
    agent_id = None
    name = "operator"

    def __init__(self) -> None:
        self.close_count = 0

    async def register(self) -> None:
        raise RuntimeError("Identity unavailable")

    async def close(self) -> None:
        self.close_count += 1


async def test_reader_is_available_before_registration_and_closed_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _RegistrationFailureAgent()
    settings = SimpleNamespace(
        service=SimpleNamespace(name="ui-test", version="0.1.0"),
        server=SimpleNamespace(port=18008),
        logging=SimpleNamespace(level="WARNING", directory=str(tmp_path / "logs")),
        database=SimpleNamespace(path=str(tmp_path / "economy.db")),
        frontend=SimpleNamespace(web_root=str(tmp_path / "web")),
        user_agent=SimpleNamespace(
            agent_config_path=str(tmp_path / "agents.yaml"),
            handle="operator",
        ),
        recovery=SimpleNamespace(maintenance_lock_path=tmp_path / "maintenance.lock"),
    )

    class _Factory:
        def __init__(self, *, config_path: Path) -> None:
            assert config_path == (tmp_path / "agents.yaml").resolve()

        def user_agent(self, handle: str) -> _RegistrationFailureAgent:
            assert handle == "operator"
            return agent

    async def _connect(*_args: object, **_kwargs: object) -> _Database:
        return _Database()

    monkeypatch.setattr(lifespan_module, "get_settings", lambda: settings)
    monkeypatch.setattr(lifespan_module, "setup_logging", lambda *_args: None)
    monkeypatch.setattr(lifespan_module, "AgentFactory", _Factory)
    monkeypatch.setattr(lifespan_module.aiosqlite, "connect", _connect)
    reset_app_state()
    try:
        async with lifespan(FastAPI()):
            state = get_app_state()
            assert state.task_board_reader is agent
            assert state.user_agent is None
            assert agent.close_count == 0
        assert agent.close_count == 1
    finally:
        reset_app_state()
