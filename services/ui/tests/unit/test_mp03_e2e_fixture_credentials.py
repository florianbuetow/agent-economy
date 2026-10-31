"""MP-03 guardrails for the isolated browser-test operator fixture."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from service_auth.config import load_agent_config

E2E_SUPPORT = Path(__file__).resolve().parents[1] / "e2e"
if str(E2E_SUPPORT) not in sys.path:
    sys.path.insert(0, str(E2E_SUPPORT))

import _fixture_support as fixture_support  # noqa: E402
from _fixture_support import provision_e2e_operator, start_controlled_ui_server  # noqa: E402


class _TrackingSocket:
    """Minimal socket double that records cleanup."""

    def __init__(self) -> None:
        self.closed = False

    def getsockname(self) -> tuple[str, int]:
        return "127.0.0.1", 43210

    def fileno(self) -> int:
        return 99

    def close(self) -> None:
        self.closed = True


@pytest.mark.unit
def test_browser_fixture_provisions_isolated_loadable_operator(tmp_path: Path) -> None:
    """Browser tests use disposable keys and unreachable fixture endpoints."""
    agent_config_path, handle = provision_e2e_operator(tmp_path)

    assert agent_config_path.is_relative_to(tmp_path)
    assert handle == "e2e-operator"

    raw_config = yaml.safe_load(agent_config_path.read_text())
    keys_dir = Path(raw_config["data"]["keys_dir"])
    roster_path = Path(raw_config["data"]["roster_path"])
    assert keys_dir.is_relative_to(tmp_path)
    assert roster_path.is_relative_to(tmp_path)

    config = load_agent_config(handle, agent_config_path)
    assert config.name == "E2E Operator"
    assert {
        config.identity_url,
        config.bank_url,
        config.task_board_url,
        config.reputation_url,
        config.court_url,
    } == {"http://127.0.0.1:0"}

    assert (keys_dir / f"{handle}.key").stat().st_mode & 0o777 == 0o600
    assert (keys_dir / f"{handle}.pub").is_file()
    assert (keys_dir / f"{handle}.lock").stat().st_mode & 0o777 == 0o600


@pytest.mark.unit
def test_normal_browser_child_removes_ambient_frozen_clock(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal browser child cannot inherit the frozen-clock test switch."""
    captured_environment: dict[str, str] = {}
    tracking_socket = _TrackingSocket()

    def _capture_process(*_args: Any, **kwargs: Any) -> object:
        captured_environment.update(kwargs["env"])
        return object()

    monkeypatch.setenv("UI_E2E_FROZEN_CLOCK", "1")
    monkeypatch.setattr(fixture_support.subprocess, "Popen", _capture_process)
    monkeypatch.setattr(fixture_support, "_wait_for_server", lambda *_args: None)
    monkeypatch.setattr(fixture_support, "_stop_server", lambda *_args: None)

    runner = start_controlled_ui_server(
        tmp_path_factory.mktemp("ambient_clock_db") / "economy.db",
        tmp_path_factory,
        "ambient_clock_server",
        frozen_clock=False,
        socket_factory=lambda: tracking_socket,
    )
    next(runner)
    runner.close()

    assert "UI_E2E_FROZEN_CLOCK" not in captured_environment


@pytest.mark.unit
def test_provision_failure_closes_owned_server_socket(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Setup failure before process launch still closes the held socket."""
    tracking_socket = _TrackingSocket()

    def _fail_provision(_work_dir: Path) -> tuple[Path, str]:
        raise RuntimeError("injected provision failure")

    runner = start_controlled_ui_server(
        tmp_path_factory.mktemp("provision_failure_db") / "economy.db",
        tmp_path_factory,
        "provision_failure_server",
        frozen_clock=False,
        socket_factory=lambda: tracking_socket,
        provision_operator=_fail_provision,
    )
    with pytest.raises(RuntimeError, match="injected provision failure"):
        next(runner)

    assert tracking_socket.closed
