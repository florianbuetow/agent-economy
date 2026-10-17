"""Acceptance tests for the MP-01 disposable live-test runtime."""

from __future__ import annotations

import base64
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import TypedDict

import httpx
import pytest
import scripts.isolated_test_runtime as runtime_module
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts.isolated_test_runtime import IsolatedRuntime, RuntimeErrorWithLog

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


class _ManifestProcessRecord(TypedDict):
    pid: int
    pgid: int
    sid: int
    running: bool
    leader_returncode: int | None
    group_drained: bool
    cleanup_error: str | None


class _ManifestCommandRecord(_ManifestProcessRecord):
    command: list[str]


class _RuntimeManifest(TypedDict):
    processes: dict[str, _ManifestProcessRecord]
    commands: list[_ManifestCommandRecord]


def _string_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        pytest.fail(f"{label} must be a string-keyed object")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _required_int(mapping: dict[str, object], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        pytest.fail(f"{key} must be an integer")
    return value


def _required_bool(mapping: dict[str, object], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        pytest.fail(f"{key} must be a boolean")
    return value


def _process_record(value: object, label: str) -> _ManifestProcessRecord:
    mapping = _string_mapping(value, label)
    leader_returncode = mapping.get("leader_returncode")
    if leader_returncode is not None and (
        not isinstance(leader_returncode, int) or isinstance(leader_returncode, bool)
    ):
        pytest.fail(f"{label}.leader_returncode must be an integer or null")
    cleanup_error = mapping.get("cleanup_error")
    if cleanup_error is not None and not isinstance(cleanup_error, str):
        pytest.fail(f"{label}.cleanup_error must be a string or null")
    return {
        "pid": _required_int(mapping, "pid"),
        "pgid": _required_int(mapping, "pgid"),
        "sid": _required_int(mapping, "sid"),
        "running": _required_bool(mapping, "running"),
        "leader_returncode": leader_returncode,
        "group_drained": _required_bool(mapping, "group_drained"),
        "cleanup_error": cleanup_error,
    }


def _command_record(value: object, label: str) -> _ManifestCommandRecord:
    mapping = _string_mapping(value, label)
    command = mapping.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        pytest.fail(f"{label}.command must be a string array")
    process_record = _process_record(mapping, label)
    return {
        **process_record,
        "command": [item for item in command if isinstance(item, str)],
    }


def _read_runtime_manifest(path: Path) -> _RuntimeManifest:
    raw: object = json.loads(path.read_text())
    mapping = _string_mapping(raw, "runtime manifest")
    raw_processes = _string_mapping(mapping.get("processes"), "runtime manifest.processes")
    raw_commands = mapping.get("commands")
    if not isinstance(raw_commands, list):
        pytest.fail("runtime manifest.commands must be an array")
    return {
        "processes": {
            service: _process_record(record, f"runtime manifest.processes.{service}")
            for service, record in raw_processes.items()
        },
        "commands": [
            _command_record(record, f"runtime manifest.commands[{index}]")
            for index, record in enumerate(raw_commands)
        ],
    }


def _wait_for_sentinel(port: int) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    pytest.fail(f"sentinel did not bind port {port}")


def _pid_is_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _cleanup_owned_group(pgid: int, child_pid: int) -> None:
    if _pid_is_gone(child_pid):
        return
    try:
        child_pgid = os.getpgid(child_pid)
    except ProcessLookupError:
        return
    if child_pgid == pgid:
        os.killpg(pgid, 9)


def _sentinel(tmp_path: Path) -> tuple[subprocess.Popen[bytes], int]:
    port_file = tmp_path / "sentinel-port"
    code = (
        "import pathlib,socket,time;"
        "s=socket.socket();s.bind(('127.0.0.1',0));s.listen();"
        f"pathlib.Path({str(port_file)!r}).write_text(str(s.getsockname()[1]));"
        "time.sleep(120)"
    )
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "--directory",
            str(REPO_ROOT / "services" / "db-gateway"),
            "python",
            "-c",
            code,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10.0
    while not port_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert port_file.exists(), "sentinel did not publish its port"
    port = int(port_file.read_text())
    _wait_for_sentinel(port)
    return process, port


def _register(identity_url: str, name: str) -> str:
    public_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    response = httpx.post(
        f"{identity_url}/agents/register",
        json={"name": name, "public_key": f"ed25519:{base64.b64encode(public_key).decode()}"},
        timeout=5.0,
    )
    response.raise_for_status()
    return str(response.json()["agent_id"])


def test_two_minimal_stacks_are_isolated_and_preserve_unrelated_listener(
    tmp_path: Path,
) -> None:
    sentinel, sentinel_port = _sentinel(tmp_path)
    first = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway", "identity"),
    )
    second = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway", "identity"),
    )
    try:
        first.start()
        second.start()

        assert first.ports != second.ports
        assert first.run_dir != second.run_dir
        assert first.agent_config_path != second.agent_config_path

        first_id = _register(first.service_url("identity"), "first-runtime-agent")
        second_id = _register(second.service_url("identity"), "second-runtime-agent")
        assert first_id != second_id
        assert (
            httpx.get(
                f"{first.service_url('identity')}/agents/{second_id}", timeout=2.0
            ).status_code
            == 404
        )
        assert (
            httpx.get(
                f"{second.service_url('identity')}/agents/{first_id}", timeout=2.0
            ).status_code
            == 404
        )

        first.stop()
        assert httpx.get(f"{second.service_url('identity')}/health", timeout=2.0).is_success
        assert sentinel.poll() is None
        _wait_for_sentinel(sentinel_port)

        raw_manifest: object = json.loads((first.run_dir / "runtime.json").read_text())
        manifest_mapping = _string_mapping(raw_manifest, "runtime manifest")
        assert manifest_mapping["run_id"] == first.run_id
        config_paths = _string_mapping(
            manifest_mapping.get("config_paths"), "runtime manifest.config_paths"
        )
        assert all(
            isinstance(path, str) and Path(path).is_absolute() for path in config_paths.values()
        )
        manifest = _read_runtime_manifest(first.run_dir / "runtime.json")
        assert manifest["processes"]
        assert all(not record["running"] for record in manifest["processes"].values())
        assert all(record["group_drained"] for record in manifest["processes"].values())
        assert all(
            record["leader_returncode"] is not None for record in manifest["processes"].values()
        )
        assert all(record["cleanup_error"] is None for record in manifest["processes"].values())
    finally:
        first.stop()
        second.stop()
        sentinel.terminate()
        sentinel.wait(timeout=10)


