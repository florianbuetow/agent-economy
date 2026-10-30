"""Supervision unit generation: deterministic output and launch-recipe argv parity."""

from __future__ import annotations

import plistlib
import re
import shlex
from pathlib import Path

import pytest

import scripts.supervise as supervise

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
UV = "/opt/uv/bin/uv"

_RECIPE_FOR_SERVICE = {
    "db-gateway": "_launch-db-gateway",
    "identity": "_launch-identity",
    "reputation": "_launch-reputation",
    "central-bank": "_launch-bank",
    "task-board": "_launch-taskboard",
    "court": "_launch-court",
    "ui": "_launch-ui",
}


def _recipe_uvicorn_argv(recipe: str) -> list[str]:
    """Return the ``uv run uvicorn ...`` argv of a root justfile launch recipe."""
    text = (REPO_ROOT / "justfile").read_text()
    ports = supervise.read_ports(REPO_ROOT / "justfile")
    body = re.search(rf"^{re.escape(recipe)}:\n((?:[ \t]+.*\n|\n)+)", text, re.M)
    assert body is not None, recipe
    line = next(ln for ln in body.group(1).splitlines() if "uv run uvicorn" in ln)
    command = line[line.index("uv run uvicorn") :]
    command = re.sub(r"\{\{(port_\w+)\}\}", lambda m: ports[m.group(1)], command)
    return shlex.split(command)


def _units() -> dict[str, supervise.Unit]:
    return {u.name: u for u in supervise.build_units(REPO_ROOT, UV)}


def test_service_argv_matches_launch_recipe_apart_from_uv_path_and_host() -> None:
    units = _units()
    for name, recipe in _RECIPE_FOR_SERVICE.items():
        expected = _recipe_uvicorn_argv(recipe)
        actual = list(units[name].argv)
        assert actual[0] == UV
        assert _mask_host(actual[1:]) == _mask_host(expected[1:])
        host_at = actual.index("--host") + 1
        config_host = supervise.read_host(REPO_ROOT / "services" / name / "config.yaml")
        assert actual[host_at] == config_host


def _mask_host(argv: list[str]) -> list[str]:
    masked = list(argv)
    masked[masked.index("--host") + 1] = "<host>"
    return masked


def test_working_directory_matches_recipe_cd() -> None:
    for name, unit in _units().items():
        if name in {"feeder", "mathbot"}:
            assert unit.working_dir == REPO_ROOT / "agents"
        else:
            assert unit.working_dir == REPO_ROOT / "services" / name


def test_agent_argv_matches_start_recipes() -> None:
    units = _units()
    assert units["feeder"].argv == (UV, "run", "python", "-m", "task_feeder")
    assert units["mathbot"].argv == (
        UV,
        "run",
        "python",
        "-m",
        "math_worker",
        "mathbot",
    )


def test_plist_is_deterministic_and_has_required_keys() -> None:
    unit = _units()["task-board"]
    first = supervise.render_plist(unit, {})
    assert first == supervise.render_plist(unit, {})
    plist = plistlib.loads(first)
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ThrottleInterval"] == 5
    assert plist["RunAtLoad"] is True
    assert plist["Label"] == "com.agent-economy.task-board"
    assert plist["WorkingDirectory"] == str(REPO_ROOT / "services" / "task-board")
    assert plist["ProgramArguments"] == list(unit.argv)
    assert plist["StandardOutPath"].startswith(str(REPO_ROOT / "data" / "logs"))
    assert plist["StandardErrorPath"].startswith(str(REPO_ROOT / "data" / "logs"))
    assert "EnvironmentVariables" not in plist


def test_plist_environment_only_when_given() -> None:
    plist = plistlib.loads(
        supervise.render_plist(_units()["court"], {"B_KEY": "2", "A_KEY": "1"})
    )
    assert plist["EnvironmentVariables"] == {"A_KEY": "1", "B_KEY": "2"}


def test_systemd_unit_content_and_ordering() -> None:
    units = _units()
    identity = supervise.render_systemd(units["identity"])
    assert identity == supervise.render_systemd(units["identity"])
    assert "Restart=on-failure" in identity
    assert "RestartSec=3" in identity
    assert "After=com.agent-economy.db-gateway.service" in identity
    assert "WantedBy=default.target" in identity
    assert f"WorkingDirectory={REPO_ROOT / 'services' / 'identity'}" in identity
    assert "After=" not in supervise.render_systemd(units["db-gateway"])
    board = supervise.render_systemd(units["task-board"])
    assert "com.agent-economy.identity.service" in board
    court = supervise.render_systemd(units["court"])
    if units["court"].env_file is not None:
        assert f"EnvironmentFile={units['court'].env_file}" in court


def test_systemd_quotes_paths_with_spaces() -> None:
    unit = supervise.Unit(
        name="x",
        working_dir=Path("/a b"),
        argv=("/opt/my uv/uv", "run"),
        after=(),
        stdout_path=Path("/l/o"),
        stderr_path=Path("/l/e"),
        env_file=None,
    )
    assert 'ExecStart="/opt/my uv/uv" run' in supervise.render_systemd(unit)


def test_read_env_file_skips_comments_and_empty_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('# c\n\nA=1\nB=\nC="quoted"\n')
    assert supervise.read_env_file(env) == {"A": "1", "C": "quoted"}


def test_install_status_uninstall_round_trip_without_loading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    units = supervise.build_units(REPO_ROOT, UV)
    unit_dir = tmp_path / "units"
    log_dir_units = [
        supervise.Unit(
            name=u.name,
            working_dir=u.working_dir,
            argv=u.argv,
            after=u.after,
            stdout_path=tmp_path / "logs" / f"{u.name}.out",
            stderr_path=tmp_path / "logs" / f"{u.name}.err",
            env_file=None,
        )
        for u in units
    ]
    assert supervise.status(log_dir_units, unit_dir, "linux") == len(units)
    assert supervise.install(log_dir_units, unit_dir, "linux", load=False) == 0
    assert len(list(unit_dir.glob("*.service"))) == len(units)
    assert supervise.uninstall(log_dir_units, unit_dir, "linux", load=False) == 0
    assert list(unit_dir.glob("*.service")) == []
    capsys.readouterr()
