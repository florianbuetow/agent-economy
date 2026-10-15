"""T-072 coverage sweep: HEALTH-03 was missing from the reputation-service-tests.md
sweep. ``test_health.py`` checks the field exists and its type, and
``test_persistence.py`` checks it resets near zero after a restart, but nothing
asserts it actually advances between two calls.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.unit
async def test_health_03_uptime_is_monotonic(client: AsyncClient) -> None:
    """HEALTH-03: uptime_seconds strictly increases across a >= 1s delay."""
    first = await client.get("/health")
    assert first.status_code == 200
    first_uptime = first.json()["uptime_seconds"]

    await asyncio.sleep(1.0)

    second = await client.get("/health")
    assert second.status_code == 200
    second_uptime = second.json()["uptime_seconds"]

    assert second_uptime > first_uptime
