"""Regression coverage for MP-05 owned-process cleanup signal ordering."""

from __future__ import annotations

import contextlib
import errno
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from scripts.isolated_test_runtime import (
    _drain_owned_process_group,
    _release_owned_launch,
    _start_owned_launch,
)
from tests.runtime import test_mp05_demo_cli_restart as demo_cli
from tests.runtime import test_mp05_demo_native_restart as demo_native
from tests.runtime import test_mp05_demo_other_action_restart as demo_other_action
from tests.runtime import test_mp05_demo_setup_restart as demo_setup
from tests.runtime import test_mp05_legacy_refund_process_restart as legacy_refund

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

pytestmark = pytest.mark.integration

_Termination = Callable[[Any], None]
_CASES: tuple[tuple[str, ModuleType, type[Any], _Termination], ...] = (
    (
        "legacy-refund",
        legacy_refund,
        legacy_refund._OwnedBoard,
        legacy_refund._terminate_board,
    ),
    ("demo-cli", demo_cli, demo_cli._OwnedChild, demo_cli._terminate_owned),
    ("demo-setup", demo_setup, demo_setup._OwnedChild, demo_setup._terminate_owned),
    (
        "demo-native",
        demo_native,
        demo_native._OwnedChild,
        demo_native._terminate_owned,
    ),
    (
        "demo-other-action",
        demo_other_action,
        demo_other_action._OwnedChild,
        demo_other_action._terminate_owned,
    ),
)


@dataclass(slots=True)
class _LaunchGate:
    descriptor: int | None

    def release(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            raise RuntimeError("Test launch gate is no longer owned")
        try:
            _release_owned_launch(descriptor)
        finally:
            self.descriptor = None

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        os.close(descriptor)


def _start_owned(
    owner_type: type[Any],
    root: Path,
    *,
    ownership: contextlib.ExitStack,
) -> Any:
    process, pgid, sid, descriptor = _start_owned_launch(
        ["/bin/sleep", "60"],
        cwd=str(root),
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(),
    )
    gate = _LaunchGate(descriptor)
    ownership.callback(gate.close)
    child = owner_type(
        process=process,
        pgid=pgid,
        sid=sid,
        actor_pid=process.pid,
    )
    ownership.callback(_ensure_cleanup, child)
    gate.release()
    return child


def _drain(child: Any) -> None:
    _drain_owned_process_group(
        child.process,
        pgid=child.pgid,
        sid=child.sid,
    )
    child.drained = True


def _ensure_cleanup(child: Any) -> None:
    try:
        os.getpgid(child.actor_pid)
    except ProcessLookupError:
        child.process.wait(timeout=5.0)
        return
    if child.process.poll() is None:
        _drain(child)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _legacy_duplicate_cleanup(child: Any) -> None:
    if child.actor_pid is not None:
        with contextlib.suppress(ProcessLookupError):
            if (
                os.getpgid(child.actor_pid) == child.pgid
                and os.getsid(child.actor_pid) == child.sid
            ):
                os.kill(child.actor_pid, signal.SIGKILL)
    os.killpg(child.pgid, signal.SIGKILL)


def _inject_group_kill_denial_after_exact_kill(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    child: Any,
) -> dict[str, bool]:
    state = {"exact_kill_succeeded": False, "group_kill_attempted": False}
    real_kill = os.kill
    real_killpg = os.killpg

    def record_exact_kill(pid: int, selected_signal: int) -> None:
        real_kill(pid, selected_signal)
        if pid == child.actor_pid and selected_signal == signal.SIGKILL:
            state["exact_kill_succeeded"] = True

    def deny_group_kill(pgid: int, selected_signal: int) -> None:
        if (
            pgid == child.pgid
            and selected_signal == signal.SIGKILL
            and state["exact_kill_succeeded"]
        ):
            state["group_kill_attempted"] = True
            raise PermissionError(errno.EPERM, "injected dying-group denial")
        real_killpg(pgid, selected_signal)

    monkeypatch.setattr(module.os, "kill", record_exact_kill)
    monkeypatch.setattr(module.os, "killpg", deny_group_kill)
    return state


def test_old_pid_then_group_sequence_reproduces_partial_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with contextlib.ExitStack() as ownership:
        child = _start_owned(
            legacy_refund._OwnedBoard,
            tmp_path,
            ownership=ownership,
        )
        try:
            state = _inject_group_kill_denial_after_exact_kill(
                monkeypatch,
                legacy_refund,
                child,
            )
            with pytest.raises(PermissionError, match="injected dying-group denial"):
                _legacy_duplicate_cleanup(child)
            assert state == {
                "exact_kill_succeeded": True,
                "group_kill_attempted": True,
            }
            assert not child.drained
        finally:
            monkeypatch.undo()


def test_start_owned_release_failure_reaps_the_registered_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: tuple[subprocess.Popen[bytes], int, int, int] | None = None
    real_start = _start_owned_launch
    real_release = _release_owned_launch

    def capture_start(*args: Any, **kwargs: Any) -> tuple[subprocess.Popen[bytes], int, int, int]:
        nonlocal captured
        captured = real_start(*args, **kwargs)
        return captured

    def fail_release(descriptor: int) -> None:
        real_release(descriptor)
        raise BrokenPipeError("injected test launch release failure")

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_start_owned_launch", capture_start)
    monkeypatch.setattr(module, "_release_owned_launch", fail_release)

    with (
        pytest.raises(BrokenPipeError, match="injected test launch release failure"),
        contextlib.ExitStack() as ownership,
    ):
        _start_owned(
            legacy_refund._OwnedBoard,
            tmp_path,
            ownership=ownership,
        )

    assert captured is not None
    process, pgid, _sid, descriptor = captured
    assert process.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)
    with pytest.raises(OSError):
        os.fstat(descriptor)


