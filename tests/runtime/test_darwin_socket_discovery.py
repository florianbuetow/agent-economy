"""Darwin-native TCP listener discovery without filesystem-scanning lsof."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.stop_agent as stop_agent_module
from scripts.stop_agent import (
    ProcessIdentity,
    ServiceStopTarget,
    _parse_darwin_tcp_listeners,
    listener_pids,
    select_service_processes,
)

pytestmark = pytest.mark.unit

_HEADER = """Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address  Foreign Address  (state) rxbytes txbytes rhiwat shiwat process:pid state options gencnt flags flags1 usecnt rtncnt fltrs
"""


def _row(local: str, process: str, pid: int) -> str:
    return (
        f"tcp4 0 0 {local} *.* LISTEN 0 0 131072 131072 "
        f"{process}:{pid} 00100 00000006 00000000081c9489 "
        "00000001 00000800 1 0 000000\n"
    )


def _target(tmp_path: Path) -> ServiceStopTarget:
    repo_root = tmp_path / "repo"
    service_root = repo_root / "services" / "identity"
    bin_dir = service_root / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    bin_dir.joinpath("python").touch()
    bin_dir.joinpath("uvicorn").touch()
    service_root.joinpath("config.yaml").write_text(
        "server:\n  host: 127.0.0.1\n  port: 8001\n", encoding="utf-8"
    )
    return ServiceStopTarget.create(
        repo_root=repo_root,
        service_root=service_root,
        config_path=service_root / "config.yaml",
        entrypoint="identity_service.app:create_app",
        port=8001,
    )


def _identity(
    target: ServiceStopTarget, *, pid: int, argv: tuple[str, ...]
) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        ppid=1,
        uid=os.getuid(),
        start_id=f"birth-{pid}",
        cwd=target.service_root,
        executable=target.interpreter,
        argv=argv,
        environment={"CONFIG_PATH": str(target.config_path)},
    )


def test_parser_reads_realistic_listener_with_spaced_process_name() -> None:
    output = _HEADER + _row("127.0.0.1.8001", "Python Service Worker", 71040)

    assert _parse_darwin_tcp_listeners(output, 8001) == (71040,)


def test_darwin_discovery_uses_native_socket_table_without_lsof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _HEADER + _row("127.0.0.1.8001", "python3.13", 71040)
    commands: list[tuple[str, ...]] = []

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(tuple(command))
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(stop_agent_module.sys, "platform", "darwin")
    monkeypatch.setattr(stop_agent_module.subprocess, "run", run)

    assert listener_pids(8001) == (71040,)
    assert commands == [("/usr/sbin/netstat", "-anv", "-p", "tcp")]


def test_native_foreign_or_ambiguous_listener_set_is_rejected(tmp_path: Path) -> None:
    target = _target(tmp_path)
    output = (
        _HEADER
        + _row("127.0.0.1.8001", "python3.13", 71040)
        + _row("127.0.0.1.8001", "foreign", 71041)
    )
    owned_argv = (
        str(target.interpreter),
        str(target.uvicorn_launcher),
        target.entrypoint,
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8001",
    )
    owned = _identity(target, pid=71040, argv=owned_argv)
    foreign = _identity(
        target,
        pid=71041,
        argv=(str(target.interpreter), "-m", "http.server", "8001"),
    )
    discovered = _parse_darwin_tcp_listeners(output, 8001)

    assert discovered == (71040, 71041)
    with pytest.raises(RuntimeError, match="foreign or ambiguous"):
        select_service_processes((owned, foreign), target)


def test_relevant_malformed_native_listener_record_fails_closed() -> None:
    output = _HEADER + _row("127.0.0.1.8001", "missing-pid", 71040).replace(
        "missing-pid:71040", "missing-pid:not-a-pid"
    )

    with pytest.raises(RuntimeError, match="malformed Darwin TCP listener"):
        _parse_darwin_tcp_listeners(output, 8001)


@pytest.mark.parametrize("output", ["", "garbage\n", "Active Internet connections\n"])
def test_empty_or_truncated_native_table_fails_closed(output: str) -> None:
    with pytest.raises(RuntimeError, match="unrecognized Darwin TCP table"):
        _parse_darwin_tcp_listeners(output, 8001)


def test_recognized_native_table_without_matching_listener_is_empty() -> None:
    output = _HEADER + _row("127.0.0.1.8123", "python3.13", 71040)

    assert _parse_darwin_tcp_listeners(output, 8001) == ()


def test_native_listener_pid_zero_is_rejected() -> None:
    output = _HEADER + _row("127.0.0.1.8001", "kernel_task", 0)

    with pytest.raises(RuntimeError, match="PID 0"):
        _parse_darwin_tcp_listeners(output, 8001)
