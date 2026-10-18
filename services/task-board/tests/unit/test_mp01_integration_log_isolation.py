"""Check fixture log ownership before any service can write a log file."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit
_fixtures = importlib.import_module("tests.integration.conftest")


class _ConfigurationCaptured(Exception):
    """Stop the fixture before service startup or logging initialization."""


def _gateway_configuration(tmp_path: Path) -> dict[str, Any]:
    factory = _fixtures.__dict__["_write_gateway_config"]
    path = Path(factory(str(tmp_path / "gateway.db")))
    try:
        return dict(yaml.safe_load(path.read_text()))
    finally:
        path.unlink()


def _assert_owned_log_path(configuration: dict[str, Any], tmp_path: Path) -> Path:
    directory = Path(configuration["logging"]["directory"])
    assert directory.is_absolute(), "Test log paths must not depend on the repository cwd"
    assert directory.is_relative_to(tmp_path), (
        "Test logs must stay within owned temporary resources"
    )
    return directory


def test_gateway_fixture_logs_are_owned_before_startup(tmp_path: Path) -> None:
    _assert_owned_log_path(_gateway_configuration(tmp_path), tmp_path)


@pytest.mark.asyncio
async def test_board_fixture_logs_are_owned_and_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def capture_configuration() -> None:
        captured.append(dict(yaml.safe_load(Path(os.environ["CONFIG_PATH"]).read_text())))
        raise _ConfigurationCaptured

    monkeypatch.setattr(_fixtures, "create_app", capture_configuration)
    previous_path = os.environ.get("CONFIG_PATH")
    fixture = _fixtures.__dict__["app"].__wrapped__
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
    board_logs = _assert_owned_log_path(captured[0], tmp_path)
    gateway_logs = _assert_owned_log_path(_gateway_configuration(tmp_path), tmp_path)
    assert board_logs != gateway_logs
