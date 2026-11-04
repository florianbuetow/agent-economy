from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from task_board_service.core.state import init_app_state, reset_app_state
from task_board_service.routers.health import router as health_router
from task_board_service.routers.tasks import router as tasks_router
from task_board_service.services.async_store import store_call, store_read
from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.task_creation import TaskCreationCoordinator

pytestmark = pytest.mark.unit

_TRACE_MARKER: ContextVar[str | None] = ContextVar("trace_marker", default=None)


class _SlowStore:
    def list_tasks(self, **_: Any) -> list[dict[str, Any]]:
        time.sleep(0.15)
        return []


class _HeldStore:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def list_tasks(self, **_: Any) -> list[dict[str, Any]]:
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test did not release held Gateway read")
        return []


class _TaskManager:
    def __init__(self, store: _SlowStore | _HeldStore) -> None:
        self.store = store
        self.deadline_evaluator = self
        self._creation = TaskCreationCoordinator(self)  # type: ignore[arg-type]

    async def list_tasks(self, **_: Any) -> list[dict[str, Any]]:
        return await self._creation.list_tasks(None, None, None, None, None)

    async def evaluate_deadlines_batch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return tasks

    async def get_stats(self) -> dict[str, Any]:
        return {"total_tasks": 0, "tasks_by_status": {}}


@pytest.mark.asyncio
async def test_store_call_preserves_request_context() -> None:
    token = _TRACE_MARKER.set("request-trace")
    try:
        assert await store_read(_TRACE_MARKER.get) == "request-trace"
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert await store_call(_TRACE_MARKER.get, executor=executor) == "request-trace"
    finally:
        _TRACE_MARKER.reset(token)


@pytest.mark.asyncio
async def test_store_call_drains_in_flight_work_before_propagating_cancellation() -> None:
    entered = threading.Event()
    release = threading.Event()

    def held_mutation() -> None:
        entered.set()
        if not release.wait(timeout=2):
            raise TimeoutError("test did not release held mutation")

    pending = asyncio.create_task(store_read(held_mutation))
    assert await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=0.5)
    pending.cancel()
    await asyncio.sleep(0)
    assert not pending.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, timeout=0.5)


@pytest.mark.asyncio
async def test_deadline_sweep_does_not_block_unrelated_event_loop_work() -> None:
    evaluator = DeadlineEvaluator(
        _SlowStore(),  # type: ignore[arg-type]
        escrow_coordinator=None,  # type: ignore[arg-type]
        platform_agent=None,
        deadline_actor_id="a-system",
    )
    sweep = asyncio.create_task(evaluator.run_periodic_sweep(0))
    ticks = 0
    deadline = asyncio.get_running_loop().time() + 0.12
    try:
        while asyncio.get_running_loop().time() < deadline:
            ticks += 1
            await asyncio.sleep(0.01)
    finally:
        sweep.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sweep
    assert ticks >= 5


@pytest.mark.asyncio
async def test_public_tasks_request_does_not_block_health_endpoint() -> None:
    app = FastAPI()
    app.include_router(tasks_router)
    app.include_router(health_router)
    state = init_app_state()
    held_store = _HeldStore()
    state.task_manager = _TaskManager(held_store)  # type: ignore[assignment]
    loops = [asyncio.create_task(asyncio.sleep(10)) for _ in range(3)]
    state.deadline_sweep_task, state.posting_recovery_task, state.financial_effect_recovery_task = (
        loops
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            pending_tasks = asyncio.create_task(client.get("/tasks"))
            entered = await asyncio.wait_for(
                asyncio.to_thread(held_store.entered.wait),
                timeout=0.5,
            )
            assert entered is True
            health = await asyncio.wait_for(client.get("/health"), timeout=0.1)
            assert not pending_tasks.done()
            held_store.release.set()
            tasks = await asyncio.wait_for(pending_tasks, timeout=0.5)
        assert health.status_code == 200
        assert tasks.status_code == 200
    finally:
        held_store.release.set()
        for loop in loops:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop
        reset_app_state()
