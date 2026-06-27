"""Acceptance tests for platform-treasury funding during UI startup.

The UI's user agent shares the platform identity and posts tasks, which lock
escrow from its own bank account. ``lifespan`` mints that account on startup so
posting works; without it ``escrow_lock`` fails with ``account_not_found``,
surfaced to the UI as a misleading 404 on ``POST /tasks``.

These tests stub the agent factory so they never touch the network, then assert
on the emitted log file (the ``"ui"`` logger sets ``propagate = False``, so
``caplog`` cannot see these records).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi import FastAPI

from ui_service.config import clear_settings_cache, get_settings
from ui_service.core import lifespan as lifespan_module
from ui_service.core.lifespan import lifespan
from ui_service.core.state import reset_app_state

if TYPE_CHECKING:
    from pathlib import Path


class _StubUserAgent:
    """Minimal stand-in whose registration always succeeds."""

    agent_id = "a-test-ui"
    name = "ui-user-agent"

    async def register(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _SuccessPlatformAgent:
    """Platform agent whose account creation succeeds."""

    def __init__(self) -> None:
        self.agent_id: str | None = None

    async def create_account(self, agent_id: str, initial_balance: int) -> dict[str, Any]:
        return {"account_id": agent_id, "balance": initial_balance}

    async def close(self) -> None:
        return None


class _ConflictPlatformAgent:
    """Platform agent whose account already exists (409)."""

    def __init__(self) -> None:
        self.agent_id: str | None = None

    async def create_account(self, agent_id: str, initial_balance: int) -> dict[str, Any]:
        request = httpx.Request("POST", f"http://bank/accounts/{agent_id}")
        response = httpx.Response(409, request=request)
        msg = f"account already exists (requested balance {initial_balance})"
        raise httpx.HTTPStatusError(msg, request=request, response=response)

    async def close(self) -> None:
        return None


class _SuccessFactory:
    """Factory whose treasury funding succeeds."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    def user_agent(self) -> _StubUserAgent:
        return _StubUserAgent()

    def platform_agent(self) -> _SuccessPlatformAgent:
        return _SuccessPlatformAgent()


class _ConflictFactory:
    """Factory whose treasury account already exists."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    def user_agent(self) -> _StubUserAgent:
        return _StubUserAgent()

    def platform_agent(self) -> _ConflictPlatformAgent:
        return _ConflictPlatformAgent()


def _write_ui_config(tmp_path: Path, *, treasury_balance: int | None) -> Path:
    """Write a UI config at INFO level; omit ``treasury_balance`` when None."""
    logs_dir = tmp_path / "logs"
    config_path = tmp_path / "config.yaml"
    user_agent_block = '  agent_config_path: "../../agents/config.yaml"\n'
    if treasury_balance is not None:
        user_agent_block += f"  treasury_balance: {treasury_balance}\n"
    config_path.write_text(
        f"""\
service:
  name: "ui"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
logging:
  level: "INFO"
  directory: "{logs_dir}"
database:
  path: "{tmp_path / "nonexistent.db"}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "{tmp_path / "web"}"
request:
  max_body_size: 1572864
user_agent:
{user_agent_block}""",
    )
    return config_path


def _read_logs(tmp_path: Path) -> str:
    return "\n".join(path.read_text() for path in (tmp_path / "logs").glob("*.log"))


@pytest.mark.unit
async def test_treasury_funded_is_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful treasury mint logs success, never the failure path."""
    config_path = _write_ui_config(tmp_path, treasury_balance=750)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setattr(lifespan_module, "AgentFactory", _SuccessFactory)

    clear_settings_cache()
    reset_app_state()
    try:
        async with lifespan(FastAPI()):
            pass
    finally:
        clear_settings_cache()
        reset_app_state()

    log_text = _read_logs(tmp_path)
    assert "Platform treasury funded" in log_text
    assert "funding failed" not in log_text


@pytest.mark.unit
async def test_existing_treasury_account_is_not_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 409 (account already exists) is informational, not a failure."""
    config_path = _write_ui_config(tmp_path, treasury_balance=750)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setattr(lifespan_module, "AgentFactory", _ConflictFactory)

    clear_settings_cache()
    reset_app_state()
    try:
        async with lifespan(FastAPI()):
            pass
    finally:
        clear_settings_cache()
        reset_app_state()

    log_text = _read_logs(tmp_path)
    assert "Platform treasury account already exists" in log_text
    assert "funding failed" not in log_text


@pytest.mark.unit
def test_partial_user_agent_block_backfills_treasury_balance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user_agent block missing treasury_balance is backfilled, not rejected."""
    config_path = _write_ui_config(tmp_path, treasury_balance=None)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    clear_settings_cache()
    try:
        settings = get_settings()
    finally:
        clear_settings_cache()

    assert isinstance(settings.user_agent.treasury_balance, int)
    assert settings.user_agent.treasury_balance > 0
