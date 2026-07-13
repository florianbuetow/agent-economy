"""WP-06.1 (GAP-A1): the deadline evaluator fires the Court ruling trigger.

A disputed task with a submitted rebuttal — or one whose rebuttal deadline has
passed — must fire a platform-signed ``trigger_ruling`` on the next lazy evaluation.
A disputed task still inside an open rebuttal window (no rebuttal yet) must not.
A failed trigger is swallowed so the read that provoked it still succeeds, and the
task stays disputed for the next retry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from tests.fakes.in_memory_task_store import InMemoryTaskStore


def _now() -> datetime:
    return datetime.now(UTC)


def _disputed_task(
    *,
    dispute_id: str | None = "disp-1",
    rebuttal_submitted_at: str | None = None,
    rebuttal_deadline: str | None = None,
) -> dict[str, Any]:
    return {
        "task_id": "t-wp06",
        "poster_id": "a-poster",
        "title": "t",
        "spec": "s",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 3600,
        "review_deadline_seconds": 3600,
        "status": "disputed",
        "escrow_id": "esc-1",
        "bid_count": 1,
        "worker_id": "a-worker",
        "accepted_bid_id": "bid-1",
        "created_at": _now().isoformat(),
        "accepted_at": None,
        "submitted_at": None,
        "approved_at": None,
        "cancelled_at": None,
        "disputed_at": _now().isoformat(),
        "dispute_reason": "wrong",
        "dispute_id": dispute_id,
        "rebuttal_deadline": rebuttal_deadline,
        "rebuttal_submitted_at": rebuttal_submitted_at,
        "ruling_id": None,
        "ruled_at": None,
        "worker_pct": None,
        "ruling_summary": None,
        "expired_at": None,
        "escrow_pending": 0,
    }


def _make_evaluator(store: InMemoryTaskStore, platform_agent: Any) -> DeadlineEvaluator:
    coordinator = AsyncMock()
    coordinator.retry_pending_escrow = AsyncMock(side_effect=lambda task: task)
    return DeadlineEvaluator(
        store=store,
        escrow_coordinator=coordinator,
        platform_agent=platform_agent,
    )


@pytest.mark.unit
async def test_trigger_fires_when_rebuttal_submitted(tmp_path: Any) -> None:
    """A rebuttal exists -> the ruling trigger fires with the dispute id."""
    store = InMemoryTaskStore(db_path=str(tmp_path / "a.db"))
    task = _disputed_task(rebuttal_submitted_at=_now().isoformat())
    store.insert_task(task)
    platform_agent = AsyncMock()
    platform_agent.trigger_ruling = AsyncMock(return_value={"status": "ruled"})

    evaluator = _make_evaluator(store, platform_agent)
    await evaluator.evaluate_deadline(store.get_task("t-wp06"))

    platform_agent.trigger_ruling.assert_awaited_once_with("disp-1")


@pytest.mark.unit
async def test_trigger_fires_when_rebuttal_deadline_passed(tmp_path: Any) -> None:
    """No rebuttal but the window has closed -> the ruling trigger fires."""
    store = InMemoryTaskStore(db_path=str(tmp_path / "b.db"))
    past = (_now() - timedelta(seconds=5)).isoformat()
    store.insert_task(_disputed_task(rebuttal_deadline=past))
    platform_agent = AsyncMock()
    platform_agent.trigger_ruling = AsyncMock(return_value={"status": "ruled"})

    evaluator = _make_evaluator(store, platform_agent)
    await evaluator.evaluate_deadline(store.get_task("t-wp06"))

    platform_agent.trigger_ruling.assert_awaited_once_with("disp-1")


@pytest.mark.unit
async def test_trigger_not_fired_inside_open_window(tmp_path: Any) -> None:
    """No rebuttal and the window is still open -> no ruling trigger."""
    store = InMemoryTaskStore(db_path=str(tmp_path / "c.db"))
    future = (_now() + timedelta(hours=1)).isoformat()
    store.insert_task(_disputed_task(rebuttal_deadline=future))
    platform_agent = AsyncMock()
    platform_agent.trigger_ruling = AsyncMock()

    evaluator = _make_evaluator(store, platform_agent)
    await evaluator.evaluate_deadline(store.get_task("t-wp06"))

    platform_agent.trigger_ruling.assert_not_awaited()


@pytest.mark.unit
async def test_failed_trigger_is_retryable(tmp_path: Any) -> None:
    """A Court error during the trigger does not break the read; task stays disputed."""
    store = InMemoryTaskStore(db_path=str(tmp_path / "d.db"))
    store.insert_task(_disputed_task(rebuttal_submitted_at=_now().isoformat()))
    platform_agent = AsyncMock()
    request = httpx.Request("POST", "http://court/disputes/disp-1/rule")
    response = httpx.Response(502, request=request)
    platform_agent.trigger_ruling = AsyncMock(
        side_effect=httpx.HTTPStatusError("502", request=request, response=response)
    )

    evaluator = _make_evaluator(store, platform_agent)
    result = await evaluator.evaluate_deadline(store.get_task("t-wp06"))

    assert result["status"] == "disputed"
    platform_agent.trigger_ruling.assert_awaited_once_with("disp-1")


@pytest.mark.unit
async def test_no_trigger_without_platform_agent(tmp_path: Any) -> None:
    """With no injected platform agent the evaluator leaves disputed tasks alone."""
    store = InMemoryTaskStore(db_path=str(tmp_path / "e.db"))
    store.insert_task(_disputed_task(rebuttal_submitted_at=_now().isoformat()))
    coordinator = AsyncMock()
    coordinator.retry_pending_escrow = AsyncMock(side_effect=lambda task: task)

    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=coordinator, platform_agent=None)
    result = await evaluator.evaluate_deadline(store.get_task("t-wp06"))
    assert result["status"] == "disputed"
