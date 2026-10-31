"""Unit tests for demo_replay.config (GAP-E11 — kill hardcoded URLs)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from demo_replay.config import PlatformConfig, load_platform_settings

VALID_CONFIG_YAML = """\
platform:
  identity_url: "http://localhost:8001"
  bank_url: "http://localhost:8002"
  task_board_url: "http://localhost:8003"
  reputation_url: "http://localhost:8004"
  court_url: "http://localhost:8005"
  platform_key_path: "keys/platform.key"
  token_ttl_seconds: 300
"""


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


@pytest.mark.unit
class TestPlatformConfig:
    def test_creates_config(self) -> None:
        config = PlatformConfig(
            identity_url="http://localhost:8001",
            bank_url="http://localhost:8002",
            task_board_url="http://localhost:8003",
            reputation_url="http://localhost:8004",
            court_url="http://localhost:8005",
            platform_key_path=Path("keys/platform.key"),
            token_ttl_seconds=300,
        )
        assert config.identity_url == "http://localhost:8001"
        assert config.court_url == "http://localhost:8005"

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            PlatformConfig(
                identity_url="http://localhost:8001",
                bank_url="http://localhost:8002",
                task_board_url="http://localhost:8003",
                reputation_url="http://localhost:8004",
                court_url="http://localhost:8005",
                platform_key_path=Path("keys/platform.key"),
                token_ttl_seconds=300,
                unknown_field="oops",  # type: ignore[call-arg]
            )

    def test_rejects_missing_field(self) -> None:
        with pytest.raises(ValidationError):
            PlatformConfig(
                identity_url="http://localhost:8001",
                bank_url="http://localhost:8002",
                task_board_url="http://localhost:8003",
                reputation_url="http://localhost:8004",
                platform_key_path=Path("keys/platform.key"),
                token_ttl_seconds=300,
            )  # type: ignore[call-arg]

    @pytest.mark.parametrize("invalid_ttl", [None, True, 0, -1])
    def test_rejects_invalid_token_ttl(self, invalid_ttl: object) -> None:
        with pytest.raises(ValidationError, match="token_ttl_seconds"):
            PlatformConfig(
                identity_url="http://localhost:8001",
                bank_url="http://localhost:8002",
                task_board_url="http://localhost:8003",
                reputation_url="http://localhost:8004",
                court_url="http://localhost:8005",
                platform_key_path=Path("keys/platform.key"),
                token_ttl_seconds=invalid_ttl,  # type: ignore[arg-type]
            )


@pytest.mark.unit
class TestLoadPlatformSettings:
    def test_loads_urls_from_yaml(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, VALID_CONFIG_YAML)
        config = load_platform_settings(path)
        assert config.identity_url == "http://localhost:8001"
        assert config.bank_url == "http://localhost:8002"
        assert config.task_board_url == "http://localhost:8003"
        assert config.reputation_url == "http://localhost:8004"
        assert config.court_url == "http://localhost:8005"

    def test_missing_platform_section_fails_fast(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, "not_platform:\n  key: value\n")
        with pytest.raises(ValidationError):
            load_platform_settings(path)

    def test_missing_url_key_fails_fast(self, tmp_path: Path) -> None:
        body = VALID_CONFIG_YAML.replace('  court_url: "http://localhost:8005"\n', "")
        path = _write_config(tmp_path, body)
        with pytest.raises(ValidationError):
            load_platform_settings(path)

    def test_non_mapping_file_raises_value_error(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, "- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="Invalid config file"):
            load_platform_settings(path)
