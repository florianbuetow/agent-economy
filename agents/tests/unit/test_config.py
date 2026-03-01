"""Unit tests for configuration loading."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from base_agent.config import Settings


@pytest.mark.unit
class TestSettings:
    """Tests for the Settings model."""

    def test_valid_settings(self, sample_settings: Settings) -> None:
        assert sample_settings.platform.identity_url == "http://localhost:8001"
        assert sample_settings.data.keys_dir is not None

    def test_missing_platform_raises(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                data={"keys_dir": "/tmp/keys", "roster_path": "roster.yaml"},
            )

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                platform={
                    "identity_url": "http://localhost:8001",
                    "bank_url": "http://localhost:8002",
                    "task_board_url": "http://localhost:8003",
                    "reputation_url": "http://localhost:8004",
                    "court_url": "http://localhost:8005",
                    "unknown_field": "bad",
                },
                data={"keys_dir": "/tmp/keys", "roster_path": "roster.yaml"},
            )
