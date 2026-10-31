"""T-189/T-190: execute Git guards without committing or pushing."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
_HOOKS = Path(__file__).resolve().parents[2] / ".githooks"
_CLOCK_COMMAND = "stamp=$(TZ=Europe/Amsterdam date '+%u %H') || fail_clock"


def _write_clock(clock: Path, body: str) -> None:
    clock.write_text(
        '#!/bin/sh\n[ "$TZ" = "Europe/Amsterdam" ] || exit 99\n'
        '[ "$1" = "+%u %H" ] || exit 98\n'
        f"{body}\n"
    )
    clock.chmod(0o700)


def _copy_hook_with_clock(tmp_path: Path, hook: str, clock: Path) -> Path:
    source_bytes = {
        name: (_HOOKS / name).read_bytes()
        for name in ("pre-commit", "pre-push", "check-office-hours")
    }
    copied_hooks = tmp_path / "hooks"
    copied_hooks.mkdir()
    for name, contents in source_bytes.items():
        copied = copied_hooks / name
        copied.write_bytes(contents)
        copied.chmod(0o700)

    check = copied_hooks / "check-office-hours"
    check_text = check.read_text()
    assert check_text.count(_CLOCK_COMMAND) == 1
    check.write_text(
        check_text.replace(
            _CLOCK_COMMAND,
            (
                "stamp=$(TZ=Europe/Amsterdam "
                f"{shlex.quote(str(clock))} '+%u %H') || fail_clock"
            ),
        )
    )
    check.chmod(0o700)

    assert all(
        (_HOOKS / name).read_bytes() == contents
        for name, contents in source_bytes.items()
    )
    executable = copied_hooks / hook
    assert executable.is_file() and os.access(executable, os.X_OK)
    return executable


def _environment_with_clock(stamp: str) -> dict[str, str]:
    environment = {**os.environ, "TEST_CLOCK_VALUE": stamp}
    assert environment.get("PATH") == os.environ.get("PATH")
    return environment


@pytest.mark.parametrize("hook", ["pre-commit", "pre-push"])
@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("1 07", 0),
        ("1 08", 1),
        ("3 12", 1),
        ("5 17", 1),
        ("5 18", 0),
        ("5 23", 0),
        ("6 08", 0),
        ("6 17", 0),
        ("7 12", 0),
        ("1 00", 0),
        ("bad clock", 1),
        ("0 08", 1),
        ("1 24", 1),
        ("", 1),
    ],
)
def test_office_hours_guard(
    tmp_path: Path, hook: str, stamp: str, expected: int
) -> None:
    clock = tmp_path / "fake-clock"
    _write_clock(clock, 'printf "%s\\n" "$TEST_CLOCK_VALUE"')
    executable = _copy_hook_with_clock(tmp_path, hook, clock)
    result = subprocess.run(
        [str(executable)],
        cwd=tmp_path,
        env=_environment_with_clock(stamp),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == expected, result.stderr
    if expected:
        assert "Europe/Amsterdam" in result.stderr


@pytest.mark.parametrize("hook", ["pre-commit", "pre-push"])
def test_clock_command_failure_blocks_git_action(tmp_path: Path, hook: str) -> None:
    clock = tmp_path / "fake-clock"
    _write_clock(clock, "exit 7")
    executable = _copy_hook_with_clock(tmp_path, hook, clock)
    result = subprocess.run(
        [str(executable)],
        env=_environment_with_clock("unused"),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 1
    assert "Europe/Amsterdam" in result.stderr
