"""Fail-closed ownership checks for the local agent stop helper."""

from __future__ import annotations

import os
import signal
import struct
from pathlib import Path

import pytest

import scripts.stop_agent as stop_agent_module
from scripts.stop_agent import (
    ProcessIdentity,
    StopTarget,
    _parse_darwin_procargs_buffer,
    select_owned_processes,
    stop_agents,
)

pytestmark = pytest.mark.unit


def _identity(
    *,
    pid: int = 41,
    uid: int | None = None,
    start_id: str = "start-41",
    cwd: Path,
    executable: Path,
    argv: tuple[str, ...],
    config_path: Path,
) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        ppid=1,
        uid=os.getuid() if uid is None else uid,
        start_id=start_id,
        cwd=cwd,
        executable=executable,
        argv=argv,
        environment={"AGENT_CONFIG_PATH": str(config_path)},
    )


def test_selection_requires_exact_repo_cwd_module_config_and_profile(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    agents_dir = repo_root / "agents"
    interpreter = agents_dir / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    config_path = agents_dir / "config.yaml"
    config_path.write_text("workers: {}\n", encoding="utf-8")
    target = StopTarget.math_worker(repo_root, config_path, "mathbot")
    exact = _identity(
        cwd=agents_dir,
        executable=interpreter,
        argv=("/venv/bin/python", "-m", "math_worker", "mathbot"),
        config_path=config_path,
    )
    wrong_cwd = _identity(
        pid=42,
        cwd=tmp_path / "other" / "agents",
        executable=interpreter,
        argv=exact.argv,
        config_path=config_path,
    )
    wrong_config = _identity(
        pid=43,
        cwd=agents_dir,
        executable=interpreter,
        argv=exact.argv,
        config_path=tmp_path / "isolated" / "config.yaml",
    )
    wrong_profile = _identity(
        pid=44,
        cwd=agents_dir,
        executable=interpreter,
        argv=("/venv/bin/python", "-m", "math_worker", "otherbot"),
        config_path=config_path,
    )
    wrong_module = _identity(
        pid=45,
        cwd=agents_dir,
        executable=interpreter,
        argv=("/venv/bin/python", "-m", "task_feeder"),
        config_path=config_path,
    )
    wrong_uid = _identity(
        pid=46,
        uid=os.getuid() + 1,
        cwd=agents_dir,
        executable=interpreter,
        argv=exact.argv,
        config_path=config_path,
    )
    wrong_executable = _identity(
        pid=47,
        cwd=agents_dir,
        executable=tmp_path / "other-python",
        argv=exact.argv,
        config_path=config_path,
    )
    argv_suffix_spoof = _identity(
        pid=48,
        cwd=agents_dir,
        executable=interpreter,
        argv=("/bin/sh", "-c", "payload", "-m", "math_worker", "mathbot"),
        config_path=config_path,
    )

    assert select_owned_processes(
        (
            exact,
            wrong_cwd,
            wrong_config,
            wrong_profile,
            wrong_module,
            wrong_uid,
            wrong_executable,
            argv_suffix_spoof,
        ),
        target,
    ) == (exact,)


def test_default_config_is_resolved_from_the_confirmed_process_cwd(
    tmp_path: Path,
) -> None:
    agents_dir = tmp_path / "repo" / "agents"
    interpreter = agents_dir / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    config_path = agents_dir / "config.yaml"
    config_path.write_text("task_feeder: {}\n", encoding="utf-8")
    target = StopTarget.task_feeder(tmp_path / "repo", config_path)
    identity = ProcessIdentity(
        pid=51,
        ppid=1,
        uid=os.getuid(),
        start_id="start-51",
        cwd=agents_dir,
        executable=interpreter,
        argv=("/venv/bin/python", "-m", "task_feeder"),
        environment={},
    )

    assert select_owned_processes((identity,), target) == (identity,)


def test_relative_agent_config_override_resolves_from_agent_start_cwd(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    agents_dir = repo_root / "agents"
    interpreter = agents_dir / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    config_path = agents_dir / "custom.yaml"
    config_path.write_text("task_feeder: {}\n", encoding="utf-8")

    target = StopTarget.task_feeder(repo_root, Path("custom.yaml"))

    assert target.config_path == config_path


def test_stop_revalidates_full_identity_immediately_before_signal(
    tmp_path: Path,
) -> None:
    agents_dir = tmp_path / "repo" / "agents"
    interpreter = agents_dir / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    config_path = agents_dir / "config.yaml"
    config_path.write_text("workers: {}\n", encoding="utf-8")
    target = StopTarget.math_worker(tmp_path / "repo", config_path, "mathbot")
    captured = _identity(
        cwd=agents_dir,
        executable=interpreter,
        argv=("/venv/bin/python", "-m", "math_worker", "mathbot"),
        config_path=config_path,
    )
    reused_pid = ProcessIdentity(
        pid=captured.pid,
        ppid=captured.ppid,
        uid=captured.uid,
        start_id="replacement-process",
        cwd=captured.cwd,
        executable=captured.executable,
        argv=captured.argv,
        environment=captured.environment,
    )
    signals: list[tuple[int, signal.Signals]] = []

    with pytest.raises(RuntimeError, match="identity changed"):
        stop_agents(
            target,
            captured=(captured,),
            inspect_process=lambda _pid: reused_pid,
            send_signal=lambda pid, selected: signals.append((pid, selected)),
        )

    assert signals == []


def test_stop_signals_only_the_revalidated_owned_process(tmp_path: Path) -> None:
    agents_dir = tmp_path / "repo" / "agents"
    interpreter = agents_dir / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    config_path = agents_dir / "config.yaml"
    config_path.write_text("task_feeder: {}\n", encoding="utf-8")
    target = StopTarget.task_feeder(tmp_path / "repo", config_path)
    owned = _identity(
        cwd=agents_dir,
        executable=interpreter,
        argv=("/venv/bin/python", "-m", "task_feeder"),
        config_path=config_path,
    )
    signals: list[tuple[int, signal.Signals]] = []

    stopped = stop_agents(
        target,
        captured=(owned,),
        inspect_process=lambda _pid: owned,
        send_signal=lambda pid, selected: signals.append((pid, selected)),
    )

    assert stopped == (owned.pid,)
    assert signals == [(owned.pid, signal.SIGTERM)]


def test_darwin_procargs_parser_preserves_empty_argv_before_environment() -> None:
    raw = (
        struct.pack("i", 4)
        + b"/resolved/python\0\0\0"
        + b"python\0-m\0math_worker\0\0"
        + b"AGENT_CONFIG_PATH=/config.yaml\0SECRET=hidden\0"
    )

    executable, argv, environment = _parse_darwin_procargs_buffer(raw, 99)

    assert executable == "/resolved/python"
    assert argv == ("python", "-m", "math_worker", "")
    assert environment["AGENT_CONFIG_PATH"] == "/config.yaml"


def test_darwin_inspection_rejects_identity_change_during_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    identities = iter(((os.getuid(), 1, "start-one"), (os.getuid(), 1, "start-two")))
    monkeypatch.setattr(
        stop_agent_module, "_darwin_bsd_identity", lambda _pid: next(identities)
    )
    monkeypatch.setattr(
        stop_agent_module,
        "_darwin_procargs",
        lambda _pid: ("/resolved/python", ("python", "-m", "task_feeder"), {}),
    )
    monkeypatch.setattr(stop_agent_module, "_darwin_cwd", lambda _pid: tmp_path)

    with pytest.raises(ProcessLookupError):
        stop_agent_module._darwin_process_identity(99)


def test_root_stop_recipes_use_scoped_helper_without_broad_process_kills() -> None:
    justfile = (
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("justfile")
        .read_text(encoding="utf-8")
    )
    stop_recipes = justfile.split("# Stop task feeder", 1)[1].split(
        "# Fund the feeder agent", 1
    )[0]

    assert "pkill" not in stop_recipes
    assert "scripts/stop_agent.py" in stop_recipes
    assert 'stop-mathbot profile="mathbot":' in stop_recipes
    assert "--module task_feeder" in stop_recipes
    assert "--module math_worker" in stop_recipes
    assert "--profile {{quote(profile)}}" in stop_recipes
    assert "termination requested safely" in stop_recipes.lower()
