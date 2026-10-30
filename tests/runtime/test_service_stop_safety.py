"""Fail-closed ownership checks for local service stop recipes."""

from __future__ import annotations

import errno
import os
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.stop_agent as stop_agent_module
from scripts.stop_agent import (
    ProcessAbsentError,
    ProcessIdentity,
    ServiceStopTarget,
    _lsof_listener_pids,
    select_service_processes,
    stop_service,
)

pytestmark = pytest.mark.unit


def _target(tmp_path: Path) -> ServiceStopTarget:
    repo_root = tmp_path / "repo"
    service_root = repo_root / "services" / "identity"
    interpreter = service_root / ".venv" / "bin" / "python"
    uvicorn = service_root / ".venv" / "bin" / "uvicorn"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    uvicorn.touch()
    config_path = service_root / "config.yaml"
    config_path.write_text("server:\n  host: 127.0.0.1\n  port: 8001\n", encoding="utf-8")
    return ServiceStopTarget.create(
        repo_root=repo_root,
        service_root=service_root,
        config_path=config_path,
        entrypoint="identity_service.app:create_app",
        port=8001,
    )


def _identity(
    target: ServiceStopTarget,
    *,
    pid: int,
    ppid: int,
    argv: tuple[str, ...],
    config_path: Path | None = None,
    cwd: Path | None = None,
    executable: Path | None = None,
) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        ppid=ppid,
        uid=os.getuid(),
        start_id=f"start-{pid}",
        cwd=target.service_root if cwd is None else cwd,
        executable=target.interpreter if executable is None else executable,
        argv=argv,
        environment={
            "CONFIG_PATH": str(
                target.config_path if config_path is None else config_path
            )
        },
    )


def _uvicorn_argv(
    target: ServiceStopTarget, *, reload: bool, host: str = "127.0.0.1"
) -> tuple[str, ...]:
    arguments = [
        str(target.interpreter),
        str(target.uvicorn_launcher),
        target.entrypoint,
        "--factory",
    ]
    if reload:
        arguments.append("--reload")
    arguments.extend(("--host", host, "--port", str(target.port)))
    return tuple(arguments)


def test_foreground_selection_requires_exact_service_identity(tmp_path: Path) -> None:
    target = _target(tmp_path)
    owned = _identity(target, pid=101, ppid=1, argv=_uvicorn_argv(target, reload=False))
    wrong_config = _identity(
        target,
        pid=102,
        ppid=1,
        argv=_uvicorn_argv(target, reload=False),
        config_path=tmp_path / "isolated" / "config.yaml",
    )

    root, family = select_service_processes((owned,), target)

    assert root == owned
    assert family == (owned,)
    with pytest.raises(RuntimeError, match="foreign or ambiguous"):
        select_service_processes((wrong_config,), target)


