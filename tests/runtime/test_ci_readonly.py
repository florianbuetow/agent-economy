"""Service CI recipes validate source files without rewriting them."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_NAMES = (
    "identity",
    "central-bank",
    "task-board",
    "reputation",
    "court",
    "db-gateway",
    "ui",
)
CI_RECIPES = ("ci", "ci-quiet")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _ci_sandbox(tmp_path: Path, service_name: str) -> tuple[Path, dict[str, str]]:
    service_root = REPO_ROOT / "services" / service_name
    sandbox = tmp_path / service_name
    sandbox.mkdir()
    shutil.copyfile(service_root / "justfile", sandbox / "justfile")
    shutil.copyfile(service_root / "config.yaml", sandbox / "config.yaml")

    real_just = shutil.which("just")
    real_uv = shutil.which("uv")
    assert real_just is not None
    assert real_uv is not None

    tool_bin = sandbox / "tool-bin"
    tool_bin.mkdir()
    _write_executable(
        tool_bin / "just",
        "\n".join(
            (
                "#!/bin/sh",
                'case "$1" in',
                "  code-format|code-style)",
                f'    exec "{real_just}" --justfile "$PWD/justfile" "$1"',
                "    ;;",
                "  *) exit 0 ;;",
                "esac",
                "",
            )
        ),
    )
    _write_executable(
        tool_bin / "uv",
        "\n".join(
            (
                "#!/bin/sh",
                'test "$1" = "run" || exit 97',
                "shift",
                f'exec "{real_uv}" run --project "{service_root}" --frozen --no-sync "$@"',
                "",
            )
        ),
    )
    env = os.environ.copy()
    env["PATH"] = f"{tool_bin}{os.pathsep}{env['PATH']}"
    return sandbox, env


def _run_recipe(
    sandbox: Path,
    env: dict[str, str],
    recipe: str,
) -> subprocess.CompletedProcess[str]:
    real_just = shutil.which("just")
    assert real_just is not None
    return subprocess.run(
        (real_just, "--justfile", str(sandbox / "justfile"), recipe),
        cwd=sandbox,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("service_name", SERVICE_NAMES)
@pytest.mark.parametrize("recipe", CI_RECIPES)
def test_ci_rejects_formatting_defects_without_rewriting_source(
    tmp_path: Path,
    service_name: str,
    recipe: str,
) -> None:
    sandbox, env = _ci_sandbox(tmp_path, service_name)
    source = sandbox / "sample.py"
    original = b"value={'answer':[1,2,3]}\n"
    source.write_bytes(original)

    result = _run_recipe(sandbox, env, recipe)

    assert result.returncode != 0, result.stdout + result.stderr
    assert source.read_bytes() == original


@pytest.mark.parametrize("service_name", SERVICE_NAMES)
def test_explicit_formatter_remains_available(tmp_path: Path, service_name: str) -> None:
    sandbox, env = _ci_sandbox(tmp_path, service_name)
    source = sandbox / "sample.py"
    original = b"value={'answer':[1,2,3]}\n"
    source.write_bytes(original)

    result = _run_recipe(sandbox, env, "code-format")

    assert result.returncode == 0, result.stdout + result.stderr
    assert source.read_bytes() != original
    assert source.read_text(encoding="utf-8") == 'value = {"answer": [1, 2, 3]}\n'


@pytest.mark.parametrize("service_name", SERVICE_NAMES)
@pytest.mark.parametrize("recipe", CI_RECIPES)
def test_ci_accepts_already_formatted_source(
    tmp_path: Path,
    service_name: str,
    recipe: str,
) -> None:
    sandbox, env = _ci_sandbox(tmp_path, service_name)
    source = sandbox / "sample.py"
    original = b'VALUE = {"answer": [1, 2, 3]}\n'
    source.write_bytes(original)

    result = _run_recipe(sandbox, env, recipe)

    assert result.returncode == 0, result.stdout + result.stderr
    assert source.read_bytes() == original
