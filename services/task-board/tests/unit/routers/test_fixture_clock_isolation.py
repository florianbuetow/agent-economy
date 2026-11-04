"""Business deadline freezes must not wake unrelated elapsed-time timers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from freezegun import freeze_time

pytestmark = pytest.mark.unit


@pytest.mark.usefixtures("app")
async def test_business_time_freeze_does_not_wake_elapsed_timer() -> None:
    """A one-hour timer remains pending while the business date jumps forward."""
    loop = asyncio.get_running_loop()
    elapsed = asyncio.Event()
    started = loop.time()
    timer = loop.call_later(3600, elapsed.set)
    try:
        with freeze_time("2040-01-01T00:00:00Z"):
            assert datetime.now(UTC) == datetime(2040, 1, 1, tzinfo=UTC)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not elapsed.is_set(), "Business time incorrectly advanced an elapsed timer"
            assert 0 <= loop.time() - started < 5
    finally:
        timer.cancel()