def test_relative_service_config_override_resolves_from_service_start_cwd(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    relative_config = target.service_root / "custom.yaml"
    relative_config.write_text("server:\n  host: 127.0.0.1\n  port: 8001\n", encoding="utf-8")

    relative_target = ServiceStopTarget.create(
        repo_root=target.repo_root,
        service_root=target.service_root,
        config_path=Path("custom.yaml"),
        entrypoint=target.entrypoint,
        port=target.port,
    )

    assert relative_target.config_path == relative_config


def test_reload_selection_proves_child_parentage_without_selecting_child(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    supervisor = _identity(
        target, pid=201, ppid=1, argv=_uvicorn_argv(target, reload=True)
    )
    child = _identity(
        target,
        pid=202,
        ppid=supervisor.pid,
        argv=(
            str(target.interpreter),
            "-c",
            "from multiprocessing.spawn import spawn_main; spawn_main()",
            "--multiprocessing-fork",
        ),
    )

    root, family = select_service_processes((supervisor, child), target)

    assert root == supervisor
    assert family == (supervisor, child)


def test_stop_signals_only_root_and_confirms_captured_family_exit(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    supervisor = _identity(
        target, pid=301, ppid=1, argv=_uvicorn_argv(target, reload=True)
    )
    child = _identity(
        target,
        pid=302,
        ppid=supervisor.pid,
        argv=(str(target.interpreter), "-c", "spawn_main()"),
    )
    signals: list[tuple[int, signal.Signals]] = []
    times = iter((0.0, 0.0, 0.1))
    inspections = {supervisor.pid: [supervisor], child.pid: [child]}

    def inspect_then_exit(pid: int) -> ProcessIdentity:
        values = inspections[pid]
        if values:
            return values.pop()
        raise ProcessAbsentError(pid)

    requested = stop_service(
        target,
        captured=(supervisor, child),
        timeout_seconds=1.0,
        inspect_process=inspect_then_exit,
        send_signal=lambda pid, selected: signals.append((pid, selected)),
        monotonic=lambda: next(times),
        sleep=lambda _seconds: None,
    )

    assert requested == supervisor.pid
    assert signals == [(supervisor.pid, signal.SIGTERM)]


def test_stop_refuses_foreign_listener_without_signal(tmp_path: Path) -> None:
    target = _target(tmp_path)
    foreign = _identity(
        target,
        pid=401,
        ppid=1,
        argv=("/usr/bin/python", "-m", "http.server", "8001"),
        executable=Path("/usr/bin/python"),
    )
    signals: list[tuple[int, signal.Signals]] = []

    with pytest.raises(RuntimeError, match="foreign or ambiguous"):
        stop_service(
            target,
            captured=(foreign,),
            timeout_seconds=1.0,
            inspect_process=lambda _pid: foreign,
            send_signal=lambda pid, selected: signals.append((pid, selected)),
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
        )

    assert signals == []


def test_same_birth_metadata_change_is_not_mistaken_for_exit(tmp_path: Path) -> None:
    target = _target(tmp_path)
    owned = _identity(target, pid=451, ppid=1, argv=_uvicorn_argv(target, reload=False))
    changed = ProcessIdentity(
        pid=owned.pid,
        ppid=owned.ppid,
        uid=owned.uid,
        start_id=owned.start_id,
        cwd=owned.cwd,
        executable=owned.executable,
        argv=(*owned.argv, "--unexpected"),
        environment=owned.environment,
    )
    inspections = iter((owned, changed))

    with pytest.raises(RuntimeError, match="metadata changed while still running"):
        stop_service(
            target,
            captured=(owned,),
            timeout_seconds=1.0,
            inspect_process=lambda _pid: next(inspections),
            send_signal=lambda _pid, _selected: None,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
        )


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_stop_rejects_non_finite_timeout(tmp_path: Path, timeout: float) -> None:
    target = _target(tmp_path)
    owned = _identity(target, pid=461, ppid=1, argv=_uvicorn_argv(target, reload=False))

    with pytest.raises(ValueError, match="finite"):
        stop_service(
            target,
            captured=(owned,),
            timeout_seconds=timeout,
            inspect_process=lambda _pid: owned,
            send_signal=lambda _pid, _selected: None,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
        )


def test_listener_parser_accepts_realistic_process_and_descriptor_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.stop_agent.shutil.which", lambda _name: "/usr/sbin/lsof"
    )
    monkeypatch.setattr(
        "scripts.stop_agent.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="p71040\nf7\n", stderr=""
        ),
    )

    assert _lsof_listener_pids(8001) == (71040,)


def test_listener_parser_accepts_valid_evidence_and_surfaces_unrelated_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "scripts.stop_agent.shutil.which", lambda _name: "/usr/sbin/lsof"
    )
    monkeypatch.setattr(
        "scripts.stop_agent.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="p71040\nf7\n",
            stderr="lsof: WARNING: can't stat() apfs file system /Volumes/TimeMachine\n",
        ),
    )

    assert _lsof_listener_pids(8001) == (71040,)
    assert "TimeMachine" in capsys.readouterr().err


def test_listener_parser_accepts_clean_empty_result_without_socket_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.stop_agent.shutil.which", lambda _name: "/usr/sbin/lsof"
    )
    monkeypatch.setattr(
        "scripts.stop_agent.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        "scripts.stop_agent.socket.socket",
        lambda *_args, **_kwargs: pytest.fail(
            "clean absence must not need a socket probe"
        ),
    )

    assert _lsof_listener_pids(8001) == ()


@pytest.mark.parametrize(
    ("connect_result", "expected"),
    [
        (0, "still accepts connections"),
        (errno.EACCES, "cannot prove loopback refusal"),
    ],
)
def test_listener_error_refuses_live_or_indeterminate_port(
    monkeypatch: pytest.MonkeyPatch, connect_result: int, expected: str
) -> None:
    class ProbeSocket:
        def __enter__(self) -> ProbeSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _timeout: float) -> None:
            return None

        def connect_ex(self, _address: tuple[str, int]) -> int:
            return connect_result

    monkeypatch.setattr(
        "scripts.stop_agent.shutil.which", lambda _name: "/usr/sbin/lsof"
    )
    monkeypatch.setattr(
        "scripts.stop_agent.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="permission denied"
        ),
    )
    monkeypatch.setattr(
        "scripts.stop_agent.socket.socket", lambda *_args, **_kwargs: ProbeSocket()
    )

    with pytest.raises(RuntimeError, match=expected):
        _lsof_listener_pids(8001)


