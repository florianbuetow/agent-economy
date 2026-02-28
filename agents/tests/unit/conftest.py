"""Unit test fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from base_agent.config import Settings, clear_settings_cache


@pytest.fixture()
def tmp_keys_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for key storage."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    return keys_dir


@pytest.fixture()
def sample_roster(tmp_path: Path) -> Path:
    """Create a temporary roster file."""
    roster: dict[str, Any] = {
        "agents": {
            "testbot": {
                "name": "Test Bot",
                "type": "worker",
            },
        },
    }
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(yaml.dump(roster))
    return roster_path


@pytest.fixture()
def sample_settings(tmp_keys_dir: Path, sample_roster: Path) -> Settings:
    """Create a Settings object pointing at temporary paths."""
    return Settings(
        platform={
            "identity_url": "http://localhost:8001",
            "bank_url": "http://localhost:8002",
            "task_board_url": "http://localhost:8003",
            "reputation_url": "http://localhost:8004",
            "court_url": "http://localhost:8005",
        },
        data={
            "keys_dir": str(tmp_keys_dir),
            "roster_path": str(sample_roster),
        },
    )


@pytest.fixture(autouse=True)
def _clear_config_cache() -> None:
    """Clear config cache between tests."""
    clear_settings_cache()