def test_startup_failure_is_nonzero_and_reports_the_owned_log(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    runtime._ensure_keys_provisioned()
    runtime.config_paths["db-gateway"].write_text("invalid: true\n")

    with pytest.raises(RuntimeErrorWithLog) as exc_info:
        runtime.start()

    assert "db-gateway" in str(exc_info.value)
    assert str(runtime.log_paths["db-gateway"]) in str(exc_info.value)
    assert runtime.processes == {}


def test_phase_command_receives_generated_runtime_environment(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway", "identity"),
    )
    output_path = tmp_path / "command-env.json"
    code = (
        "import json,os,pathlib;"
        "data={"
        "'agent_config':os.environ['AGENT_CONFIG_PATH'],"
        "'identity_url':os.environ['IDENTITY_URL'],"
        "'runtime_dir':os.environ['ISOLATED_RUNTIME_DIR']};"
        f"pathlib.Path({str(output_path)!r}).write_text(json.dumps(data))"
    )
    try:
        runtime.start()
        result = runtime.run_command(
            [
                "uv",
                "run",
                "--directory",
                str(REPO_ROOT / "services" / "db-gateway"),
                "python",
                "-c",
                code,
            ]
        )
    finally:
        runtime.stop()

    assert result == 0
    captured = json.loads(output_path.read_text())
    assert captured["agent_config"] == str(runtime.agent_config_path)
    assert captured["identity_url"] == runtime.service_url("identity")
    assert captured["runtime_dir"] == str(runtime.run_dir)
    assert os.path.commonpath([captured["runtime_dir"], str(tmp_path)]) == str(tmp_path)


def test_generated_mock_court_import_uses_no_external_model_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "ambient-value")
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("identity", "court"),
    )
    marker_path = tmp_path / "external-model-metadata-url"
    instrumentation_dir = tmp_path / "instrumentation"
    instrumentation_dir.mkdir()
    (instrumentation_dir / "sitecustomize.py").write_text(
        "import pathlib\n"
        "import httpx\n"
        f"marker = pathlib.Path({str(marker_path)!r})\n"
        "original_get = httpx.get\n"
        "def guarded_get(url, *args, **kwargs):\n"
        "    if str(url).startswith(('http://', 'https://')):\n"
        "        marker.write_text(str(url))\n"
        "        raise httpx.ConnectError('external metadata blocked by test')\n"
        "    return original_get(url, *args, **kwargs)\n"
        "httpx.get = guarded_get\n"
    )
    try:
        phase_environment_keys = set(runtime.environment())
        identity_environment_keys = set(runtime.service_environment("identity"))
        env = runtime.service_environment("court")
    finally:
        runtime.stop()
    assert "LITELLM_LOCAL_MODEL_COST_MAP" not in phase_environment_keys
    assert "LITELLM_LOCAL_MODEL_COST_MAP" not in identity_environment_keys
    assert env["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    env["PYTHONPATH"] = os.pathsep.join(
        (str(instrumentation_dir), str(REPO_ROOT / "services" / "court" / "src"))
    )

    completed = subprocess.run(
        ["uv", "run", "python", "-c", "import court_service.app"],
        cwd=REPO_ROOT / "services" / "court",
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )

    assert completed.returncode == 0, completed.stderr
    assert not marker_path.exists(), (
        f"mock Court import attempted external metadata: {marker_path.read_text()}"
    )


