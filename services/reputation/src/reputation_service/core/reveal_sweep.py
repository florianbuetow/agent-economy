"""Background sweep that durably reveals timed-out sealed feedback (G11)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from reputation_service.logging import get_logger
from reputation_service.services.feedback import reveal_timed_out_feedback

if TYPE_CHECKING:
    from reputation_service.core.state import AppState


async def run_reveal_sweep_once(state: AppState) -> int:
    """Reveal every sealed row past the timeout; return how many were revealed."""
    store = state.feedback_store
    if store is None:
        msg = "Feedback store not initialized"
        raise RuntimeError(msg)
    return await asyncio.to_thread(
        reveal_timed_out_feedback,
        store,
        state.feedback_reveal_timeout_seconds,
    )


async def reveal_sweep_loop(state: AppState, interval_seconds: float) -> None:
    """Run the sweep forever, once per ``interval_seconds``.

    A failed pass is logged and retried on the next tick; the reveal is idempotent
    because the Gateway flips and emits inside one transaction.
    """
    logger = get_logger(__name__)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            revealed = await run_reveal_sweep_once(state)
        except Exception:
            logger.exception("Timed feedback reveal sweep failed")
        else:
            if revealed:
                logger.info("Timed feedback revealed", extra={"revealed": revealed})
