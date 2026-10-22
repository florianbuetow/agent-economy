"""MP-05 source inventory follows the feeder's selected configuration file."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scripts.recovery_fields import configuration_fields
from service_commons.recovery_contract import PathResolutionBase

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("absolute", [False, True])
def test_source_capture_outside_package_matches_configured_feeder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, absolute: bool
) -> None:
    config_directory = tmp_path / "external-config"
    source_directory = config_directory / "source"
    source_directory.mkdir(parents=True)
    expected_source = source_directory / "tasks.jsonl"
    expected_source.write_text('{"title":"external source"}\n', encoding="utf-8")
    unrelated_cwd = tmp_path / "unrelated-command"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    config = yaml.safe_load((REPO_ROOT / "agents/config.yaml").read_bytes())
    configured = str(expected_source) if absolute else "source/tasks.jsonl"
    config["task_feeder"]["tasks_file"] = configured

    fields = configuration_fields(
        "agents",
        config,
        config_path=config_directory / "agents.yaml",
        process_cwd=unrelated_cwd,
        agents_package_root=REPO_ROOT / "agents",
    )
    sources = [item for item in fields if item.field_path == ("task_feeder", "tasks_file")]

    assert len(sources) == 1
    assert sources[0].captured_value == configured
    assert sources[0].resolved_source_path == expected_source
    assert sources[0].resolution_base is (
        PathResolutionBase.ABSOLUTE if absolute else PathResolutionBase.CONFIG_PARENT
    )
    assert sources[0].resolved_source_path.read_bytes() == expected_source.read_bytes()
