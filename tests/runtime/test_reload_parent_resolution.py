"""Bounded reload-supervisor recovery from native listener children."""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

from scripts.stop_agent import (
    ProcessIdentity,
    ServiceStopTarget,
    capture_service_processes,
    select_service_processes,
    stop_service,
)

pytestmark = pytest.mark.unit


def _target(tmp_path: Path) -> ServiceStopTarget:
    repo_root = tmp_path / "repo"
    service_root = repo_root / "services" / "identity"
    bin_dir = service_root / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    bin_dir.joinpath("python").touch()
    bin_dir.joinpath("uvicorn").touch()
    config = service_root / "config.yaml"
    config.write_text("server:\n  port: 8001\n", encoding="utf-8")
    return ServiceStopTarget.create(
        repo_root=repo_root,
        service_root=service_root,
        config_path=config,
        entrypoint="identity_service.app:create_app",
        port=8001,
    )


def _identity(
    target: ServiceStopTarget,
    *,
    pid: int,
    ppid: int,
    argv: tuple[str, ...],
    config: Path | None = None,
) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        ppid=ppid,
        uid=os.getuid(),
        start_id=f"birth-{pid}",
        cwd=target.service_root,
        executable=target.interpreter,
        argv=argv,
        environment={
            "CONFIG_PATH": str(target.config_path if config is None else config)
        },
    )


def _reload_parent(target: ServiceStopTarget, *, pid: int) -> ProcessIdentity:
    return _identity(
        target,
        pid=pid,
        ppid=1,
        argv=(
            str(target.interpreter),
            str(target.uvicorn_launcher),
            target.entrypoint,
            "--factory",
            "--reload",
            "--host",
            "127.0.0.1",
            "--port",
            str(target.port),
        ),
    )


def _reload_child(
    target: ServiceStopTarget, *, pid: int, parent_pid: int, config: Path | None = None
) -> ProcessIdentity:
    return _identity(
        target,
        pid=pid,
        ppid=parent_pid,
        argv=(
            str(target.interpreter),
            "-c",
            "from multiprocessing.spawn import spawn_main; spawn_main()",
            "--multiprocessing-fork",
        ),
        config=config,
    )


def test_child_only_listener_resolves_exact_direct_reload_parent(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    parent = _reload_parent(target, pid=701)
    child = _reload_child(target, pid=702, parent_pid=parent.pid)
    processes = {parent.pid: parent, child.pid: child}

    captured = capture_service_processes(
        (child.pid,), target, inspect_process=lambda pid: processes[pid]
    )
    selected_parent, family = select_service_processes(captured, target)

    assert captured == (parent, child)
    assert selected_parent == parent
    assert family == (parent, child)


def test_child_only_listener_rejects_foreign_parent_or_child_family(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    parent = _reload_parent(target, pid=711)
    foreign_parent = ProcessIdentity(
        **{**parent.__dict__, "argv": (str(target.interpreter), "-m", "http.server")}
    )
    child = _reload_child(target, pid=712, parent_pid=parent.pid)

    with pytest.raises(RuntimeError, match="exact reload supervisor"):
        capture_service_processes(
            (child.pid,),
            target,
            inspect_process=lambda pid: child if pid == child.pid else foreign_parent,
        )

    wrong_config = tmp_path / "foreign-config.yaml"
    wrong_config.write_text("server: {}\n", encoding="utf-8")
    foreign_child = _reload_child(
        target, pid=713, parent_pid=parent.pid, config=wrong_config
    )
    inspected: list[int] = []

    with pytest.raises(
        RuntimeError, match="does not match the selected service family"
    ):
        capture_service_processes(
            (foreign_child.pid,),
            target,
            inspect_process=lambda pid: inspected.append(pid) or foreign_child,
        )

    assert inspected == [foreign_child.pid]


def test_changed_reload_parent_is_never_signaled(tmp_path: Path) -> None:
    target = _target(tmp_path)
    parent = _reload_parent(target, pid=721)
    child = _reload_child(target, pid=722, parent_pid=parent.pid)
    replacement_parent = ProcessIdentity(
        **{**parent.__dict__, "start_id": "replacement-birth"}
    )
    inspections = {child.pid: child, parent.pid: replacement_parent}
    signals: list[tuple[int, signal.Signals]] = []

    with pytest.raises(
        RuntimeError, match="supervisor exited while a captured child remains"
    ):
        stop_service(
            target,
            captured=(parent, child),
            timeout_seconds=1.0,
            inspect_process=lambda pid: inspections[pid],
            send_signal=lambda pid, selected: signals.append((pid, selected)),
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
        )

    assert signals == []
