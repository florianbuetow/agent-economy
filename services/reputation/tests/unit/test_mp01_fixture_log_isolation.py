"""Reputation startup fixtures must bind logs to owned temporary roots."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit


class _ConfigurationCaptured(Exception):
    """Stop a configuration test before application startup."""


def _assert_owned(configuration: dict[str, Any], tmp_path: Path) -> None:
    directory = Path(configuration["logging"]["directory"])
    assert directory.is_absolute(), "Fixture logs must not depend on the repository cwd"
    assert directory.is_relative_to(tmp_path), "Fixture logs must stay under tmp_path"
    assert directory == tmp_path / "logs" / "reputation"


def _restore_config_path(previous: str | None, fixtures: Any) -> None:
    if previous is None:
        os.environ.pop("CONFIG_PATH", None)
    else:
        os.environ["CONFIG_PATH"] = previous
    fixtures.clear_settings_cache()
    fixtures.reset_app_state()


@pytest.mark.parametrize(
    "module_name",
    ["tests.unit.routers.conftest", "tests.integration.conftest"],
)
def test_service_fixture_owns_reputation_logs_before_startup(
    module_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = importlib.import_module(module_name)
    previous = os.environ.get("CONFIG_PATH")
    generator = fixtures.__dict__["_isolate_test"].__wrapped__(tmp_path, monkeypatch)
    try:
        next(generator)
        configuration = dict(yaml.safe_load(Path(os.environ["CONFIG_PATH"]).read_text()))
    finally:
        generator.close()
        _restore_config_path(previous, fixtures)
    _assert_owned(configuration, tmp_path)


def test_persistence_config_writer_owns_reputation_logs(tmp_path: Path) -> None:
    module = importlib.import_module("tests.unit.test_persistence")
    path = Path(module._write_config(tmp_path, str(tmp_path / "feedback.db")))
    _assert_owned(dict(yaml.safe_load(path.read_text())), tmp_path)


@pytest.mark.asyncio
async def test_cfg01_actual_startup_config_owns_reputation_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("tests.unit.test_persistence")
    captured: list[dict[str, Any]] = []

    def capture() -> None:
        captured.append(dict(yaml.safe_load(Path(os.environ["CONFIG_PATH"]).read_text())))
        raise _ConfigurationCaptured

    monkeypatch.setattr(module, "create_app", capture)
    previous = os.environ.get("CONFIG_PATH")
    try:
        with pytest.raises(_ConfigurationCaptured):
            await module.test_cfg_01_service_starts_with_valid_db_gateway(tmp_path)
    finally:
        _restore_config_path(previous, module)
    assert len(captured) == 1
    _assert_owned(captured[0], tmp_path)
