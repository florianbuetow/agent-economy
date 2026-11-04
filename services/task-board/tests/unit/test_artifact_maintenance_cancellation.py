"""Artifact maintenance must finish its storage call before shutdown continues."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from task_board_service.services.artifact_maintenance import ArtifactMaintenance

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _HeldAssets:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def recover_storage(self, **_: Any) -> dict[str, int]:
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test did not release artifact maintenance")
        return {"removed": 0}

    def archive_terminal_artifacts(self, **_: Any) -> int:
        return 0


async def test_cancellation_drains_in_flight_artifact_storage_work() -> None:
    assets = _HeldAssets()
    maintenance = ArtifactMaintenance(
        assets,  # type: ignore[arg-type]
        stale_file_seconds=1,
        archive_after_seconds=1,
        archive_batch_size=1,
    )
    task = asyncio.create_task(maintenance.run_periodic(interval_seconds=60))
    assert await asyncio.to_thread(assets.entered.wait, 0.5)

    task.cancel()
    await asyncio.sleep(0)
    try:
        assert not task.done()
    finally:
        assets.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
