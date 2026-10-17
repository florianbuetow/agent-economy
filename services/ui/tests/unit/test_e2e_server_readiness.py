"""Deterministic regression proof for controlled UI server readiness."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


@pytest.fixture
def fixture_support(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the shipped fixture helper without retaining an import-path override."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    return importlib.import_module("_fixture_support")


class _ControlledProcess:
    """Process double with a controlled return code and poll count."""

    def __init__(self, return_code: int | None) -> None:
        self.return_code = return_code
        self.poll_count = 0

    def poll(self) -> int | None:
        self.poll_count += 1
        return self.return_code


def _controlled_clock(*values: float) -> Callable[[], float]:
    moments = iter(values)
    return lambda: next(moments)


def _no_sleep(_seconds: float) -> None:
    """Keep controlled-clock unit proofs independent of real wall time."""


@pytest.mark.unit
def test_transient_read_timeout_can_reach_later_healthy_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_support: ModuleType,
) -> None:
    """A listening socket may time out before the child can answer HTTP."""
    process = _ControlledProcess(return_code=None)
    requests: list[tuple[str, float]] = []
    outcomes: list[httpx.Response | httpx.ReadTimeout] = [
        httpx.ReadTimeout("controlled readiness delay"),
        httpx.Response(status_code=200),
    ]

    def _get(url: str, *, timeout: float) -> httpx.Response:
        requests.append((url, timeout))
        outcome = outcomes.pop(0)
        if isinstance(outcome, httpx.ReadTimeout):
            raise outcome
        return outcome

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setattr(fixture_support.time, "monotonic", _controlled_clock(0.0, 0.0, 1.0))
    monkeypatch.setattr(fixture_support.time, "sleep", _no_sleep)

    fixture_support._wait_for_server(43210, process, tmp_path / "server.log", timeout=2.0)

    assert requests == [
        ("http://127.0.0.1:43210/health", 2.0),
        ("http://127.0.0.1:43210/health", 2.0),
    ]
    assert process.poll_count == 2


@pytest.mark.unit
def test_connect_error_can_reach_later_healthy_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_support: ModuleType,
) -> None:
    """Connection refusal remains a retryable readiness outcome."""
    process = _ControlledProcess(return_code=None)
    outcomes: list[httpx.Response | httpx.ConnectError] = [
        httpx.ConnectError("controlled connection refusal"),
        httpx.Response(status_code=200),
    ]

    def _get(_url: str, *, timeout: float) -> httpx.Response:
        assert timeout == 2.0
        outcome = outcomes.pop(0)
        if isinstance(outcome, httpx.ConnectError):
            raise outcome
        return outcome

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setattr(fixture_support.time, "monotonic", _controlled_clock(0.0, 0.0, 1.0))
    monkeypatch.setattr(fixture_support.time, "sleep", _no_sleep)

    fixture_support._wait_for_server(43210, process, tmp_path / "server.log", timeout=2.0)

    assert process.poll_count == 2
    assert outcomes == []


@pytest.mark.unit
def test_persistent_read_timeouts_end_with_server_log_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_support: ModuleType,
) -> None:
    """Exhausting the polling budget reports the retained child log."""
    process = _ControlledProcess(return_code=None)
    server_log = tmp_path / "server.log"
    server_log.write_text("controlled child still starting\n")
    request_count = 0

    def _timeout(_url: str, *, timeout: float) -> httpx.Response:
        nonlocal request_count
        assert timeout == 2.0
        request_count += 1
        raise httpx.ReadTimeout("controlled readiness delay")

    monkeypatch.setattr(httpx, "get", _timeout)
    monkeypatch.setattr(
        fixture_support.time,
        "monotonic",
        _controlled_clock(0.0, 0.0, 1.0, 2.0),
    )
    monkeypatch.setattr(fixture_support.time, "sleep", _no_sleep)

    with pytest.raises(TimeoutError) as exc_info:
        fixture_support._wait_for_server(43210, process, server_log, timeout=2.0)

    message = str(exc_info.value)
    assert "Server on port 43210 did not become healthy within 2.0s." in message
    assert f"Server log ({server_log}):" in message
    assert "controlled child still starting" in message
    assert request_count == 2
    assert process.poll_count == 2


@pytest.mark.unit
def test_child_exit_fails_immediately_with_server_log_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_support: ModuleType,
) -> None:
    """A terminated child is diagnosed before any health request is sent."""
    process = _ControlledProcess(return_code=23)
    server_log = tmp_path / "server.log"
    server_log.write_text("controlled child startup failure\n")
    request_count = 0

    def _unexpected_get(_url: str, *, timeout: float) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise AssertionError(f"unexpected health request with timeout {timeout}")

    monkeypatch.setattr(httpx, "get", _unexpected_get)
    monkeypatch.setattr(fixture_support.time, "monotonic", _controlled_clock(0.0, 0.0))

    with pytest.raises(RuntimeError) as exc_info:
        fixture_support._wait_for_server(43210, process, server_log, timeout=2.0)

    message = str(exc_info.value)
    assert "UI E2E server exited with code 23 before readiness." in message
    assert f"Server log ({server_log}):" in message
    assert "controlled child startup failure" in message
    assert request_count == 0
    assert process.poll_count == 1