@pytest.mark.parametrize("exit_code", [0, 7])
def test_phase_command_drains_background_descendants_after_leader_exit(
    tmp_path: Path,
    exit_code: int,
) -> None:
    sentinel, sentinel_port = _sentinel(tmp_path)
    child_pid_path = tmp_path / f"background-child-{exit_code}.pid"
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    child_code = (
        "import os,pathlib,signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(os.getpid()));"
        "time.sleep(120)"
    )
    parent_code = (
        "import subprocess,sys,time,pathlib;"
        f"ready=pathlib.Path({str(child_pid_path)!r});"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        "deadline=time.monotonic()+10;"
        "\nwhile not ready.exists() and time.monotonic()<deadline: time.sleep(0.01);"
        "\nif not ready.exists(): raise RuntimeError('child readiness timeout');"
        f"\nsys.exit({exit_code})"
    )
    try:
        result = runtime.run_command(
            [
                "uv",
                "run",
                "--directory",
                str(REPO_ROOT / "services" / "db-gateway"),
                "python",
                "-c",
                parent_code,
            ]
        )
        child_pid = int(child_pid_path.read_text())

        assert result == exit_code
        command_record = runtime.command_records[0]
        assert command_record["pid"] == command_record["pgid"] == command_record["sid"]
        assert command_record["leader_returncode"] == exit_code
        assert command_record["group_drained"]
        assert command_record["cleanup_error"] is None
        assert _pid_is_gone(child_pid)
        assert sentinel.poll() is None
        _wait_for_sentinel(sentinel_port)
    finally:
        if child_pid_path.exists() and runtime.command_records:
            _cleanup_owned_group(
                int(runtime.command_records[0]["pid"]),
                int(child_pid_path.read_text()),
            )
        runtime.stop()
        sentinel.terminate()
        sentinel.wait(timeout=10)


