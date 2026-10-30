"""Task Board background loops survive gateway outages and crashes (plan item 0.1)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from task_board_service.core.lifespan import supervise_loop
from task_board_service.core.state import AppState
from task_board_service.services.deadline_evaluator import DeadlineEvaluator

pytestmark = pytest.mark.unit

_OUTAGE_INTERVALS = 3


class _FlakyGatewayStore:
    """Store whose listing raises ConnectError for the first N calls."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def list_tasks(self, **_filters: Any) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ConnectError("db-gateway unreachable")
        return []


async def _wait_until(predicate: Any) -> None:
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_sweep_resumes_after_gateway_outage() -> None:
    store = _FlakyGatewayStore(failures=_OUTAGE_INTERVALS)
    evaluator = DeadlineEvaluator(
        store=store,  # type: ignore[arg-type]
        escrow_coordinator=MagicMock(),
        platform_agent=MagicMock(),
        deadline_actor_id="system-task-board-deadline",
    )
    sweep = asyncio.create_task(evaluator.run_periodic_sweep(0))
    try:
        await _wait_until(lambda: store.calls > _OUTAGE_INTERVALS + 4 or sweep.done())
        assert not sweep.done()
        assert store.calls > _OUTAGE_INTERVALS + 4
    finally:
        sweep.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep


async def test_supervisor_restarts_crashed_loop() -> None:
    state = AppState()
    starts: list[int] = []

    async def flaky_loop() -> None:
        starts.append(1)
        if len(starts) == 1:
            raise RuntimeError("loop crashed")
        await asyncio.Event().wait()

    first = supervise_loop(state, "deadline_sweep_task", flaky_loop, restart_delay_seconds=0)
    try:
        await _wait_until(lambda: len(starts) == 2)
        current = state.deadline_sweep_task
        assert current is not None
        assert current is not first
        assert not current.done()
    finally:
        state.shutting_down = True
        task = state.deadline_sweep_task
        assert task is not None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_supervisor_does_not_restart_cancelled_loop() -> None:
    state = AppState()
    starts: list[int] = []

    async def loop() -> None:
        starts.append(1)
        await asyncio.Event().wait()

    task = supervise_loop(state, "posting_recovery_task", loop, restart_delay_seconds=0)
    await _wait_until(lambda: len(starts) == 1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert state.posting_recovery_task is task
    assert len(starts) == 1


async def test_supervisor_does_not_restart_during_shutdown() -> None:
    state = AppState()
    starts: list[int] = []

    async def crashing_loop() -> None:
        starts.append(1)
        raise RuntimeError("loop crashed")

    state.shutting_down = True
    task = supervise_loop(
        state, "financial_effect_recovery_task", crashing_loop, restart_delay_seconds=0
    )
    with contextlib.suppress(RuntimeError):
        await task
    await asyncio.sleep(0.05)
    assert state.financial_effect_recovery_task is task
    assert len(starts) == 1
