"""Child-process app factory for the isolated UI browser suite."""

from __future__ import annotations

import datetime as dt
import os

from _board_read_adapter import reader_from_environment
from _fixture_support import E2E_OPERATOR_AGENT_ID
from service_auth.user_agent import UserAgent

from ui_service.app import create_app as create_ui_app
from ui_service.services import database


async def _register_fixture_operator(agent: UserAgent) -> None:
    """Complete startup without contacting a real Identity service."""
    agent.agent_id = E2E_OPERATOR_AGENT_ID


def create_app():
    """Create the UI app with registration controlled in this child only."""
    reader = reader_from_environment(dict(os.environ))
    UserAgent.register = _register_fixture_operator
    UserAgent.get_task = reader.get_task
    UserAgent.list_tasks = reader.list_tasks
    if os.environ.get("UI_E2E_FROZEN_CLOCK") == "1":
        database._clock = lambda: dt.datetime(2026, 3, 2, 7, 0, 0, tzinfo=dt.UTC)
    return create_ui_app()
