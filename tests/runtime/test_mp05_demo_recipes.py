"""Demo entry points preserve the stack and expose explicit resume commands."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[2]
_RUN_ID = "run-00000000-0000-4000-8000-000000000591"


@pytest.mark.parametrize("recipe", ["demo", "demo-scale"])
def test_demo_recipe_runs_without_identity_or_runtime_reset(recipe: str) -> None:
    result = subprocess.run(
        ["just", "--dry-run", recipe], cwd=_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    command = result.stdout + result.stderr
    assert "--run-id" not in command
    for forbidden in ("rm ", "stop-all", "start-all", "lsof", "read -r"):
        assert forbidden not in command


@pytest.mark.parametrize(
    ("recipe", "scenario"),
    [("demo-resume", "quick"), ("demo-scale-resume", "scale")],
)
def test_demo_resume_recipe_preserves_existing_runtime(recipe: str, scenario: str) -> None:
    result = subprocess.run(
        ["just", "--dry-run", recipe, _RUN_ID],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    command = result.stdout + result.stderr
    assert f"scenarios/{scenario}.yaml" in command
    assert f"--run-id {_RUN_ID}" in command or f"--run-id '{_RUN_ID}'" in command
    for forbidden in ("rm ", "stop-all", "start-all", "lsof", "read -r"):
        assert forbidden not in command
