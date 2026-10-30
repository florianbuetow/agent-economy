#!/usr/bin/env python3
"""Install, remove and inspect OS-level supervision for the local stack.

macOS: one launchd LaunchAgent plist per service, feeder and math worker.
Linux: one systemd user unit per service, feeder and math worker.

Usage::

    uv run --project agents scripts/supervise.py install
    uv run --project agents scripts/supervise.py status
    uv run --project agents scripts/supervise.py uninstall
    uv run --project agents scripts/supervise.py stop      # unload, keep unit files
    uv run --project agents scripts/supervise.py start     # load installed unit files

Every ProgramArguments / ExecStart is the same argv the root justfile
``_launch-*`` recipe runs after its ``cd``, so scripts/stop_agent.py keeps
recognising the supervised processes. Exit code is 1 if any unit fails.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

LABEL_PREFIX = "com.agent-economy"

# name, directory under services/, uvicorn entrypoint, justfile port variable, start-order deps
_SERVICES: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    (
        "db-gateway",
        "db-gateway",
        "db_gateway_service.app:create_app",
        "port_db_gateway",
        (),
    ),
    (
        "identity",
        "identity",
        "identity_service.app:create_app",
        "port_identity",
        ("db-gateway",),
    ),
    (
        "reputation",
        "reputation",
        "reputation_service.app:create_app",
        "port_reputation",
        ("identity",),
    ),
    (
        "central-bank",
        "central-bank",
        "central_bank_service.app:create_app",
        "port_central_bank",
        ("identity",),
    ),
    (
        "task-board",
        "task-board",
        "task_board_service.app:create_app",
        "port_task_board",
        ("identity", "central-bank"),
    ),
    (
        "court",
        "court",
        "court_service.app:create_app",
        "port_court",
        ("task-board", "reputation"),
    ),
    ("ui", "ui", "ui_service.app:create_app", "port_ui", ("identity", "task-board")),
)

# name, module, profile argument or None, start-order deps
_AGENTS: tuple[tuple[str, str, str | None, tuple[str, ...]], ...] = (
    ("feeder", "task_feeder", None, ("task-board",)),
    ("mathbot", "math_worker", "mathbot", ("task-board",)),
)


@dataclass(frozen=True)
class Unit:
    """One supervised process."""

    name: str
    working_dir: Path
    argv: tuple[str, ...]
    after: tuple[str, ...]
    stdout_path: Path
    stderr_path: Path
    env_file: Path | None

    @property
    def label(self) -> str:
        return f"{LABEL_PREFIX}.{self.name}"


def read_ports(justfile: Path) -> dict[str, str]:
    """Return the ``port_* := "N"`` variables from the root justfile."""
    ports = dict(
        re.findall(r'^(port_\w+)\s*:=\s*"(\d+)"\s*$', justfile.read_text(), re.M)
    )
    if not ports:
        raise RuntimeError(f"No port variables found in {justfile}")
    return ports


def read_host(config_path: Path) -> str:
    """Return ``server.host`` from a service config.yaml."""
    config = yaml.safe_load(config_path.read_text())
    host = config["server"]["host"]
    if not isinstance(host, str) or not host:
        raise RuntimeError(f"server.host missing in {config_path}")
    return host


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; comments, blanks and empty values are skipped."""
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("\"'")
        if value:
            values[key.strip()] = value
    return values


def build_units(repo_root: Path, uv_path: str) -> list[Unit]:
    """Build the unit list for the stack, reading ports and hosts from repo files."""
    ports = read_ports(repo_root / "justfile")
    log_dir = repo_root / "data" / "logs"
    units: list[Unit] = []
    for name, directory, entrypoint, port_var, after in _SERVICES:
        service_dir = repo_root / "services" / directory
        host = read_host(service_dir / "config.yaml")
        env_file = service_dir / ".env" if name == "court" else None
        units.append(
            Unit(
                name=name,
                working_dir=service_dir,
                argv=(
                    uv_path,
                    "run",
                    "uvicorn",
                    entrypoint,
                    "--factory",
                    "--host",
                    host,
                    "--port",
                    ports[port_var],
                ),
                after=after,
                stdout_path=log_dir / f"supervised-{name}.out.log",
                stderr_path=log_dir / f"supervised-{name}.err.log",
                env_file=env_file
                if env_file is not None and env_file.is_file()
                else None,
            )
        )
    for name, module, profile, after in _AGENTS:
        tail = ("-m", module) if profile is None else ("-m", module, profile)
        units.append(
            Unit(
                name=name,
                working_dir=repo_root / "agents",
                argv=(uv_path, "run", "python", *tail),
                after=after,
                stdout_path=log_dir / f"supervised-{name}.out.log",
                stderr_path=log_dir / f"supervised-{name}.err.log",
                env_file=None,
            )
        )
    return units


def render_plist(unit: Unit, environment: dict[str, str]) -> bytes:
    """Render the launchd plist for a unit. Output is deterministic."""
    document: dict[str, object] = {
        "Label": unit.label,
        "ProgramArguments": list(unit.argv),
        "WorkingDirectory": str(unit.working_dir),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "StandardOutPath": str(unit.stdout_path),
        "StandardErrorPath": str(unit.stderr_path),
    }
    if environment:
        document["EnvironmentVariables"] = dict(sorted(environment.items()))
    return plistlib.dumps(document, sort_keys=True)