def test_supervisor_sigterm_stops_only_its_owned_process_groups(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "--directory",
            str(REPO_ROOT / "services" / "db-gateway"),
            str(REPO_ROOT / "scripts" / "isolated_test_runtime.py"),
            "--artifacts-root",
            str(tmp_path),
            "--services",
            "db-gateway,identity",
            "--",
            "/bin/sleep",
            "120",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        first_line = process.stdout.readline().decode().strip()
        assert first_line.startswith("Isolated runtime: ")
        run_dir = Path(first_line.removeprefix("Isolated runtime: "))
        manifest_path = run_dir / "runtime.json"

        deadline = time.monotonic() + 30.0
        manifest: _RuntimeManifest = {"processes": {}, "commands": []}
        while time.monotonic() < deadline:
            manifest = _read_runtime_manifest(manifest_path)
            records = manifest["processes"]
            commands = manifest["commands"]
            if isinstance(records, dict) and len(records) == 2 and len(commands) == 1:
                break
            time.sleep(0.1)
        else:
            pytest.fail("supervisor did not start both owned services")

        process.terminate()
        assert process.wait(timeout=20.0) != 0
        assert process.stderr is not None
        supervisor_stderr = process.stderr.read().decode()
        final_manifest = _read_runtime_manifest(manifest_path)
        assert len(final_manifest["processes"]) == 2
        assert all(not record["running"] for record in final_manifest["processes"].values())
        assert all(
            record["leader_returncode"] is not None
            for record in final_manifest["processes"].values()
        )
        assert all(record["group_drained"] for record in final_manifest["processes"].values())
        assert len(final_manifest["commands"]) == 1
        command_record = final_manifest["commands"][0]
        assert not command_record["running"]
        assert command_record["leader_returncode"] is not None, supervisor_stderr
        assert command_record["group_drained"]
        assert command_record["cleanup_error"] is None
        assert command_record["pid"] == command_record["pgid"] == command_record["sid"]
        sleep_pid = int(command_record["pid"])
        assert _pid_is_gone(sleep_pid)
        with pytest.raises(ProcessLookupError):
            os.getpgid(sleep_pid)
        assert supervisor_stderr == ""
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20.0)


def test_shutdown_requested_before_command_registration_is_drained(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    runtime.request_shutdown(signal.SIGTERM)
    try:
        result = runtime.run_command(["/bin/sleep", "120"])
        assert result == 128 + signal.SIGTERM
        assert len(runtime.command_records) == 1
        command_record = runtime.command_records[0]
        assert not command_record["running"]
        assert command_record["leader_returncode"] is not None
        assert command_record["group_drained"]
        assert command_record["cleanup_error"] is None
        assert command_record["pid"] == command_record["pgid"] == command_record["sid"]
        command_pid = int(command_record["pid"])
        assert _pid_is_gone(command_pid)
        with pytest.raises(ProcessLookupError):
            os.getpgid(command_pid)
    finally:
        runtime.stop()


def test_sigterm_during_command_drain_wins_over_captured_zero_exit(tmp_path: Path) -> None:
    sentinel, sentinel_port = _sentinel(tmp_path)
    child_pid_path = tmp_path / "signal-during-drain-child.pid"
    child_code = (
        "import os,pathlib,signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(os.getpid()));"
        "time.sleep(120)"
    )
    parent_code = (
        "import subprocess,sys,time,pathlib;"
        f"ready=pathlib.Path({str(child_pid_path)!r});"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        "deadline=time.monotonic()+10;"
        "\nwhile not ready.exists() and time.monotonic()<deadline: time.sleep(0.01);"
        "\nif not ready.exists(): raise RuntimeError('child readiness timeout');"
        "\nsys.exit(0)"
    )
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "--directory",
            str(REPO_ROOT / "services" / "db-gateway"),
            str(REPO_ROOT / "scripts" / "isolated_test_runtime.py"),
            "--artifacts-root",
            str(tmp_path),
            "--services",
            "db-gateway",
            "--",
            "uv",
            "run",
            "--directory",
            str(REPO_ROOT / "services" / "db-gateway"),
            "python",
            "-c",
            parent_code,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    manifest: _RuntimeManifest = {"processes": {}, "commands": []}
    try:
        assert process.stdout is not None
        first_line = process.stdout.readline().decode().strip()
        run_dir = Path(first_line.removeprefix("Isolated runtime: "))
        manifest_path = run_dir / "runtime.json"
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            manifest = _read_runtime_manifest(manifest_path)
            commands = manifest["commands"]
            if commands and child_pid_path.exists():
                leader_pid = int(commands[0]["pid"])
                if _pid_is_gone(leader_pid) and not _pid_is_gone(int(child_pid_path.read_text())):
                    break
            time.sleep(0.01)
        else:
            pytest.fail("command did not enter leader-reaped descendant-drain window")

        process.terminate()
        assert process.wait(timeout=20.0) == 128 + signal.SIGTERM
        assert process.stderr is not None
        assert process.stderr.read().decode() == ""
        final_manifest = _read_runtime_manifest(manifest_path)
        command_record = final_manifest["commands"][0]
        assert command_record["group_drained"]
        assert not command_record["running"]
        assert command_record["cleanup_error"] is None
        assert _pid_is_gone(int(child_pid_path.read_text()))
        assert all(record["group_drained"] for record in final_manifest["processes"].values())
        assert sentinel.poll() is None
        _wait_for_sentinel(sentinel_port)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20.0)
        if child_pid_path.exists() and manifest.get("commands"):
            _cleanup_owned_group(
                int(manifest["commands"][0]["pgid"]),
                int(child_pid_path.read_text()),
            )
        sentinel.terminate()
        sentinel.wait(timeout=10)


