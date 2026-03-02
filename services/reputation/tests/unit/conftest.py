"""Unit test fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from reputation_service.config import clear_settings_cache, get_settings
from reputation_service.core.state import get_app_state, reset_app_state
from tests.fakes.sqlite_feedback_store import SqliteFeedbackStore

try:
    from tests.unit import test_persistence as persistence_tests
except Exception:
    persistence_tests = None

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Clear settings cache and app state between tests."""
    clear_settings_cache()
    reset_app_state()


@pytest.fixture(autouse=True)
def _patch_persistence_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Align persistence helpers with gateway-config + fake-store test pattern."""
    if persistence_tests is None:
        return

    original_write_config = persistence_tests._write_config

    def _write_config_with_gateway(
        tmp_path: Path,
        db_path: str,
        reveal_timeout: int = 86400,
    ) -> str:
        config_path = original_write_config(tmp_path, db_path, reveal_timeout)
        config_file = Path(config_path)
        content = config_file.read_text()
        if "db_gateway:" not in content:
            content += """
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
"""
            config_file.write_text(content)
        return config_path

    async def _make_client_with_fake_store(app: FastAPI) -> AsyncIterator[AsyncClient]:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client,
        ):
            state = get_app_state()
            state.feedback_store = SqliteFeedbackStore(db_path=get_settings().database.path)
            yield client

    monkeypatch.setattr(persistence_tests, "_write_config", _write_config_with_gateway)
    monkeypatch.setattr(persistence_tests, "_make_client", _make_client_with_fake_store)
