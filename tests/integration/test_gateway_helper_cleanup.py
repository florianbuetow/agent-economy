"""Isolation regressions for the in-process signed Gateway test helper."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db_gateway_service.config import clear_settings_cache, get_settings
from db_gateway_service.core.state import get_app_state, reset_app_state
from tests.integration import gateway_helpers

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class ExpectedContextError(RuntimeError):
    """Intentional test-body failure used to exercise async cleanup."""


def _track_temporary_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Path]:
    original_factory = tempfile.TemporaryDirectory
    created: list[Path] = []

    def tracking_factory(*args: object, **kwargs: object) -> tempfile.TemporaryDirectory[str]:
        temporary_directory = original_factory(*args, **kwargs)  # type: ignore[arg-type]
        created.append(Path(temporary_directory.name))
        return temporary_directory

    monkeypatch.setattr(gateway_helpers.tempfile, "TemporaryDirectory", tracking_factory)
    return created


def _configure_caller_environment(monkeypatch: pytest.MonkeyPatch) -> Path:
    config_path = Path(__file__).resolve().parents[2] / "services" / "db-gateway" / "config.yaml"
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    reset_app_state()
    clear_settings_cache()
    assert get_settings().service.name == "db-gateway"
    return config_path


def _assert_caller_environment_restored(config_path: Path, created: list[Path]) -> None:
    assert all(not path.exists() for path in created)
    assert get_settings().service.name == "db-gateway"
    assert get_settings().auth.max_lifetime_seconds == 300
    with pytest.raises(RuntimeError, match="Application state not initialized"):
        get_app_state()
    assert config_path.exists()


async def test_setup_failure_removes_keys_config_and_restores_globals(
    monkeypatch: pytest.MonkeyPatch,
    initialized_db: str,
) -> None:
    config_path = _configure_caller_environment(monkeypatch)
    created = _track_temporary_directories(monkeypatch)

    def fail_app_creation() -> None:
        raise ExpectedContextError("forced app construction failure")

    monkeypatch.setattr("db_gateway_service.app.create_app", fail_app_creation)

    with pytest.raises(ExpectedContextError, match="forced app construction failure"):
        gateway_helpers.create_gateway_client(initialized_db)

    _assert_caller_environment_restored(config_path, created)


async def test_context_exception_removes_keys_config_and_restores_globals(
    monkeypatch: pytest.MonkeyPatch,
    initialized_db: str,
) -> None:
    config_path = _configure_caller_environment(monkeypatch)
    created = _track_temporary_directories(monkeypatch)
    client = gateway_helpers.create_gateway_client(initialized_db)

    with pytest.raises(ExpectedContextError, match="forced test-body failure"):
        async with client:
            raise ExpectedContextError("forced test-body failure")

    _assert_caller_environment_restored(config_path, created)


async def test_closing_replaced_client_preserves_current_global_state(
    monkeypatch: pytest.MonkeyPatch,
    initialized_db: str,
    tmp_path: Path,
    schema_sql: str,
) -> None:
    _configure_caller_environment(monkeypatch)
    replacement_db = tmp_path / "replacement.db"
    connection = sqlite3.connect(replacement_db)
    connection.executescript(schema_sql)
    connection.close()
    first_client = gateway_helpers.create_gateway_client(initialized_db)
    first_state = get_app_state()
    second_client = gateway_helpers.create_gateway_client(str(replacement_db))
    second_state = get_app_state()
    assert second_state is not first_state

    await first_client.aclose()

    assert get_app_state() is second_state
    assert second_state.db_writer is not None
    second_state.db_writer.connection.execute("SELECT 1").fetchone()
    await second_client.aclose()


async def test_writer_close_failure_still_completes_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    initialized_db: str,
) -> None:
    config_path = _configure_caller_environment(monkeypatch)
    created = _track_temporary_directories(monkeypatch)
    client = gateway_helpers.create_gateway_client(initialized_db)
    state = get_app_state()
    assert state.db_writer is not None

    def fail_close() -> None:
        raise RuntimeError("forced writer close failure")

    monkeypatch.setattr(state.db_writer, "close", fail_close)
    try:
        with pytest.raises(RuntimeError, match="forced writer close failure"):
            await client.aclose()
        _assert_caller_environment_restored(config_path, created)
    finally:
        for path in created:
            shutil.rmtree(path, ignore_errors=True)


async def test_writer_close_cancellation_is_preserved_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    initialized_db: str,
) -> None:
    config_path = _configure_caller_environment(monkeypatch)
    created = _track_temporary_directories(monkeypatch)
    client = gateway_helpers.create_gateway_client(initialized_db)
    state = get_app_state()
    assert state.db_writer is not None

    def cancel_close() -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(state.db_writer, "close", cancel_close)
    try:
        with pytest.raises(asyncio.CancelledError):
            await client.aclose()
        _assert_caller_environment_restored(config_path, created)
    finally:
        for path in created:
            shutil.rmtree(path, ignore_errors=True)
