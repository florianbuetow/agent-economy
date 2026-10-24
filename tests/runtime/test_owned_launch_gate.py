"""T-187/T-188 acceptance coverage for race-free owned process launch."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
from pathlib import Path
from typing import BinaryIO, cast

import httpx
import pytest
import scripts.isolated_test_runtime as runtime_module
from scripts.isolated_test_runtime import (
    IsolatedRuntime,
    RuntimeErrorWithLog,
    UnprovenLaunchCleanupError,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _marker_command(marker_path: Path, exit_code: int) -> list[str]:
    return [
        "/bin/sh",
        "-c",
        f"printf executed > {shlex.quote(str(marker_path))}; exit {exit_code}",
    ]


def test_waited_direct_command_loses_process_group_identity() -> None:
    process = subprocess.Popen(["/usr/bin/true"], start_new_session=True)
    assert process.wait(timeout=1.0) == 0

    with pytest.raises(ProcessLookupError):
        runtime_module._owned_group_identity(process)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_fast_command_is_blocked_until_live_identity_and_manifest_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    marker_path = tmp_path / f"fast-command-{exit_code}"
    real_identity = runtime_module._owned_group_identity
    real_write_manifest = runtime._write_manifest
    identity_captured = False
    registered_while_blocked = False

    def _inspect_live_identity(process: subprocess.Popen[bytes]) -> tuple[int, int]:
        nonlocal identity_captured
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.05)
        assert not marker_path.exists()
        identity = real_identity(process)
        assert identity == (process.pid, process.pid)
        identity_captured = True
        return identity

    def _inspect_registration() -> None:
        nonlocal registered_while_blocked
        real_write_manifest()
        if runtime.active_command_record is None:
            return
        manifest = json.loads((runtime.run_dir / "runtime.json").read_text())
        active_command = runtime.active_command
        assert active_command is not None
        assert manifest["commands"][-1]["pid"] == active_command.pid
        assert not marker_path.exists()
        registered_while_blocked = True

    monkeypatch.setattr(runtime_module, "_owned_group_identity", _inspect_live_identity)
    monkeypatch.setattr(runtime, "_write_manifest", _inspect_registration)

    assert runtime.run_command(_marker_command(marker_path, exit_code)) == exit_code

    assert identity_captured
    assert registered_while_blocked
    assert marker_path.read_text() == "executed"
    record = runtime.command_records[0]
    assert record["pid"] == record["pgid"] == record["sid"]
    assert record["leader_returncode"] == exit_code
    assert record["group_drained"]
    assert not record["running"]
    assert record["cleanup_error"] is None


def test_launcher_ignores_python_startup_hooks_before_gate_and_execs_direct_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    startup_marker = tmp_path / "sitecustomize-ran"
    target_marker = tmp_path / "direct-target-ran"
    python_path = tmp_path / "python-path"
    python_path.mkdir()
    (python_path / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(startup_marker)!r}).write_text('ran')\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(python_path))
    real_identity = runtime_module._owned_group_identity

    def _inspect_gate(process: subprocess.Popen[bytes]) -> tuple[int, int]:
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.05)
        assert not startup_marker.exists()
        assert not target_marker.exists()
        return real_identity(process)

    monkeypatch.setattr(runtime_module, "_owned_group_identity", _inspect_gate)

    assert runtime.run_command(_marker_command(target_marker, 0)) == 0
    assert target_marker.read_text() == "executed"
    assert not startup_marker.exists()


def test_wrong_identity_rejection_never_executes_requested_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    marker_path = tmp_path / "wrong-identity-command"
    rejected_process: subprocess.Popen[bytes] | None = None

    def _reject_identity(process: subprocess.Popen[bytes]) -> tuple[int, int]:
        nonlocal rejected_process
        rejected_process = process
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.05)
        raise RuntimeError("injected wrong private session")

    monkeypatch.setattr(runtime_module, "_owned_group_identity", _reject_identity)

    with pytest.raises(RuntimeError, match="injected wrong private session"):
        runtime.run_command(_marker_command(marker_path, 0))

    assert not marker_path.exists()
    assert runtime.active_command is None
    assert runtime.command_records == []
    assert rejected_process is not None
    assert rejected_process.returncode == 125


def test_gate_write_failure_has_one_close_owner_and_preserves_reused_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    marker_path = tmp_path / "release-failure-command"
    real_close = os.close
    gate_descriptor: int | None = None
    close_count = 0
    reused_descriptors: tuple[int, int] | None = None

    def _fail_gate_write(descriptor: int, payload: bytes) -> int:
        nonlocal gate_descriptor
        assert payload == runtime_module.OWNED_LAUNCH_AUTHORIZATION
        gate_descriptor = descriptor
        raise BrokenPipeError("injected launch gate write failure")

    def _track_gate_close(descriptor: int) -> None:
        nonlocal close_count, reused_descriptors
        if gate_descriptor is None or descriptor != gate_descriptor:
            real_close(descriptor)
            return
        close_count += 1
        real_close(descriptor)
        if close_count == 1:
            reused_descriptors = os.pipe()
            assert descriptor in reused_descriptors

    monkeypatch.setattr(runtime_module.os, "write", _fail_gate_write)
    monkeypatch.setattr(runtime_module.os, "close", _track_gate_close)

    with pytest.raises(BrokenPipeError, match="injected launch gate write failure"):
        runtime.run_command(_marker_command(marker_path, 0))

    assert not marker_path.exists()
    assert close_count == 1
    assert reused_descriptors is not None
    assert gate_descriptor is not None
    os.fstat(gate_descriptor)
    assert runtime.active_command is None
    record = runtime.command_records[0]
    assert record["group_drained"]
    assert not record["running"]
    assert record["cleanup_error"] is None
    for descriptor in reused_descriptors:
        try:
            real_close(descriptor)
        except OSError:
            pass


def test_preownership_gate_close_failure_preserves_identity_error_and_reaps_exact_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    pipe_descriptors: tuple[int, int] | None = None
    rejected_process: subprocess.Popen[bytes] | None = None
    real_pipe = os.pipe
    real_close = os.close
    close_failed = False
    identity_rejected = False

    def _capture_pipe() -> tuple[int, int]:
        nonlocal pipe_descriptors
        descriptors = real_pipe()
        if pipe_descriptors is None:
            pipe_descriptors = descriptors
        return descriptors

    def _close_then_fail(descriptor: int) -> None:
        nonlocal close_failed
        assert pipe_descriptors is not None
        real_close(descriptor)
        if identity_rejected and descriptor == pipe_descriptors[1] and not close_failed:
            close_failed = True
            raise OSError("injected gate close failure")

    def _reject_identity(process: subprocess.Popen[bytes]) -> tuple[int, int]:
        nonlocal identity_rejected, rejected_process
        rejected_process = process
        identity_rejected = True
        raise RuntimeError("injected identity rejection")

    def _forbid_group_signal(_pgid: int, _signal_number: int) -> None:
        pytest.fail("pre-ownership cleanup must not signal a process group")

    monkeypatch.setattr(runtime_module.os, "pipe", _capture_pipe)
    monkeypatch.setattr(runtime_module.os, "close", _close_then_fail)
    monkeypatch.setattr(runtime_module.os, "killpg", _forbid_group_signal)
    monkeypatch.setattr(runtime_module, "_owned_group_identity", _reject_identity)

    with pytest.raises(RuntimeError, match="injected identity rejection") as exc_info:
        runtime.run_command(_marker_command(tmp_path / "must-not-run", 0))

    assert close_failed
    assert rejected_process is not None
    assert rejected_process.returncode == 125
    assert any(
        "injected gate close failure" in note for note in exc_info.value.__notes__
    )


def test_parent_read_close_failure_reaps_exact_child_without_group_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    marker_path = tmp_path / "must-not-run"
    pipe_descriptors: tuple[int, int] | None = None
    launched_process: subprocess.Popen[bytes] | None = None
    popen_returned = False
    real_pipe = os.pipe
    real_close = os.close
    real_popen = subprocess.Popen

    def _capture_gate_pipe() -> tuple[int, int]:
        nonlocal pipe_descriptors
        descriptors = real_pipe()
        if pipe_descriptors is None:
            pipe_descriptors = descriptors
        return descriptors

    def _capture_process(
        args: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        stdout: BinaryIO | int | None,
        stderr: BinaryIO | int | None,
        start_new_session: bool,
        pass_fds: tuple[int, ...],
    ) -> subprocess.Popen[bytes]:
        nonlocal launched_process, popen_returned
        process = cast(
            "subprocess.Popen[bytes]",
            real_popen(
                args,
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=start_new_session,
                pass_fds=pass_fds,
            ),
        )
        launched_process = process
        popen_returned = True
        return process

    def _close_read_then_fail(descriptor: int) -> None:
        assert pipe_descriptors is not None
        real_close(descriptor)
        if popen_returned and descriptor == pipe_descriptors[0]:
            raise OSError("injected parent read close failure")

    def _forbid_group_signal(_pgid: int, _signal_number: int) -> None:
        pytest.fail("pre-ownership cleanup must not signal a process group")

    monkeypatch.setattr(runtime_module.os, "pipe", _capture_gate_pipe)
    monkeypatch.setattr(runtime_module.os, "close", _close_read_then_fail)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", _capture_process)
    monkeypatch.setattr(runtime_module.os, "killpg", _forbid_group_signal)

    try:
        with pytest.raises(OSError, match="injected parent read close failure"):
            runtime.run_command(_marker_command(marker_path, 0))

        assert launched_process is not None
        assert launched_process.returncode == 125
        assert not marker_path.exists()
    finally:
        if pipe_descriptors is not None:
            try:
                real_close(pipe_descriptors[1])
            except OSError:
                pass
        if launched_process is not None and launched_process.poll() is None:
            launched_process.terminate()
            launched_process.wait(timeout=5.0)


def test_preownership_wait_timeouts_terminate_kill_and_reap_only_exact_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    rejected_process: subprocess.Popen[bytes] | None = None
    terminate_calls = 0
    kill_calls = 0

    def _reject_after_instrumenting(
        process: subprocess.Popen[bytes],
    ) -> tuple[int, int]:
        nonlocal rejected_process
        rejected_process = process
        real_wait = process.wait
        real_terminate = process.terminate
        real_kill = process.kill
        wait_calls = 0

        def _timeout_once(timeout: float) -> int:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls <= 2:
                raise subprocess.TimeoutExpired(process.args, timeout)
            return real_wait(timeout=timeout)

        def _terminate() -> None:
            nonlocal terminate_calls
            terminate_calls += 1
            real_terminate()

        def _kill() -> None:
            nonlocal kill_calls
            kill_calls += 1
            real_kill()

        monkeypatch.setattr(process, "wait", _timeout_once)
        monkeypatch.setattr(process, "terminate", _terminate)
        monkeypatch.setattr(process, "kill", _kill)
        raise RuntimeError("injected identity rejection")

    def _forbid_group_signal(_pgid: int, _signal_number: int) -> None:
        pytest.fail("pre-ownership cleanup must not signal a process group")

    monkeypatch.setattr(runtime_module.os, "killpg", _forbid_group_signal)
    monkeypatch.setattr(
        runtime_module,
        "_owned_group_identity",
        _reject_after_instrumenting,
    )

    with pytest.raises(RuntimeError, match="injected identity rejection"):
        runtime.run_command(_marker_command(tmp_path / "must-not-run", 0))

    assert terminate_calls == 1
    assert kill_calls == 1
    assert rejected_process is not None
    assert rejected_process.returncode in {125, -signal.SIGTERM}


def test_final_preownership_wait_failure_retains_exact_handle_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    primary_error = RuntimeError("injected identity rejection")
    rejected_process: subprocess.Popen[bytes] | None = None

    def _reject_after_instrumenting(
        process: subprocess.Popen[bytes],
    ) -> tuple[int, int]:
        nonlocal rejected_process
        rejected_process = process
        wait_calls = 0

        def _fail_waits(timeout: float) -> int:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls <= 2:
                raise subprocess.TimeoutExpired(process.args, timeout)
            raise RuntimeError("injected final exact wait failure")

        monkeypatch.setattr(process, "wait", _fail_waits)
        raise primary_error

    def _forbid_group_signal(_pgid: int, _signal_number: int) -> None:
        pytest.fail("pre-ownership cleanup must not signal a process group")

    monkeypatch.setattr(runtime_module.os, "killpg", _forbid_group_signal)
    monkeypatch.setattr(
        runtime_module,
        "_owned_group_identity",
        _reject_after_instrumenting,
    )

    with pytest.raises(UnprovenLaunchCleanupError) as exc_info:
        runtime.run_command(_marker_command(tmp_path / "must-not-run", 0))

    try:
        assert exc_info.value.process is rejected_process
        assert exc_info.value.primary_error is primary_error
        assert "injected final exact wait failure" in str(exc_info.value)
        assert rejected_process is not None
    finally:
        if rejected_process is not None:
            subprocess.Popen.wait(rejected_process, timeout=5.0)


def test_immediately_failing_service_reports_its_owned_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    monkeypatch.setattr(runtime, "_ensure_keys_provisioned", lambda: None)
    monkeypatch.setitem(
        runtime_module.SERVICE_MODULES,
        "db-gateway",
        "missing_owned_launch_test_module:create_app",
    )

    try:
        with pytest.raises(RuntimeErrorWithLog) as exc_info:
            runtime.start_service("db-gateway")

        record = runtime.process_records["db-gateway"]
        assert record["pid"] == record["pgid"] == record["sid"]
        assert str(runtime.log_paths["db-gateway"]) in str(exc_info.value)
        assert runtime.log_paths["db-gateway"].read_bytes()
    finally:
        runtime.stop()


def test_service_launch_gate_preserves_inherited_listener_descriptor(
    tmp_path: Path,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    try:
        runtime.start_service("db-gateway")
        response = httpx.get(f"{runtime.service_url('db-gateway')}/health", timeout=2.0)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    finally:
        runtime.stop()
