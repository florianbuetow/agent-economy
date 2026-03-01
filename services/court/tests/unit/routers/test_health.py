"""Health endpoint tests.

Covers: HLTH-01 to HLTH-04 from court-service-tests.md.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tests.helpers import new_task_id
from tests.unit.routers.conftest import (
    file_dispute,
    file_dispute_payload,
    file_rebut_and_rule,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.unit
class TestHealth:
    """Health endpoint tests."""

    async def test_hlth_01_health_schema(self, client: AsyncClient) -> None:
        """HLTH-01: Health schema is correct."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "started_at" in data
        assert "total_disputes" in data
        assert "active_disputes" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert isinstance(data["started_at"], str)

    async def test_hlth_02_total_disputes_accurate(self, client: AsyncClient) -> None:
        """HLTH-02: total_disputes count is accurate after filing."""
        for _ in range(2):
            await file_dispute(client, file_dispute_payload(task_id=new_task_id()))

        response = await client.get("/health")
        data = response.json()
        assert data["total_disputes"] == 2

    async def test_hlth_03_active_disputes_excludes_ruled(self, client: AsyncClient) -> None:
        """HLTH-03: active_disputes equals count of non-ruled disputes."""
        d1_payload = file_dispute_payload(task_id=new_task_id())
        d2_payload = file_dispute_payload(task_id=new_task_id())
        d3_payload = file_dispute_payload(task_id=new_task_id())

        await file_dispute(client, d1_payload)
        await file_dispute(client, d2_payload)
        await file_rebut_and_rule(client, file_payload=d3_payload)

        response = await client.get("/health")
        data = response.json()
        assert data["total_disputes"] == 3
        assert data["active_disputes"] == 2

    async def test_hlth_04_uptime_monotonic(self, client: AsyncClient) -> None:
        """HLTH-04: Uptime is monotonic."""
        r1 = await client.get("/health")
        await asyncio.sleep(1.1)
        r2 = await client.get("/health")
        assert r2.json()["uptime_seconds"] > r1.json()["uptime_seconds"]
