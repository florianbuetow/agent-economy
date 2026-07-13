"""Acceptance tests for the ABSENCE of treasury funding during UI startup.

Frozen-test exception #10 (WP-08, 2026-07-13, recorded in
docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md §5.0):
these three tests used to assert the UI's user agent shared the platform
identity and minted its own treasury account on startup. The ratified Q-9
decision (docs/plans/2026-07-10-q9-treasury-bootstrap-decision.md) moves
genesis to an explicit, idempotent ``just provision`` step
(``agents/src/treasury_provision_cli``) instead — "UI down => no treasury"
is exactly the failure mode Q-9 eliminates. Startup now only registers the
operator identity (Q-2); it never touches the bank.

These tests stub the agent factory so they never touch the network, then
assert on the emitted log file (the ``"ui"`` logger sets ``propagate =
False``, so ``caplog`` cannot see these records).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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


class _UnreachablePlatformAgent:
    """A platform agent that fails loudly if the bank is ever touched.

    Startup must not construct a real platform-agent bank call at all under
    the new contract, so any method invocation here is a test failure — this
    stub exists to catch a regression that reintroduces a startup mint.
    """

    def __init__(self) -> None:
        self.agent_id: str | None = None

    async def create_account(self, agent_id: str, initial_balance: int) -> dict[str, Any]:
        msg = f"create_account({agent_id}, {initial_balance}) called — startup must not mint"
        raise AssertionError(msg)

    async def credit_account(self, account_id: str, amount: int, reference: str) -> dict[str, Any]:
        msg = f"credit_account({account_id}, {amount}, {reference}) called — startup must not mint"
        raise AssertionError(msg)

    async def close(self) -> None:
        return None


class _NoMintFactory:
    """Factory whose platform_agent() explodes if startup ever calls the bank."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    def user_agent(self, handle: str) -> _StubUserAgent:  # noqa: ARG002
        return _StubUserAgent()

    def platform_agent(self) -> _UnreachablePlatformAgent:
        return _UnreachablePlatformAgent()


def _write_ui_config(tmp_path: Path, *, handle: str | None) -> Path:
    """Write a UI config at INFO level; omit ``handle`` when None (tests backfill)."""
    logs_dir = tmp_path / "logs"
    config_path = tmp_path / "config.yaml"
    user_agent_block = '  agent_config_path: "../../agents/config.yaml"\n'
    if handle is not None:
        user_agent_block += f'  handle: "{handle}"\n'
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
async def test_startup_performs_no_treasury_mint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exception #10: startup never mints — no bank call, no mint-related log line."""
    config_path = _write_ui_config(tmp_path, handle="operator")
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setattr(lifespan_module, "AgentFactory", _NoMintFactory)

    clear_settings_cache()
    reset_app_state()
    try:
        async with lifespan(FastAPI()):
            pass
    finally:
        clear_settings_cache()
        reset_app_state()

    log_text = _read_logs(tmp_path)
    assert "UserAgent initialized" in log_text
    assert "treasury" not in log_text.lower()
    assert "funding failed" not in log_text


@pytest.mark.unit
async def test_zero_balance_operator_at_startup_is_not_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exception #10: a freshly-registered, zero-balance operator is a normal startup.

    Genesis belongs to ``just provision`` now, so an operator with no bank
    account yet (as it would be on a completely fresh economy) must not
    surface as any kind of startup failure — even a totally unreachable bank
    (the ``_UnreachablePlatformAgent`` stub) must not be consulted.
    """
    config_path = _write_ui_config(tmp_path, handle="operator")
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setattr(lifespan_module, "AgentFactory", _NoMintFactory)

    clear_settings_cache()
    reset_app_state()
    try:
        async with lifespan(FastAPI()):
            pass
    finally:
        clear_settings_cache()
        reset_app_state()

    log_text = _read_logs(tmp_path)
    assert "UserAgent initialized" in log_text
    assert "UserAgent initialization failed" not in log_text
    assert "treasury" not in log_text.lower()
    assert "funding failed" not in log_text


@pytest.mark.unit
def test_partial_user_agent_block_backfills_handle_no_treasury_balance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exception #10: user_agent config backfills ``handle``; ``treasury_balance`` is gone."""
    config_path = _write_ui_config(tmp_path, handle=None)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    clear_settings_cache()
    try:
        settings = get_settings()
    finally:
        clear_settings_cache()

    assert isinstance(settings.user_agent.handle, str)
    assert settings.user_agent.handle != ""
    assert not hasattr(settings.user_agent, "treasury_balance")
