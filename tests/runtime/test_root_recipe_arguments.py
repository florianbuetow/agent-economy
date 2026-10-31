"""Root recipes treat user-supplied names as data, not shell source."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_SERVICES = (
    "identity",
    "central-bank",
    "task-board",
    "reputation",
    "court",
    "db-gateway",
    "ui",
)


def _run_just(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("just", *arguments),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("recipe", ["test", "ci-service"])
def test_service_recipe_rejects_shell_input_before_dispatch(tmp_path: Path, recipe: str) -> None:
    marker = tmp_path / "argument-was-executed"
    result = _run_just(recipe, f"missing; touch {shlex.quote(str(marker))}; false")

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert not marker.exists()
    assert "Unknown service" in output


@pytest.mark.parametrize("recipe", ["test", "ci-service"])
@pytest.mark.parametrize("service", _SERVICES)
def test_service_recipe_accepts_only_named_service_directories(recipe: str, service: str) -> None:
    result = _run_just("--dry-run", recipe, service)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert f"service='{service}'" in output
    assert 'cd "services/$service"' in output


def test_start_mathbot_quotes_profile_in_banner_and_command() -> None:
    justfile = (_ROOT / "justfile").read_text(encoding="utf-8")
    recipe = justfile[justfile.index('start-mathbot profile="mathbot":') :]
    recipe = recipe[: recipe.index("# Stop one exact math worker profile")]

    assert 'printf "\\033[0;34m=== Starting Math Worker Agent (profile: %s)' in recipe
    assert "{{quote(profile)}}" in recipe
    assert "-m math_worker {{quote(profile)}} &" in recipe


def test_stop_mathbot_quotes_profile_in_banner_and_command() -> None:
    justfile = (_ROOT / "justfile").read_text(encoding="utf-8")
    recipe = justfile[justfile.index('stop-mathbot profile="mathbot":') :]
    recipe = recipe[: recipe.index("# Fund the feeder agent")]

    assert 'printf "\\033[0;34m=== Stopping Math Worker Agent (profile: %s)' in recipe
    assert "{{quote(profile)}}" in recipe
    assert "--profile {{quote(profile)}}" in recipe