def _systemd_quote(argument: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", argument):
        return argument
    escaped = argument.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_systemd(unit: Unit) -> str:
    """Render the systemd user unit for a unit. Output is deterministic."""
    after = "".join(f"{LABEL_PREFIX}.{dep}.service " for dep in unit.after).strip()
    lines = ["[Unit]", f"Description=Agent Economy {unit.name}"]
    if after:
        lines += [f"After={after}", f"Wants={after}"]
    lines += [
        "",
        "[Service]",
        f"WorkingDirectory={unit.working_dir}",
        f"ExecStart={' '.join(_systemd_quote(a) for a in unit.argv)}",
    ]
    if unit.env_file is not None:
        lines.append(f"EnvironmentFile={unit.env_file}")
    lines += [
        "Restart=on-failure",
        "RestartSec=3",
        f"StandardOutput=append:{unit.stdout_path}",
        f"StandardError=append:{unit.stderr_path}",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def _unit_path(unit: Unit, unit_dir: Path, platform: str) -> Path:
    suffix = ".plist" if platform == "darwin" else ".service"
    return unit_dir / f"{unit.label}{suffix}"


def default_unit_dir(platform: str) -> Path:
    if platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents"
    return Path.home() / ".config" / "systemd" / "user"


def _run(command: list[str]) -> bool:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"  {' '.join(command)} -> {result.returncode}: {result.stderr.strip()}")
    return result.returncode == 0


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(units: list[Unit], unit_dir: Path, platform: str, load: bool) -> int:
    """Write and load every unit. Returns the failure count."""
    unit_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for unit in units:
        unit.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        path = _unit_path(unit, unit_dir, platform)
        try:
            if platform == "darwin":
                env = read_env_file(unit.env_file) if unit.env_file is not None else {}
                path.write_bytes(render_plist(unit, env))
            else:
                path.write_text(render_systemd(unit))
            path.chmod(0o600)
        except OSError as exc:
            print(f"FAIL {unit.name}: {exc}")
            failures += 1
            continue
        if not load:
            print(f"wrote {path}")
            continue
        if platform == "darwin":
            _run(["launchctl", "bootout", f"{_domain()}/{unit.label}"])
            ok = _run(["launchctl", "bootstrap", _domain(), str(path)])
        else:
            ok = _run(["systemctl", "--user", "daemon-reload"]) and _run(
                ["systemctl", "--user", "enable", "--now", path.name]
            )
        print(f"{'ok  ' if ok else 'FAIL'} {unit.name}")
        failures += 0 if ok else 1
    return failures


def uninstall(units: list[Unit], unit_dir: Path, platform: str, load: bool) -> int:
    """Unload and remove every unit. Returns the failure count."""
    failures = 0
    for unit in units:
        path = _unit_path(unit, unit_dir, platform)
        if load and path.exists():
            if platform == "darwin":
                ok = _run(["launchctl", "bootout", f"{_domain()}/{unit.label}"])
            else:
                ok = _run(["systemctl", "--user", "disable", "--now", path.name])
            if not ok:
                print(f"FAIL {unit.name}")
                failures += 1
                continue
        path.unlink(missing_ok=True)
        print(f"removed {unit.name}")
    if load and platform != "darwin":
        _run(["systemctl", "--user", "daemon-reload"])
    return failures


def installed(units: list[Unit], unit_dir: Path, platform: str) -> list[Unit]:
    """Return the units whose unit file exists."""
    return [u for u in units if _unit_path(u, unit_dir, platform).exists()]


def set_running(units: list[Unit], unit_dir: Path, platform: str, running: bool) -> int:
    """Stop or start installed units without removing their unit files."""
    failures = 0
    for unit in installed(units, unit_dir, platform):
        path = _unit_path(unit, unit_dir, platform)
        if platform == "darwin":
            if running:
                ok = _run(["launchctl", "bootstrap", _domain(), str(path)])
            else:
                ok = _run(["launchctl", "bootout", f"{_domain()}/{unit.label}"])
        else:
            ok = _run(
                ["systemctl", "--user", "start" if running else "stop", path.name]
            )
        print(
            f"{'ok  ' if ok else 'FAIL'} {'start' if running else 'stop'} {unit.name}"
        )
        failures += 0 if ok else 1
    return failures


def status(units: list[Unit], unit_dir: Path, platform: str) -> int:
    """Print install and run state per unit. Returns the number of missing units."""
    missing = 0
    for unit in units:
        path = _unit_path(unit, unit_dir, platform)
        if not path.exists():
            print(f"{unit.name:<12} not installed")
            missing += 1
            continue
        if platform == "darwin":
            result = subprocess.run(
                ["launchctl", "print", f"{_domain()}/{unit.label}"],
                capture_output=True,
                text=True,
                check=False,
            )
            match = re.search(r"^\s*state = (\w+)", result.stdout, re.M)
            state = match.group(1) if match else "not loaded"
        else:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", path.name],
                capture_output=True,
                text=True,
                check=False,
            )
            state = result.stdout.strip() or "unknown"
        print(f"{unit.name:<12} installed, {state}")
    return missing


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command", choices=("install", "uninstall", "status", "stop", "start")
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--unit-dir", type=Path, default=None)
    parser.add_argument(
        "--no-load",
        action="store_true",
        help="Only write or remove unit files; do not call launchctl or systemctl.",
    )
    args = parser.parse_args(argv)

    platform = "darwin" if sys.platform == "darwin" else "linux"
    unit_dir = (
        args.unit_dir if args.unit_dir is not None else default_unit_dir(platform)
    )
    uv_path = shutil.which("uv")
    if uv_path is None:
        print("uv not found on PATH", file=sys.stderr)
        return 1
    units = build_units(args.repo_root.resolve(), uv_path)
    if args.command == "install":
        return 1 if install(units, unit_dir, platform, not args.no_load) else 0
    if args.command == "uninstall":
        return 1 if uninstall(units, unit_dir, platform, not args.no_load) else 0
    if args.command in {"stop", "start"}:
        return (
            1 if set_running(units, unit_dir, platform, args.command == "start") else 0
        )
    return 1 if status(units, unit_dir, platform) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
