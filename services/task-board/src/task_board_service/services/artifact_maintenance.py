"""Startup and periodic artifact storage recovery plus terminal-artifact archive."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from task_board_service.logging import get_logger

if TYPE_CHECKING:
    from task_board_service.services.asset_manager import AssetManager


class ArtifactMaintenance:
    """Reconcile crash leftovers, then archive terminal artifacts (never purges)."""

    def __init__(
        self,
        asset_manager: AssetManager,
        *,
        stale_file_seconds: int,
        archive_after_seconds: int,
        archive_batch_size: int,
    ) -> None:
        self._assets = asset_manager
        self._stale_file_seconds = stale_file_seconds
        self._archive_after_seconds = archive_after_seconds
        self._archive_batch_size = archive_batch_size

    def run_once(self, *, startup: bool) -> dict[str, int]:
        """One sweep; at startup every stray staging/unreferenced live file is removed."""
        counts = self._assets.recover_storage(
            now_us=time.time_ns() // 1_000,
            stale_staging_seconds=None if startup else self._stale_file_seconds,
        )
        counts["archived"] = self._assets.archive_terminal_artifacts(
            archive_after_seconds=self._archive_after_seconds,
            limit=self._archive_batch_size,
        )
        return counts

    async def run_periodic(self, interval_seconds: float) -> None:
        """Run the startup sweep, then repeat; a failed cycle is logged and retried."""
        logger = get_logger(__name__)
        startup = True
        while True:
            try:
                counts = await asyncio.to_thread(self.run_once, startup=startup)
                if any(counts.values()):
                    logger.info("Artifact maintenance cycle", extra=counts)
            except Exception:
                logger.exception("Artifact maintenance cycle failed")
            # Only the very first cycle may treat all leftovers as stray: once the
            # service accepts uploads, removal is age-gated even if startup failed.
            startup = False
            await asyncio.sleep(interval_seconds)


__all__ = ["ArtifactMaintenance"]
