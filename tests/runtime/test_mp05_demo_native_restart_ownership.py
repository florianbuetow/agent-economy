"""Failure injection for the MP-05 native restart subprocess ownership window."""

from __future__ import annotations

import contextlib
import os
import subprocess
from builtins import BaseExceptionGroup
from pathlib import Path
from typing import Any

import pytest
from tests.runtime import test_mp05_demo_native_restart as proof

pytestmark = pytest.mark.integration


class _CloseFailureLog:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_args: object) -> None:
        raise OSError("injected native log close failure")


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
        proof._terminate_owned(proof._OwnedChild(process=process, pgid=pgid, sid=sid))


def _capturing_start(
    captured: list[tuple[subprocess.Popen[bytes], int, int, int]],
) -> Any:
    real_start = proof._start_owned_launch

    def start(*_args: Any, **_kwargs: Any) -> tuple[subprocess.Popen[bytes], int, int, int]:
        launched = real_start(
            ["/bin/sleep", "60"],
            cwd="/private/tmp",
            env=dict(os.environ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(),
        )
        captured.append(launched)
        return launched

    return start


def test_launch_release_failure_preserves_primary_and_reaps_owned_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[subprocess.Popen[bytes], int, int, int]] = []
    cleanup_failures: list[BaseException] = []
    real_release = proof._release_owned_launch

    def fail_release(descriptor: int) -> None:
        real_release(descriptor)
        raise BrokenPipeError("injected native gate release failure")

    monkeypatch.setattr(proof, "_start_owned_launch", _capturing_start(captured))
    monkeypatch.setattr(proof, "_release_owned_launch", fail_release)
    ownership = contextlib.ExitStack()
    try:
        with pytest.raises(BrokenPipeError, match="injected native gate release failure"):
            proof._launch(
                ["ignored"],
                cwd=tmp_path,
                env={},
                log_path=tmp_path / "release.log",
                ownership=ownership,
                cleanup_failures=cleanup_failures,
            )
    finally:
        ownership.close()
        _force_cleanup(captured[0] if captured else None)

    assert cleanup_failures == []
    assert len(captured) == 1
    process, pgid, _sid, descriptor = captured[0]
    assert process.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_launch_log_close_failure_preserves_primary_and_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[subprocess.Popen[bytes], int, int, int]] = []
    cleanup_failures: list[BaseException] = []
    monkeypatch.setattr(proof, "_start_owned_launch", _capturing_start(captured))
    ownership = contextlib.ExitStack()
    primary: BaseException | None = None
    try:
        try:
            proof._launch(
                ["ignored"],
                cwd=Path("/private/tmp"),
                env={},
                log_path=_CloseFailurePath(),  # type: ignore[arg-type]
                ownership=ownership,
                cleanup_failures=cleanup_failures,
            )
        except BaseException as exc:
            primary = exc
        ownership.callback(
            proof._capture_cleanup,
            cleanup_failures,
            lambda: (_ for _ in ()).throw(RuntimeError("injected native cleanup failure")),
        )
    finally:
        ownership.close()
        _force_cleanup(captured[0] if captured else None)

    assert isinstance(primary, OSError)
    assert str(primary) == "injected native log close failure"
    assert len(cleanup_failures) == 1
    assert str(cleanup_failures[0]) == "injected native cleanup failure"
    assert len(captured) == 1
    process, pgid, _sid, descriptor = captured[0]
    assert process.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)
    with pytest.raises(OSError):
        os.fstat(descriptor)
    with pytest.raises(BaseExceptionGroup) as grouped:
        proof._raise_failures(primary, cleanup_failures, Path("native-proof.json"))
    assert grouped.value.exceptions == (primary, cleanup_failures[0])
