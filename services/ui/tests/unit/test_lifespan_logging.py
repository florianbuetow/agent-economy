"""Regression test for the UserAgent init success-log.

Bug: ``ui_service.core.lifespan`` logged the UserAgent success with
``extra={"name": ...}``. ``name`` is a reserved ``LogRecord`` attribute, so at
INFO level ``logging.Logger.makeRecord`` raised
``KeyError("Attempt to overwrite 'name' in LogRecord")``. The broad ``except``
then mislabeled a *successful* registration as
``"UserAgent initialization failed — proxy endpoints will be unavailable"`` on
every boot, and the success signal was lost.

The bug only manifests at INFO level: ``Logger.info`` short-circuits via
``isEnabledFor`` before ``makeRecord`` runs, so the existing WARNING-level
integration suite never tripped it. This test therefore runs the lifespan with
INFO logging and a stubbed, successful agent, then asserts on the emitted log
file (the ``"ui"`` logger sets ``propagate = False``, so ``caplog`` cannot see
these records).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI

from ui_service.config import clear_settings_cache
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


class _StubAgentFactory:
    """Drop-in for ``base_agent.AgentFactory`` that never touches the network."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    def user_agent(self) -> _StubUserAgent:
        return _StubUserAgent()


def _write_ui_config(tmp_path: Path) -> Path:
    """Write a minimal, valid UI config at INFO level into ``tmp_path``."""
    logs_dir = tmp_path / "logs"
    config_path = tmp_path / "config.yaml"
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
""",
    )
    return config_path


@pytest.mark.unit
async def test_useragent_init_success_is_logged_not_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful UserAgent registration logs success — never the failure path."""
    config_path = _write_ui_config(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setattr(lifespan_module, "AgentFactory", _StubAgentFactory)

    clear_settings_cache()
    reset_app_state()
    try:
        async with lifespan(FastAPI()):
            pass
    finally:
        clear_settings_cache()
        reset_app_state()

    log_text = "\n".join(path.read_text() for path in (tmp_path / "logs").glob("*.log"))

    assert "UserAgent initialized" in log_text
    assert "UserAgent initialization failed" not in log_text
