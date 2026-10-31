"""Unit tests for treasury_provision_cli.config."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from treasury_provision_cli.config import TreasuryConfig, load_treasury_settings

REAL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"


@pytest.mark.unit
class TestTreasuryConfig:
    def test_creates_config(self) -> None:
        config = TreasuryConfig(
            handle="operator",
            genesis_amount=1000000,
            genesis_reference="treasury_genesis",
        )
        assert config.handle == "operator"
        assert config.genesis_amount == 1000000
        assert config.genesis_reference == "treasury_genesis"

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            TreasuryConfig(
                handle="operator",
                genesis_amount=1000000,
                genesis_reference="treasury_genesis",
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_rejects_missing_field(self) -> None:
        with pytest.raises(ValidationError):
            TreasuryConfig(handle="operator", genesis_amount=1000000)  # type: ignore[call-arg]


@pytest.mark.unit
class TestLoadTreasurySettings:
    def test_loads_from_real_agents_config(self) -> None:
        """The real agents/config.yaml must carry a valid treasury section."""
        settings = load_treasury_settings(config_path=REAL_CONFIG_PATH)
        assert settings.handle == "operator"
        assert settings.genesis_amount > 0
        assert settings.genesis_reference != ""

    def test_missing_treasury_section_raises(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("platform:\n  identity_url: http://localhost:8001\n")
        with pytest.raises(ValidationError):
            load_treasury_settings(config_path=config_path)
