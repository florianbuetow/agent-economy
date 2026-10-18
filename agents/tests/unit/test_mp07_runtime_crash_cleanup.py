"""Failure-path proofs for the MP-07 subprocess crash harness."""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from tests.e2e.test_mp07_runtime_crash_replay import (
    _cleanup_history,
    _OwnedChild,
    _terminate_owned_child,
    _wait_for_gate,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


class _ClosingAgent:
    def __init__(self, name: str, calls: list[str], *, fail: bool) -> None:
        self._name = name
        self._calls = calls
        self._fail = fail

    async def close(self) -> None:
        self._calls.append(f"close:{self._name}")
        if self._fail:
            raise RuntimeError(f"close failed: {self._name}")


def _sleeping_group() -> _OwnedChild:
    process = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
    return _OwnedChild.capture(process)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("gate_kind", ("missing", "malformed"))
def test_gate_failure_drains_only_the_exact_owned_group(gate_kind: str, tmp_path: Path) -> None:
    owned = _sleeping_group()
    sentinel = _sleeping_group()
    gate = tmp_path / "gate.json"
    if gate_kind == "malformed":
        gate.write_text("{not-json", encoding="utf-8")
    try:
        with pytest.raises((TimeoutError, json.JSONDecodeError)):
            _wait_for_gate(owned, gate, timeout_seconds=0.05)
        _terminate_owned_child(owned)
        assert not _alive(owned.process.pid)
        assert _alive(sentinel.process.pid)
    finally:
        _terminate_owned_child(owned)
        _terminate_owned_child(sentinel)


@pytest.mark.asyncio
async def test_cleanup_attempts_every_group_and_agent_then_aggregates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _sleeping_group()
    second = _sleeping_group()
    calls: list[str] = []
    real_terminate = _terminate_owned_child

    def terminate(child: _OwnedChild) -> None:
        calls.append(f"terminate:{child.process.pid}")
        real_terminate(child)
        if child is first:
            raise RuntimeError("first cleanup failed after drain")

    monkeypatch.setattr(
        "tests.e2e.test_mp07_runtime_crash_replay._terminate_owned_child", terminate
    )
    agents: list[Any] = [
        _ClosingAgent("first", calls, fail=True),
        _ClosingAgent("second", calls, fail=False),
    ]
    with pytest.raises(ExceptionGroup) as captured:
        await _cleanup_history((first, second), agents)

    assert calls == [
        f"terminate:{first.process.pid}",
        f"terminate:{second.process.pid}",
        "close:first",
        "close:second",
    ]
    assert len(captured.value.exceptions) == 2
    assert not _alive(first.process.pid)
    assert not _alive(second.process.pid)
