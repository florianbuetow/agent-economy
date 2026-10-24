"""MP-08 startup and periodic recovery-loop acceptance contract."""

from __future__ import annotations

import asyncio
import importlib
import re
import threading
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.board_mp08_recovery import RecoveryBank, RecoveryStore

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
_WORKER_PATTERN = re.compile(
    r"^recovery-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class _Recovery(Protocol):
    worker_id: str

    async def run_cycle(self) -> int: ...

    async def run_periodic(self, interval_seconds: float) -> None: ...


def _new_worker(store: object | None = None) -> _Recovery:
    try:
        module = importlib.import_module("task_board_service.services.financial_effect_recovery")
    except ModuleNotFoundError as exc:
        if exc.name != "task_board_service.services.financial_effect_recovery":
            raise
        pytest.fail("Missing MP-08 FinancialEffectRecovery capability")
    recovery_type = getattr(module, "FinancialEffectRecovery", None)
    if not callable(recovery_type):
        pytest.fail("Missing callable FinancialEffectRecovery capability")
    return cast(
        "_Recovery",
        recovery_type(
            store=store or RecoveryStore(),
            bank=RecoveryBank(),
            worker_id=None,
            max_delivery_attempts_per_cycle=10,
        ),
    )


async def test_each_process_worker_gets_a_fresh_canonical_uuid() -> None:
    first = _new_worker()
    second = _new_worker()

    assert _WORKER_PATTERN.fullmatch(first.worker_id)
    assert _WORKER_PATTERN.fullmatch(second.worker_id)
    assert first.worker_id != second.worker_id


async def test_periodic_loop_runs_one_bounded_cycle_at_startup() -> None:
    recovery = _new_worker()
    started = asyncio.Event()

    async def start_cycle() -> int:
        started.set()
        return 0

    recovery.run_cycle = AsyncMock(side_effect=start_cycle)  # type: ignore[method-assign]

    task = asyncio.create_task(recovery.run_periodic(interval_seconds=3600.0))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        recovery.run_cycle.assert_awaited_once()  # type: ignore[attr-defined]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_periodic_loop_reports_typed_recoverable_failure_then_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recovery = _new_worker()
    second_cycle = asyncio.Event()
    calls = 0

    async def cycle() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ServiceError("gateway_unavailable", "bounded", 503, {})
        second_cycle.set()
        return 0

    recovery.run_cycle = cycle  # type: ignore[method-assign]
    task = asyncio.create_task(recovery.run_periodic(interval_seconds=0.001))
    try:
        await asyncio.wait_for(second_cycle.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls >= 2
    assert any(
        getattr(record, "error_code", None) == "gateway_unavailable" for record in caplog.records
    )


async def test_cancellation_drains_an_inflight_gateway_call() -> None:
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingStore(RecoveryStore):
        def lease_task_effect_delivery(self, worker_id: str) -> dict[str, Any]:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(timeout=3)
            finished.set()
            return super().lease_task_effect_delivery(worker_id)

    recovery = _new_worker(BlockingStore())
    task = asyncio.create_task(recovery.run_cycle())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_cycle_has_no_task_reader_dependency() -> None:
    class NoReaderStore(RecoveryStore):
        def __getattr__(self, name: str) -> object:
            if name in {"get_task", "list_tasks", "list_tasks_by_status"}:
                raise AssertionError(f"recovery touched forbidden public reader {name}")
            raise AttributeError(name)

    recovery = _new_worker(NoReaderStore())
    assert await recovery.run_cycle() == 0
