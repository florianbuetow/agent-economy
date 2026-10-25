"""Regression contract for concrete AssetManager lifespan cleanup ordering."""

from __future__ import annotations

import gc
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI

from task_board_service.core import lifespan as lifespan_module
from task_board_service.core.lifespan import lifespan
from task_board_service.core.state import get_app_state
from task_board_service.services.asset_manager import AssetManager
from tests.unit.test_mp06_maintenance_lock import _patch_dependencies

if TYPE_CHECKING:
    from pathlib import Path


class _RaisingTaskManager:
    def __init__(self, **_kwargs: object) -> None:
        pass

    def close(self) -> None:
        raise RuntimeError("synthetic task manager close failure")


@pytest.mark.unit
async def test_lifespan_closes_concrete_asset_manager_before_later_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asset root ownership is released even when TaskManager.close raises."""
    lock_path = tmp_path / "maintenance.lock"
    _patch_dependencies(monkeypatch, lock_path)
    monkeypatch.setattr(lifespan_module, "AssetManager", AssetManager)
    monkeypatch.setattr(lifespan_module, "TaskManager", _RaisingTaskManager)

    gc.collect()
    close_calls: list[AssetManager] = []
    original_close = AssetManager.close

    def close_spy(manager: AssetManager) -> None:
        close_calls.append(manager)
        original_close(manager)

    monkeypatch.setattr(AssetManager, "close", close_spy)
    concrete_manager: AssetManager | None = None
    try:
        with pytest.raises(RuntimeError, match="synthetic task manager close failure"):
            async with lifespan(FastAPI()):
                concrete_manager = get_app_state().asset_manager
                assert type(concrete_manager) is AssetManager

        assert concrete_manager is not None
        assert close_calls == [concrete_manager]
    finally:
        if concrete_manager is not None:
            original_close(concrete_manager)
