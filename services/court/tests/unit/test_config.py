"""Configuration and judge panel validation tests.

Covers: JUDGE-01 to JUDGE-05 from court-service-tests.md.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from court_service.config import Settings, clear_settings_cache, get_settings


def _write_config(tmp_path, panel_size: int, judge_count: int) -> str:
    """Write a config with the given panel_size and judge count."""
    judges_yaml = ""
    for i in range(judge_count):
        judges_yaml += f'\n    - id: "judge-{i}"\n      provider: "mock"\n      model: "test-model"'
    config_content = f"""\
service:
  name: "court"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8005
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
database:
  path: "{tmp_path / "test.db"}"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
platform:
  agent_id: "a-platform"
request:
  max_body_size: 1048576
disputes:
  rebuttal_deadline_seconds: 86400
  max_claim_length: 10000
  max_rebuttal_length: 10000
judges:
  panel_size: {panel_size}
  judges:{judges_yaml}
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return str(config_path)


@pytest.mark.unit
class TestJudgePanelConfig:
    """Judge panel startup validation tests."""

    def test_judge_01_even_panel_size_rejected(self, tmp_path) -> None:
        """JUDGE-01: Panel size must be odd (even size rejected at startup)."""
        config_path = _write_config(tmp_path, panel_size=2, judge_count=2)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        with pytest.raises(Exception):  # noqa: B017
            get_settings()

    def test_judge_02_panel_size_zero_rejected(self, tmp_path) -> None:
        """JUDGE-02: Panel size 0 rejected at startup."""
        config_path = _write_config(tmp_path, panel_size=0, judge_count=0)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        with pytest.raises(Exception):  # noqa: B017
            get_settings()

    def test_judge_03_negative_panel_size_rejected(self, tmp_path) -> None:
        """JUDGE-03: Panel size -1 rejected at startup."""
        config_path = _write_config(tmp_path, panel_size=-1, judge_count=0)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        with pytest.raises(Exception):  # noqa: B017
            get_settings()

    def test_judge_04_vote_count_equals_panel_size(self, tmp_path) -> None:
        """JUDGE-04: Each judge must cast exactly one vote (validated at config)."""
        config_path = _write_config(tmp_path, panel_size=1, judge_count=1)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        settings = get_settings()
        assert isinstance(settings, Settings)
        assert settings.judges.panel_size == 1
        assert len(settings.judges.judges) == 1

    def test_judge_05_panel_size_one_valid(self, tmp_path) -> None:
        """JUDGE-05: Panel size 1 is valid."""
        config_path = _write_config(tmp_path, panel_size=1, judge_count=1)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        settings = get_settings()
        assert settings.judges.panel_size == 1


@pytest.mark.unit
class TestConfigLoading:
    """Standard config loading tests."""

    def test_valid_config_loads(self, tmp_path) -> None:
        """Valid config loads without errors."""
        config_path = _write_config(tmp_path, panel_size=1, judge_count=1)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        settings = get_settings()
        assert settings.service.name == "court"
        assert settings.server.port == 8005
        assert settings.platform.agent_id == "a-platform"
        assert settings.disputes.rebuttal_deadline_seconds == 86400

    def test_extra_fields_rejected(self, tmp_path) -> None:
        """Config with extra fields causes validation error."""
        config_path = _write_config(tmp_path, panel_size=1, judge_count=1)
        with Path(config_path).open("a") as f:
            f.write("unknown_section:\n  key: value\n")
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        with pytest.raises(Exception):  # noqa: B017
            get_settings()