@pytest.mark.parametrize(
    ("case_name", "module", "owner_type", "terminate"),
    _CASES,
    ids=[case[0] for case in _CASES],
)
def test_cleanup_does_not_signal_a_dying_group_after_an_exact_kill(
    case_name: str,
    module: ModuleType,
    owner_type: type[Any],
    terminate: _Termination,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del case_name
    with contextlib.ExitStack() as ownership:
        child = _start_owned(owner_type, tmp_path, ownership=ownership)
        sentinel = _start_owned(owner_type, tmp_path, ownership=ownership)
        try:
            state = _inject_group_kill_denial_after_exact_kill(monkeypatch, module, child)
            terminate(child)
            assert child.drained
            assert child.process.poll() is not None
            assert not state["exact_kill_succeeded"]
            assert not state["group_kill_attempted"]
            assert _alive(sentinel.actor_pid)
        finally:
            monkeypatch.undo()


@pytest.mark.parametrize(
    ("case_name", "module", "owner_type", "terminate"),
    _CASES,
    ids=[case[0] for case in _CASES],
)
def test_persistent_group_denial_propagates_without_releasing_ownership(
    case_name: str,
    module: ModuleType,
    owner_type: type[Any],
    terminate: _Termination,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del case_name
    with contextlib.ExitStack() as ownership:
        child = _start_owned(owner_type, tmp_path, ownership=ownership)
        real_killpg = os.killpg

        def deny_group_signals(pgid: int, selected_signal: int) -> None:
            if pgid == child.pgid and selected_signal in {signal.SIGTERM, signal.SIGKILL}:
                raise PermissionError(errno.EPERM, "injected persistent group denial")
            real_killpg(pgid, selected_signal)

        try:
            monkeypatch.setattr(module.os, "killpg", deny_group_signals)
            with pytest.raises(PermissionError, match="injected persistent group denial"):
                terminate(child)
            assert not child.drained
            assert child.process.poll() is None
            assert os.getpgid(child.actor_pid) == child.pgid
            assert os.getsid(child.actor_pid) == child.sid
        finally:
            monkeypatch.undo()
