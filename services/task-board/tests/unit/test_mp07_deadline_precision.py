"""Regression coverage for fractional task deadline precision (T-169)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from freezegun import freeze_time

from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.task_db_client import TaskDbClient


def _open_task() -> dict[str, Any]:
    return {
        "task_id": "t-fractional-deadline",
        "poster_id": "a-poster",
        "title": "Fractional deadline",
        "spec": "Preserve the complete cutoff timestamp.",
        "reward": 100,
        "status": "open",
        "escrow_id": "esc-fractional-deadline",
        "created_at": "2025-01-01T00:00:00.250000Z",
        "bidding_deadline_seconds": 1,
        "deadline_seconds": 60,
        "review_deadline_seconds": 60,
        "bid_count": 0,
    }


def _evaluator() -> tuple[DeadlineEvaluator, MagicMock, AsyncMock]:
    store = MagicMock()
    store.update_task.return_value = 1
    store.get_task.return_value = None
    escrow_coordinator = AsyncMock()
    escrow_coordinator.retry_pending_escrow = AsyncMock(side_effect=lambda task: task)
    escrow_coordinator.try_release_escrow = AsyncMock()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=escrow_coordinator)
    return evaluator, store, escrow_coordinator


@pytest.mark.unit
def test_compute_deadline_preserves_fractional_base_precision() -> None:
    deadline = DeadlineEvaluator.compute_deadline("2025-01-01T00:00:00.250000Z", 1)

    assert deadline == "2025-01-01T00:00:01.250000Z"


@pytest.mark.unit
def test_compute_deadline_keeps_whole_second_format() -> None:
    deadline = DeadlineEvaluator.compute_deadline("2025-01-01T00:00:00Z", 1)

    assert deadline == "2025-01-01T00:00:01Z"


@pytest.mark.unit
def test_gateway_create_payload_preserves_fractional_deadline() -> None:
    requests: list[httpx.Request] = []

    def _capture_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"task_id": "t-fractional-deadline"}, request=request)

    class _TestSigner:
        def sign_request(self, request: httpx.Request, *, action: str) -> httpx.Request:
            request.headers["Authorization"] = f"Bearer {action}"
            return request

    client = TaskDbClient(
        base_url="http://db-gateway",
        timeout_seconds=1,
        signer=_TestSigner(),  # type: ignore[arg-type]
    )
    client._client.close()
    client._client = httpx.Client(
        transport=httpx.MockTransport(_capture_request),
        base_url="http://db-gateway",
        timeout=httpx.Timeout(1),
    )
    try:
        client.insert_task(_open_task())
    finally:
        client._client.close()

    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    expected_deadline = "2025-01-01T00:00:01.250000Z"
    assert payload["bidding_deadline"] == expected_deadline
    event_payload = json.loads(payload["event"]["payload"])
    assert event_payload["bidding_deadline"] == expected_deadline


@pytest.mark.unit
async def test_fractional_deadline_remains_open_just_before_cutoff() -> None:
    evaluator, store, escrow_coordinator = _evaluator()
    task = _open_task()

    with freeze_time("2025-01-01 00:00:01.249999"):
        result = await evaluator.evaluate_deadline(task)

    assert result["status"] == "open"
    store.update_task.assert_not_called()
    escrow_coordinator.try_release_escrow.assert_not_awaited()


@pytest.mark.unit
async def test_fractional_deadline_expires_at_exact_cutoff() -> None:
    evaluator, store, escrow_coordinator = _evaluator()
    task = _open_task()

    with freeze_time("2025-01-01 00:00:01.250000"):
        result = await evaluator.evaluate_deadline(task)

    assert result["status"] == "expired"
    store.update_task.assert_called_once()
    escrow_coordinator.try_release_escrow.assert_awaited_once_with(
        "t-fractional-deadline",
        "esc-fractional-deadline",
        "a-poster",
    )
