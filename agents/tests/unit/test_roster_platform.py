"""Unit tests for platform agent roster entry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.mark.unit
class TestRosterPlatformEntry:
    """Tests for platform agent in roster."""

    def test_roster_has_platform_entry(self) -> None:
        roster_path = Path(__file__).resolve().parents[2] / "roster.yaml"
        roster = yaml.safe_load(roster_path.read_text())

        assert "platform" in roster["agents"]
        assert roster["agents"]["platform"]["name"] == "Platform"
        assert roster["agents"]["platform"]["type"] == "platform"
