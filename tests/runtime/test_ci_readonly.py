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
NOOP_CI_RECIPES = (
    "init",
    "test-prerequisites",
    "code-typecheck",
    "code-lspchecks",
    "code-security",
    "code-deptry",
    "code-spell",
    "code-semgrep",
    "code-audit",
    "test-unit",
    "test-architecture",
)


@pytest.fixture
def just_executable() -> str:
    executable = shutil.which("just")
    assert executable is not None
    return executable


def _ci_sandbox(tmp_path: Path, service_name: str) -> tuple[Path, dict[str, str]]:
    service_root = REPO_ROOT / "services" / service_name
    sandbox = tmp_path / service_name
    sandbox.mkdir()
    shutil.copyfile(service_root / "justfile", sandbox / "justfile")
    shutil.copyfile(service_root / "config.yaml", sandbox / "config.yaml")
    justfile = sandbox / "justfile"
    override_recipes = "\n".join(f"{recipe}:\n    @:" for recipe in NOOP_CI_RECIPES)
    justfile.write_text(
        justfile.read_text(encoding="utf-8")
        + "\nset allow-duplicate-recipes\n\n"
        + override_recipes
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["UV_PROJECT"] = str(service_root)
    env["UV_FROZEN"] = "1"
    env["UV_NO_SYNC"] = "1"
    return sandbox, env


def _run_recipe(
    sandbox: Path,
    env: dict[str, str],
    recipe: str,
    just_executable: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (just_executable, "--justfile", str(sandbox / "justfile"), recipe),
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
    just_executable: str,
) -> None:
    sandbox, env = _ci_sandbox(tmp_path, service_name)
    source = sandbox / "sample.py"
    original = b"value={'answer':[1,2,3]}\n"
    source.write_bytes(original)

    result = _run_recipe(sandbox, env, recipe, just_executable)

    assert result.returncode != 0, result.stdout + result.stderr
    assert source.read_bytes() == original


@pytest.mark.parametrize("service_name", SERVICE_NAMES)
def test_explicit_formatter_remains_available(
    tmp_path: Path,
    service_name: str,
    just_executable: str,
) -> None:
    sandbox, env = _ci_sandbox(tmp_path, service_name)
    source = sandbox / "sample.py"
    original = b"value={'answer':[1,2,3]}\n"
    source.write_bytes(original)

    result = _run_recipe(sandbox, env, "code-format", just_executable)

    assert result.returncode == 0, result.stdout + result.stderr
    assert source.read_bytes() != original
    assert source.read_text(encoding="utf-8") == 'value = {"answer": [1, 2, 3]}\n'


@pytest.mark.parametrize("service_name", SERVICE_NAMES)
@pytest.mark.parametrize("recipe", CI_RECIPES)
def test_ci_accepts_already_formatted_source(
    tmp_path: Path,
    service_name: str,
    recipe: str,
    just_executable: str,
) -> None:
    sandbox, env = _ci_sandbox(tmp_path, service_name)
    source = sandbox / "sample.py"
    original = b'VALUE = {"answer": [1, 2, 3]}\n'
    source.write_bytes(original)

    result = _run_recipe(sandbox, env, recipe, just_executable)

    assert result.returncode == 0, result.stdout + result.stderr
    assert source.read_bytes() == original
