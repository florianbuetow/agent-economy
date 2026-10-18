"""Regression coverage for fractional native task deadline precision (T-169)."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.task_db_client import TaskDbClient
from tests.fakes.native_task_fixture import NativeTaskFixture, TaskTerms, native_task_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_TASK_ID = "t-fractional-deadline"
_FRACTIONAL_US = 250_000
_TERMS = TaskTerms(100, 1, 20, 60, 60)


def _open_task() -> dict[str, Any]:
    return {
        "task_id": _TASK_ID,
        "poster_id": "a-poster",
        "title": "Fractional deadline",
        "spec": "Preserve the complete cutoff timestamp.",
        "reward": 100,
        "status": "open",
        "escrow_id": "esc-fractional-deadline",
        "created_at": "2025-01-01T00:00:00.250000Z",
        "bidding_deadline_seconds": 1,
        "acceptance_window_seconds": 20,
        "deadline_seconds": 60,
        "review_deadline_seconds": 60,
        "bid_count": 0,
        "escrow_pending": 0,
    }


@contextmanager
def _fractional_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[NativeTaskFixture]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        scenario.clock.value_us += _FRACTIONAL_US
        scenario.open_task(_TASK_ID, _TERMS)
        task = scenario.task(_TASK_ID)
        assert task["created_at"].endswith("00.250000Z")
        assert task["bidding_cutoff_us"] == scenario.clock.value_us + 1_000_000
        yield scenario


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
        return httpx.Response(201, json={"task_id": _TASK_ID}, request=request)

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
    assert payload["acceptance_window_seconds"] == 20
    event_payload = json.loads(payload["event"]["payload"])
    assert event_payload["bidding_deadline"] == expected_deadline


@pytest.mark.unit
async def test_fractional_deadline_remains_open_just_before_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _fractional_scenario(tmp_path, monkeypatch) as scenario:
        cutoff_us = int(scenario.task(_TASK_ID)["bidding_cutoff_us"])
        scenario.clock.value_us = cutoff_us - 1
        evaluator = scenario.evaluator(platform_agent=None)

        result = await evaluator.evaluate_deadline(scenario.task(_TASK_ID))

        assert result["status"] == "open"
        assert scenario.task(_TASK_ID)["status"] == "open"
        assert (
            scenario.store.writer.connection.execute(
                "SELECT COUNT(*) FROM board_task_operations WHERE task_id=? "
                "AND command='expire_bidding'",
                (_TASK_ID,),
            ).fetchone()[0]
            == 0
        )
        assert scenario.store.reader.get_escrow(f"esc-{_TASK_ID}")["status"] == "locked"
        scenario.bank.escrow_release.assert_not_awaited()


@pytest.mark.unit
async def test_fractional_deadline_expires_at_exact_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _fractional_scenario(tmp_path, monkeypatch) as scenario:
        cutoff_us = int(scenario.task(_TASK_ID)["bidding_cutoff_us"])
        scenario.clock.value_us = cutoff_us
        evaluator = scenario.evaluator(platform_agent=None)

        result = await evaluator.evaluate_deadline(scenario.task(_TASK_ID))

        assert result["status"] == "expired"
        assert result["expired_at"].endswith("01.250000Z")
        operation_id = scenario.assert_settlement(
            _TASK_ID,
            command="expire_bidding",
            status="expired",
            recipient=scenario.poster_id,
            event_type="task.expired",
        )
        assert operation_id
        assert scenario.store.reader.get_account(scenario.poster_id)["balance"] == 1000
        scenario.bank.escrow_release.assert_awaited_once()
