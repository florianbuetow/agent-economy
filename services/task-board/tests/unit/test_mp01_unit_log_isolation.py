"""Prove Task Board unit-test configurations use owned log directories."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit import test_deadline_evaluation_config as deadline_config_tests
from tests.unit.routers import conftest as router_fixtures

pytestmark = pytest.mark.unit


class _ConfigurationCaptured(Exception):
    """Stop the fixture before application startup and logging initialization."""


@pytest.mark.asyncio
async def test_router_fixture_uses_owned_board_log_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def capture_configuration() -> None:
        captured.append(dict(yaml.safe_load(Path(os.environ["CONFIG_PATH"]).read_text())))
        raise _ConfigurationCaptured

    monkeypatch.setattr(router_fixtures, "create_app", capture_configuration)
    previous_path = os.environ.get("CONFIG_PATH")
    fixture = router_fixtures.app.__wrapped__
    generator = fixture(
        tmp_path,
        monkeypatch,
        router_fixtures.platform_keypair.__wrapped__(tmp_path),
        router_fixtures.generate_keypair(),
        router_fixtures.generate_keypair(),
        router_fixtures.generate_keypair(),
    )
    try:
        with pytest.raises(_ConfigurationCaptured):
            await anext(generator)
    finally:
        await generator.aclose()
        if previous_path is None:
            os.environ.pop("CONFIG_PATH", None)
        else:
            os.environ["CONFIG_PATH"] = previous_path

    assert len(captured) == 1
    assert Path(captured[0]["logging"]["directory"]) == (tmp_path / "logs" / "task-board")


def test_deadline_config_uses_owned_board_log_directory(tmp_path: Path) -> None:
    configuration = yaml.safe_load(
        deadline_config_tests._BASE_CONFIG.format(
            db_path=str(tmp_path / "task-board.db"),
            assets_path=str(tmp_path / "assets"),
            agent_config_path="agents.yaml",
            logs_path=str(tmp_path / "logs" / "task-board"),
            deadline_evaluation_section=(
                "deadline_evaluation:\n"
                "  evaluation_interval_seconds: 30\n"
                "  actor_id: system-task-board-deadline\n"
            ),
        )
    )

    assert Path(configuration["logging"]["directory"]) == (tmp_path / "logs" / "task-board")
