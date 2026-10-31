"""Failure-injection coverage for MP-05 crash-helper ownership windows."""

from __future__ import annotations

import contextlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.runtime import owned_board_process as proof

pytestmark = pytest.mark.integration


@dataclass
class _FakeSocket:
    descriptor: int = 71_002

    def fileno(self) -> int:
        return self.descriptor


@dataclass
class _FakeReservation:
    socket: _FakeSocket
    released: bool = False

    def release(self) -> None:
        self.released = True


class _FakeRuntime:
    def __init__(self, reservation: _FakeReservation) -> None:
        self._reservations = {"task-board": reservation}

    @staticmethod
    def service_environment(_service: str) -> dict[str, str]:
        return {}


class _CloseFailureLog:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_args: object) -> None:
        raise OSError("injected log close failure")


class _CloseFailurePath:
    @staticmethod
    def open(_mode: str) -> _CloseFailureLog:
        return _CloseFailureLog()


def _force_cleanup(
    captured: tuple[subprocess.Popen[bytes], int, int, int] | None,
) -> None:
    if captured is None:
        return
    process, pgid, sid, _descriptor = captured
    if process.poll() is None:
        proof._terminate_board(proof._OwnedBoard(process=process, pgid=pgid, sid=sid))


def test_sentinel_gate_release_failure_has_child_and_descriptor_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_failures: list[BaseException] = []
    captured: tuple[subprocess.Popen[bytes], int, int, int] | None = None
    real_start = proof._start_owned_launch
    real_release = proof._release_owned_launch

    def start(*args: Any, **kwargs: Any) -> tuple[subprocess.Popen[bytes], int, int, int]:
        nonlocal captured
        captured = real_start(*args, **kwargs)
        return captured

    def fail_release(descriptor: int) -> None:
        real_release(descriptor)
        raise BrokenPipeError("injected launch gate release failure")

    monkeypatch.setattr(proof, "_start_owned_launch", start)
    monkeypatch.setattr(proof, "_release_owned_launch", fail_release)

    with (
        contextlib.ExitStack() as ownership,
        pytest.raises(BrokenPipeError, match="injected launch gate release failure"),
    ):
        proof._start_sentinel(
            tmp_path,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )

    try:
        assert captured is not None
        process, pgid, _sid, gate_descriptor = captured
        assert process.poll() is not None
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)
        assert cleanup_failures == []
        with pytest.raises(OSError):
            os.fstat(gate_descriptor)
    finally:
        _force_cleanup(captured)


def test_board_log_close_failure_reaps_child_closes_gate_and_preserves_cleanup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation = _FakeReservation(_FakeSocket())
    runtime = _FakeRuntime(reservation)
    cleanup_failures: list[BaseException] = []
    captured: tuple[subprocess.Popen[bytes], int, int, int] | None = None
    real_start = proof._start_owned_launch

    def start(*_args: Any, **_kwargs: Any) -> tuple[subprocess.Popen[bytes], int, int, int]:
        nonlocal captured
        captured = real_start(
            ["/bin/sleep", "60"],
            cwd="/private/tmp",
            env=dict(os.environ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(),
        )
        return captured

    def fail_cleanup() -> None:
        raise RuntimeError("injected child cleanup failure")

    monkeypatch.setattr(proof, "_start_owned_launch", start)

    primary: BaseException | None = None
    ownership = contextlib.ExitStack()
    try:
        try:
            proof._start_crash_board(
                runtime,  # type: ignore[arg-type]
                "refund-intent",
                Path("gate.json"),
                _CloseFailurePath(),  # type: ignore[arg-type]
                ownership=ownership,
                cleanup_failures=cleanup_failures,
            )
        except BaseException as exc:
            primary = exc
        ownership.callback(proof._capture_cleanup, cleanup_failures, fail_cleanup)
    finally:
        ownership.close()

    try:
        assert isinstance(primary, OSError)
        assert str(primary) == "injected log close failure"
        assert reservation.released
        assert captured is not None
        process, pgid, _sid, gate_descriptor = captured
        assert process.poll() is not None
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)
        assert len(cleanup_failures) == 1
        assert str(cleanup_failures[0]) == "injected child cleanup failure"
        with pytest.raises(OSError):
            os.fstat(gate_descriptor)

        with pytest.raises(BaseExceptionGroup) as grouped:
            proof._raise_failures(primary, cleanup_failures)
        assert grouped.value.exceptions == (primary, cleanup_failures[0])
    finally:
        _force_cleanup(captured)
