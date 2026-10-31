"""Owned crash-board process helpers shared by the runtime cleanup-ordering tests."""

from __future__ import annotations

import os
import subprocess
from builtins import BaseExceptionGroup
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pytest
import tests as root_tests
from scripts.isolated_test_runtime import (
    IsolatedRuntime,
    _drain_owned_process_group,
    _release_owned_launch,
    _start_owned_launch,
)

if TYPE_CHECKING:
    import contextlib

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Root acceptance and DB Gateway fixtures both live in packages named ``tests``.
# Extend the already-imported root package only for this process so the historical
# v3 builders remain the single fixture source instead of being copied here.
_GATEWAY_TESTS = _REPO_ROOT / "services" / "db-gateway" / "tests"
assert hasattr(root_tests, "__path__")
root_tests.__path__.append(str(_GATEWAY_TESTS))

pytestmark = pytest.mark.integration

_TASK_BOARD_ROOT = _REPO_ROOT / "services" / "task-board"
_PHASE_SERVER = Path(__file__).with_name("mp05_legacy_refund_phase_server.py")
_SERVICES = ("db-gateway", "identity", "central-bank", "task-board")
_GATE_TIMEOUT_SECONDS = 30.0
_RECOVERY_TIMEOUT_SECONDS = 20.0
_Phase = Literal["refund-intent", "refund-commit"]
_IMMUTABLE_OBSERVATION_FIELDS = (
    "observation_id",
    "observation_contract_version",
    "escrow_id",
    "task_id",
    "poster_id",
    "amount",
    "observed_fact_json",
    "observed_fact_sha256",
    "caller_kid",
    "refund_operation_id",
    "refund_request_json",
    "refund_request_sha256",
)


@dataclass(slots=True)
class _OwnedBoard:
    process: subprocess.Popen[bytes]
    pgid: int
    sid: int
    actor_pid: int | None = None
    drained: bool = False


@dataclass(slots=True)
class _LaunchGate:
    descriptor: int | None

    def release(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            raise RuntimeError("Launch gate is no longer owned")
        try:
            _release_owned_launch(descriptor)
        finally:
            # _release_owned_launch closes in its own finally block, including
            # failed authorization writes. Never retain a second close owner.
            self.descriptor = None

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        os.close(descriptor)


def _start_crash_board(
    runtime: IsolatedRuntime,
    phase: _Phase,
    gate_path: Path,
    log_path: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedBoard:
    reservation = runtime._reservations.pop("task-board")
    ownership.callback(_capture_cleanup, cleanup_failures, reservation.release)
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        str(_PHASE_SERVER),
        "--phase",
        phase,
        "--fd",
        str(reservation.socket.fileno()),
        "--gate",
        str(gate_path),
    ]
    with log_path.open("ab") as log:
        process, pgid, sid, launch_descriptor = _start_owned_launch(
            command,
            cwd=str(_TASK_BOARD_ROOT),
            env=runtime.service_environment("task-board"),
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=(reservation.socket.fileno(),),
        )
        child = _OwnedBoard(process=process, pgid=pgid, sid=sid)
        ownership.callback(_capture_cleanup, cleanup_failures, _terminate_board, child)
        gate = _LaunchGate(launch_descriptor)
        ownership.callback(_capture_cleanup, cleanup_failures, gate.close)
        reservation.release()
        gate.release()
    return child


def _start_sentinel(
    root: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedBoard:
    process, pgid, sid, descriptor = _start_owned_launch(
        ["/bin/sleep", "60"],
        cwd=str(root),
        env=dict(os.environ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(),
    )
    child = _OwnedBoard(process=process, pgid=pgid, sid=sid, actor_pid=process.pid)
    ownership.callback(_capture_cleanup, cleanup_failures, _terminate_board, child)
    gate = _LaunchGate(descriptor)
    ownership.callback(_capture_cleanup, cleanup_failures, gate.close)
    gate.release()
    return child


def _terminate_board(board: _OwnedBoard) -> None:
    if board.drained:
        return
    _drain_owned_process_group(
        board.process,
        pgid=board.pgid,
        sid=board.sid,
    )
    board.drained = True


def _alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _capture_cleanup(failures: list[BaseException], callback: Any, *arguments: object) -> None:
    try:
        callback(*arguments)
    except BaseException as exc:
        failures.append(exc)


def _raise_failures(primary: BaseException | None, cleanup_failures: list[BaseException]) -> None:
    failures = ([] if primary is None else [primary]) + cleanup_failures
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup("legacy-refund process test failed", failures)
