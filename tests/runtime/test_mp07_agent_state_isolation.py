"""Generated runtimes own their agent journal configuration before startup."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts import isolated_test_runtime
from scripts.isolated_test_runtime import IsolatedRuntime

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_generated_agent_journals_have_distinct_owned_paths_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_loader = isolated_test_runtime._load_yaml
    ordinary_state = tmp_path / "ordinary-state"
    source_settings = {
        "environment_id": "ordinary-environment",
        "directory": str(ordinary_state),
        "diagnostic_limit": 11,
        "max_record_bytes": 98304,
    }

    def load_source_config(path: Path) -> dict[str, Any]:
        config = original_loader(path)
        if path == REPO_ROOT / "agents/config.yaml":
            config["runtime_state"] = dict(source_settings)
        return config

    monkeypatch.setattr(isolated_test_runtime, "_load_yaml", load_source_config)
    observed: list[tuple[Path, str]] = []
    with ExitStack() as cleanup:
        for _ in range(2):
            runtime = IsolatedRuntime.create(
                repo_root=REPO_ROOT, artifacts_root=tmp_path, services=("db-gateway", "identity")
            )
            cleanup.callback(runtime.stop)
            config = yaml.safe_load(runtime.agent_config_path.read_text(encoding="utf-8"))
            state = config["runtime_state"]
            directory = Path(state["directory"])
            assert directory.is_absolute()
            assert directory.is_relative_to(runtime.run_dir)
            assert directory != ordinary_state
            assert state["environment_id"] == runtime.run_id
            assert state["diagnostic_limit"] == 11
            assert state["max_record_bytes"] == 98304
            observed.append((directory, state["environment_id"]))
    assert observed[0][0] != observed[1][0]
    assert observed[0][1] != observed[1][1]
    assert not ordinary_state.exists()