def test_listener_error_accepts_independently_proven_connection_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RefusedSocket:
        def __enter__(self) -> RefusedSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def settimeout(self, _timeout: float) -> None:
            return None

        def connect_ex(self, _address: tuple[str, int]) -> int:
            return errno.ECONNREFUSED

    monkeypatch.setattr(
        "scripts.stop_agent.shutil.which", lambda _name: "/usr/sbin/lsof"
    )
    monkeypatch.setattr(
        "scripts.stop_agent.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="permission denied"
        ),
    )
    monkeypatch.setattr(
        "scripts.stop_agent.socket.socket", lambda *_args, **_kwargs: RefusedSocket()
    )

    assert _lsof_listener_pids(8001) == ()


def test_live_process_with_unreadable_cwd_is_not_reported_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel_identity = (os.getuid(), 1, "unchanged-birth")
    monkeypatch.setattr(
        "scripts.stop_agent._darwin_bsd_identity", lambda _pid: kernel_identity
    )
    monkeypatch.setattr(
        "scripts.stop_agent._darwin_cwd",
        lambda _pid: (_ for _ in ()).throw(RuntimeError("cwd unavailable")),
    )

    with pytest.raises(RuntimeError, match="complete metadata for live PID"):
        stop_agent_module._darwin_process_identity(71040)


def test_stop_returns_failure_when_selected_identity_exceeds_budget(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    owned = _identity(target, pid=501, ppid=1, argv=_uvicorn_argv(target, reload=False))
    signals: list[tuple[int, signal.Signals]] = []
    times = iter((0.0, 2.0))

    with pytest.raises(RuntimeError, match="still running after timeout"):
        stop_service(
            target,
            captured=(owned,),
            timeout_seconds=1.0,
            inspect_process=lambda _pid: owned,
            send_signal=lambda pid, selected: signals.append((pid, selected)),
            monotonic=lambda: next(times),
            sleep=lambda _seconds: None,
        )

    assert signals == [(owned.pid, signal.SIGTERM)]


def test_service_justfiles_share_scoped_non_escalating_kill_recipe() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    justfiles = tuple(sorted(repo_root.glob("services/*/justfile")))
    contents = {path.read_text(encoding="utf-8") for path in justfiles}

    assert len(justfiles) == 7
    assert len(contents) == 1
    kill_recipe = (
        next(iter(contents))
        .split("# Stop the locally running service", 1)[1]
        .split("# Check health status", 1)[0]
    )
    assert "lsof" not in kill_recipe
    assert "kill -9" not in kill_recipe
    assert "scripts/stop_agent.py" in kill_recipe
    assert "--service-entrypoint" in kill_recipe
    assert "--timeout-seconds" in kill_recipe


def _target_with_config(tmp_path: Path, config_text: str) -> ServiceStopTarget:
    target = _target(tmp_path)
    target.config_path.write_text(config_text, encoding="utf-8")
    return ServiceStopTarget.create(
        repo_root=target.repo_root,
        service_root=target.service_root,
        config_path=target.config_path,
        entrypoint=target.entrypoint,
        port=target.port,
    )


def test_configured_wildcard_host_is_recognised(tmp_path: Path) -> None:
    target = _target_with_config(tmp_path, "server:\n  host: 0.0.0.0\n  port: 8001\n")
    owned = _identity(
        target,
        pid=201,
        ppid=1,
        argv=_uvicorn_argv(target, reload=False, host="0.0.0.0"),
    )

    root, family = select_service_processes((owned,), target)

    assert target.host == "0.0.0.0"
    assert root == owned
    assert family == (owned,)


def test_process_bound_to_other_host_than_configured_is_refused(
    tmp_path: Path,
) -> None:
    target = _target_with_config(tmp_path, "server:\n  host: 0.0.0.0\n  port: 8001\n")
    other = _identity(
        target,
        pid=202,
        ppid=1,
        argv=_uvicorn_argv(target, reload=False, host="127.0.0.1"),
    )

    with pytest.raises(RuntimeError, match="foreign or ambiguous"):
        select_service_processes((other,), target)


def test_config_without_server_host_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="server.host"):
        _target_with_config(tmp_path, "server:\n  port: 8001\n")
