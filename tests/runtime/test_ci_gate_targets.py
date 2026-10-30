"""T-213/T-214: the commit hook gates only the components touched by pending paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci_gate_targets.py"


def _targets(paths: list[str]) -> list[str]:
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script path
        [sys.executable, str(SCRIPT)],
        input="\n".join(paths) + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in completed.stdout.splitlines() if line]


@pytest.mark.integration
def test_documentation_only_changes_need_no_gate() -> None:
    assert _targets(["docs/plans/x.md", "README.md", "openspec/specs/a/spec.md"]) == []


@pytest.mark.integration
def test_component_paths_map_to_their_component_once() -> None:
    assert _targets(
        [
            "services/task-board/src/task_board_service/app.py",
            "services/task-board/tests/unit/test_x.py",
            "libs/service-auth/src/service_auth/jws.py",
            "agents/src/math_worker/loop.py",
            "tools/src/demo_replay/__main__.py",
        ]
    ) == ["libs/service-auth", "services/task-board", "agents", "tools"]


@pytest.mark.integration
def test_root_level_code_maps_to_structure_gate() -> None:
    assert _targets(["justfile", "scripts/stop_agent.py", "tests/runtime/test_a.py"]) == ["."]


@pytest.mark.integration
def test_rename_entries_use_the_destination_path() -> None:
    assert _targets(["services/ui/old.py -> services/ui/new.py"]) == ["services/ui"]