def test_command_drain_failure_preserves_handle_and_manifest_until_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    real_drain = runtime_module._drain_owned_process_group

    def _fail_drain(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("injected command drain denial")

    monkeypatch.setattr(runtime_module, "_drain_owned_process_group", _fail_drain)
    with pytest.raises(PermissionError, match="injected command drain denial"):
        runtime.run_command(["/usr/bin/true"])

    record = runtime.command_records[0]
    assert runtime.active_command is not None
    assert record["leader_returncode"] == 0
    assert not record["group_drained"]
    assert record["running"]
    assert "injected command drain denial" in str(record["cleanup_error"])
    failed_manifest = _read_runtime_manifest(runtime.run_dir / "runtime.json")
    assert failed_manifest["commands"][0]["leader_returncode"] == 0
    assert not failed_manifest["commands"][0]["group_drained"]
    assert failed_manifest["commands"][0]["running"]
    manifest_cleanup_error = failed_manifest["commands"][0]["cleanup_error"]
    assert manifest_cleanup_error is not None
    assert "injected command drain denial" in manifest_cleanup_error

    monkeypatch.setattr(runtime_module, "_drain_owned_process_group", real_drain)
    runtime.stop()
    assert runtime.active_command is None
    assert record["group_drained"]
    assert not record["running"]
    assert record["cleanup_error"] is None


def test_stop_attempts_other_services_and_preserves_failed_handle_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway", "identity"),
    )
    runtime.start()
    identity_pid = runtime.processes["identity"].pid
    gateway_pid = runtime.processes["db-gateway"].pid
    real_drain = runtime_module._drain_owned_process_group
    failed_once = False

    def _fail_identity_once(
        process: subprocess.Popen[bytes],
        *,
        pgid: int,
        sid: int,
    ) -> None:
        nonlocal failed_once
        if process.pid == identity_pid and not failed_once:
            failed_once = True
            raise PermissionError("injected service drain denial")
        real_drain(process, pgid=pgid, sid=sid)

    monkeypatch.setattr(runtime_module, "_drain_owned_process_group", _fail_identity_once)
    with pytest.raises(runtime_module.RuntimeCleanupError, match="identity"):
        runtime.stop()

    assert "identity" in runtime.processes
    assert "db-gateway" not in runtime.processes
    identity_record = runtime.process_records["identity"]
    gateway_record = runtime.process_records["db-gateway"]
    assert identity_record["running"]
    assert not identity_record["group_drained"]
    assert "injected service drain denial" in str(identity_record["cleanup_error"])
    assert not gateway_record["running"]
    assert gateway_record["group_drained"]
    assert gateway_record["cleanup_error"] is None
    assert _pid_is_gone(gateway_pid)
    failed_manifest = _read_runtime_manifest(runtime.run_dir / "runtime.json")
    assert failed_manifest["processes"]["identity"]["running"]
    assert not failed_manifest["processes"]["identity"]["group_drained"]
    assert failed_manifest["processes"]["db-gateway"]["group_drained"]
    assert not failed_manifest["processes"]["db-gateway"]["running"]

    monkeypatch.setattr(runtime_module, "_drain_owned_process_group", real_drain)
    runtime.stop()
    assert runtime.processes == {}
    assert identity_record["group_drained"]
    assert not identity_record["running"]
    assert identity_record["cleanup_error"] is None


