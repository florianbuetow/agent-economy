"""Identity startup fixtures must bind logs to their owned temporary roots."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit


class _ConfigurationCaptured(Exception):
    """Stop a fixture after its service YAML exists but before application startup."""


def _assert_owned(configuration: dict[str, Any], tmp_path: Path) -> None:
    directory = Path(configuration["logging"]["directory"])
    assert directory.is_absolute(), "Fixture logs must not depend on the repository cwd"
    assert directory.is_relative_to(tmp_path), "Fixture logs must stay under tmp_path"
    assert directory == tmp_path / "logs" / "identity"


def _restore_config_path(previous: str | None, fixtures: Any) -> None:
    if previous is None:
        os.environ.pop("CONFIG_PATH", None)
    else:
        os.environ["CONFIG_PATH"] = previous
    fixtures.clear_settings_cache()
    fixtures.reset_app_state()


@pytest.mark.asyncio
async def test_router_fixture_owns_identity_logs_before_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = importlib.import_module("tests.unit.routers.conftest")
    captured: list[dict[str, Any]] = []

    def capture() -> None:
        captured.append(dict(yaml.safe_load(Path(os.environ["CONFIG_PATH"]).read_text())))
        raise _ConfigurationCaptured

    monkeypatch.setattr(fixtures, "create_app", capture)
    previous = os.environ.get("CONFIG_PATH")
    generator = fixtures.__dict__["app"].__wrapped__(tmp_path)
    try:
        with pytest.raises(_ConfigurationCaptured):
            await anext(generator)
    finally:
        await generator.aclose()
        _restore_config_path(previous, fixtures)
    assert len(captured) == 1
    _assert_owned(captured[0], tmp_path)


@pytest.mark.asyncio
async def test_integration_fixture_owns_identity_logs_before_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixtures = importlib.import_module("tests.integration.conftest")
    captured: list[dict[str, Any]] = []

    async def no_gateway_start(_db_path: str) -> tuple[object, object]:
        return object(), SimpleNamespace(close=lambda: None)

    def capture() -> None:
        captured.append(dict(yaml.safe_load(Path(os.environ["CONFIG_PATH"]).read_text())))
        raise _ConfigurationCaptured

    monkeypatch.setattr(fixtures, "_build_gateway_app", no_gateway_start)
    monkeypatch.setattr(fixtures, "create_app", capture)
    previous = os.environ.get("CONFIG_PATH")
    generator = fixtures.__dict__["app"].__wrapped__(
        tmp_path,
        str(tmp_path / "gateway.db"),
    )
    try:
        with pytest.raises(_ConfigurationCaptured):
            await anext(generator)
    finally:
        await generator.aclose()
        _restore_config_path(previous, fixtures)
    assert len(captured) == 1
    _assert_owned(captured[0], tmp_path)
