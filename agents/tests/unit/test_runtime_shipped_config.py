"""The shipped agent config satisfies the required journal startup contract."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from base_agent.runtime_config import RuntimeStateConfig, load_runtime_state_config

pytestmark = pytest.mark.unit


def test_shipped_runtime_config_loads_from_an_unrelated_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    monkeypatch.chdir(tmp_path)
    settings = load_runtime_state_config(config_path)
    assert settings.directory.is_absolute()
    assert settings.environment_id.strip()
    assert settings.diagnostic_limit > 0
    assert settings.max_record_bytes > 0


def test_filesystem_root_is_not_an_admissible_runtime_directory() -> None:
    with pytest.raises(ValidationError, match="directory"):
        RuntimeStateConfig(
            environment_id="root-admission-proof",
            directory=Path("/"),
            diagnostic_limit=8,
            max_record_bytes=65536,
        )
