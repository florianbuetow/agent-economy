"""CI recipe sandboxes leave normal tool dispatch unchanged."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.runtime.test_ci_readonly import _ci_sandbox, _run_recipe

pytestmark = pytest.mark.integration


def _available_tool_entries(tool: str) -> tuple[str, ...]:
    path = os.environ.get("PATH")
    assert path is not None
    entries: list[str] = []
    for directory in path.split(os.pathsep):
        candidate = Path(directory) / tool
        if candidate.is_file() and os.access(candidate, os.X_OK):
            entry = str(candidate.absolute())
            if entry not in entries:
                entries.append(entry)
    assert entries
    return tuple(entries)


def test_ci_sandbox_does_not_interpose_path_tools(tmp_path: Path) -> None:
    original_path = os.environ.get("PATH")
    assert original_path is not None

    sandbox, env = _ci_sandbox(tmp_path, "identity")

    assert env.get("PATH") == original_path
    assert not (sandbox / "tool-bin").exists()


@pytest.mark.parametrize("just_entry", _available_tool_entries("just"))
def test_ci_recipe_runs_through_each_available_just_entry(
    tmp_path: Path,
    just_entry: str,
) -> None:
    sandbox, env = _ci_sandbox(tmp_path, "identity")
    source = sandbox / "sample.py"
    original = b"value={'answer':[1,2,3]}\n"
    source.write_bytes(original)

    result = _run_recipe(sandbox, env, "ci", just_entry)

    assert result.returncode != 0, result.stdout + result.stderr
    assert source.read_bytes() == original
