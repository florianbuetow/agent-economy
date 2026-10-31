"""Task Board /health reports background loop liveness (plan item 0.1)."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from task_board_service.core.state import get_app_state

pytestmark = pytest.mark.unit

_LOOPS = {"deadline_sweep", "posting_recovery", "financial_effect_recovery", "artifact_maintenance"}


async def test_health_reports_all_loops_running(client) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["background_loops"] == dict.fromkeys(_LOOPS, "running")


async def test_health_degraded_when_sweep_loop_dead(client) -> None:
    state = get_app_state()
    sweep = state.deadline_sweep_task
    assert sweep is not None
    sweep.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweep

    response = await client.get("/health")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["background_loops"]["deadline_sweep"] == "dead"
    assert data["background_loops"]["posting_recovery"] == "running"
    assert data["background_loops"]["financial_effect_recovery"] == "running"
    assert isinstance(data["uptime_seconds"], (int, float))
    assert isinstance(data["tasks_by_status"], dict)


async def test_health_reports_artifact_maintenance_loop_when_started(client) -> None:
    state = get_app_state()
    artifact_loop = asyncio.create_task(asyncio.Event().wait())
    state.artifact_maintenance_task = artifact_loop
    try:
        running = await client.get("/health")
        assert running.status_code == 200
        assert running.json()["background_loops"]["artifact_maintenance"] == "running"

        artifact_loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await artifact_loop
        dead = await client.get("/health")
        assert dead.status_code == 503
        assert dead.json()["status"] == "degraded"
        assert dead.json()["background_loops"]["artifact_maintenance"] == "dead"
    finally:
        artifact_loop.cancel()
        state.artifact_maintenance_task = None