@pytest.mark.parametrize(
    ("probe_returncode", "probe_stdout", "expected_absent"),
    [
        (0, "", True),
        (0, "123 1 123 123 Z\n", False),
        (1, "", False),
        (0, "malformed process row\n", False),
    ],
)
def test_signal_zero_permission_requires_successful_empty_exact_group_probe(
    monkeypatch: pytest.MonkeyPatch,
    probe_returncode: int,
    probe_stdout: str,
    expected_absent: bool,
) -> None:
    process = subprocess.Popen(["/usr/bin/true"], start_new_session=True)
    process.wait(timeout=5.0)
    pgid = process.pid

    def _deny_signal_zero(_pgid: int, _signal_number: int) -> None:
        raise PermissionError(1, "injected signal-zero denial")

    def _probe_group(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = probe_stdout.replace("123", str(pgid))
        return subprocess.CompletedProcess(
            args=["ps"],
            returncode=probe_returncode,
            stdout=stdout,
            stderr="injected ps failure" if probe_returncode else "",
        )

    monkeypatch.setattr(runtime_module.os, "killpg", _deny_signal_zero)
    monkeypatch.setattr(runtime_module.subprocess, "run", _probe_group)
    if expected_absent:
        assert runtime_module._wait_for_owned_group_exit(process, pgid=pgid, timeout=0.0)
    else:
        with pytest.raises(PermissionError, match="exact PGID"):
            runtime_module._wait_for_owned_group_exit(process, pgid=pgid, timeout=0.0)


def test_signal_zero_permission_reaps_leader_that_exits_after_initial_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(["/usr/bin/true"], start_new_session=True)
    process.wait(timeout=5.0)
    pgid = process.pid
    poll_results: list[int | None] = [None, 0]
    signal_attempts = 0

    def _poll() -> int | None:
        return poll_results.pop(0)

    def _signal_zero(_pgid: int, _signal_number: int) -> None:
        nonlocal signal_attempts
        signal_attempts += 1
        if signal_attempts == 1:
            raise PermissionError(1, "injected signal-zero denial")
        raise ProcessLookupError

    def _probe_group(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["ps"],
            returncode=0,
            stdout=f"{process.pid} 1 {pgid} 0 Z\n",
            stderr="",
        )

    monkeypatch.setattr(process, "poll", _poll)
    monkeypatch.setattr(runtime_module.os, "killpg", _signal_zero)
    monkeypatch.setattr(runtime_module.subprocess, "run", _probe_group)

    assert runtime_module._wait_for_owned_group_exit(process, pgid=pgid, timeout=0.0)
    assert poll_results == []
    assert signal_attempts == 2


def test_signal_zero_permission_propagates_process_probe_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(["/usr/bin/true"], start_new_session=True)
    process.wait(timeout=5.0)

    def _deny_signal_zero(_pgid: int, _signal_number: int) -> None:
        raise PermissionError(1, "injected signal-zero denial")

    def _timeout_probe(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["ps"], timeout=1.0)

    monkeypatch.setattr(runtime_module.os, "killpg", _deny_signal_zero)
    monkeypatch.setattr(runtime_module.subprocess, "run", _timeout_probe)
    with pytest.raises(PermissionError, match="exact PGID"):
        runtime_module._wait_for_owned_group_exit(process, pgid=process.pid, timeout=0.0)
