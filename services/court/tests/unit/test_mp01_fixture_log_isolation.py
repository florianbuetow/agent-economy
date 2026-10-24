"""Prove Court test startup configurations use owned log directories."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from tests.integration import conftest as integration_fixtures
from tests.unit.routers import conftest as router_fixtures

pytestmark = pytest.mark.unit


class _ConfigurationCaptured(Exception):
    """Stop a fixture before application startup and logging initialization."""


def _read_configuration(path: str | Path) -> dict[str, Any]:
    return dict(yaml.safe_load(Path(path).read_text()))


def test_router_fixture_uses_owned_court_log_directory(tmp_path: Path) -> None:
    configuration = _read_configuration(router_fixtures._valid_config(tmp_path))

    assert Path(configuration["logging"]["directory"]) == tmp_path / "logs" / "court"


def test_integration_gateway_fixture_uses_owned_gateway_log_directory(
    tmp_path: Path,
) -> None:
    config_path = Path(integration_fixtures._write_gateway_config(str(tmp_path / "gateway.db")))
    try:
        configuration = _read_configuration(config_path)
    finally:
        config_path.unlink()

    assert Path(configuration["logging"]["directory"]) == (tmp_path / "logs" / "db-gateway")


@pytest.mark.asyncio
async def test_integration_court_fixture_uses_owned_court_log_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def capture_configuration() -> None:
        captured.append(_read_configuration(os.environ["CONFIG_PATH"]))
        raise _ConfigurationCaptured

    monkeypatch.setattr(integration_fixtures, "create_app", capture_configuration)
    previous_path = os.environ.get("CONFIG_PATH")
    fixture = integration_fixtures.app.__wrapped__
    generator = fixture(tmp_path, SimpleNamespace(base_url="http://127.0.0.1:1"), monkeypatch)
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
    assert Path(captured[0]["logging"]["directory"]) == tmp_path / "logs" / "court"
