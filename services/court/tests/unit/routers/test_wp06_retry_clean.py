"""WP-06.2 (T-040/GAP-A4) and plan 0.3: retry-clean, resumable rulings.

On a retried ruling the spec/delivery feedback may already exist. A Reputation
``409 feedback_exists`` must be treated as success so the ruling converges instead
of failing with 502 and reverting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest

from court_service.core.state import get_app_state
from tests.helpers import make_ruling_acknowledgment
from tests.unit.routers.conftest import (
    PLATFORM_AGENT_ID,
    file_and_rebut,
    inject_identity_verify,
    inject_judge,
    ruling_payload,
    token_body,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


def _feedback_exists_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://reputation/feedback")
    response = httpx.Response(
        409,
        json={"error": "feedback_exists", "message": "Feedback already submitted"},
        request=request,
    )
    return httpx.HTTPStatusError("409 Conflict", request=request, response=response)


@pytest.mark.unit
async def test_feedback_exists_treated_as_success(client: AsyncClient) -> None:
    """Reputation 409 feedback_exists -> ruling still reaches ruled (200)."""
    dispute = await file_and_rebut(client)
    dispute_id = dispute["dispute_id"]
    inject_judge(worker_pct=70)

    state = get_app_state()
    state.platform_agent.submit_platform_feedback.side_effect = _feedback_exists_error()

    rule_pay = ruling_payload(dispute_id)
    inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
    response = await client.post(f"/disputes/{dispute_id}/rule", json=token_body(rule_pay))

    assert response.status_code == 200
    assert response.json()["status"] == "ruled"
    # Both feedback submissions were still attempted (spec + delivery).
    assert state.platform_agent.submit_platform_feedback.await_count == 2


def _operation_id_conflict() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://task-board/tasks/t/ruling")
    response = httpx.Response(
        409,
        json={
            "error": "operation_id_conflict",
            "message": "Operation identity already belongs to a different task command",
            "details": {},
        },
        request=request,
    )
    return httpx.HTTPStatusError("409 Conflict", request=request, response=response)


class _StrictTaskBoard:
    """Task Board record_ruling with the Gateway's real operation_id semantics."""

    def __init__(self) -> None:
        self.settlements: list[str] = []
        self._operations: dict[str, tuple[dict[str, object], dict[str, object]]] = {}

    async def record_ruling(self, _task_id: str, payload: dict[str, object]) -> dict[str, object]:
        operation_id = str(payload["operation_id"])
        if operation_id in self._operations:
            stored_request, stored_ack = self._operations[operation_id]
            if stored_request != payload:
                raise _operation_id_conflict()
            return stored_ack
        stored = get_app_state().store.get_dispute(str(payload["ruling_id"]))
        if stored is None:
            raise AssertionError("Ruling fixture requires a stored dispute")
        ack = make_ruling_acknowledgment(
            payload,
            poster_id=str(stored["claimant_id"]),
            worker_id=str(stored["respondent_id"]),
            escrow_id=str(stored["escrow_id"]),
            reward=1000,
            event_id=1,
        )
        self._operations[operation_id] = (dict(payload), ack)
        self.settlements.append(operation_id)
        return ack


@pytest.mark.unit
async def test_kill_after_delivery_before_ruled_marker_converges_on_retry(
    client: AsyncClient,
) -> None:
    """Plan 0.3 (supersedes the T-040 'kill before persist' window).

    The decision is persisted before delivery, so a crash after Task Board
    settled and Reputation recorded -- but before the ``ruled`` marker landed --
    resumes from the stored decision: no second judge call, no second
    settlement. Task Board is modeled with the Gateway's real ``operation_id``
    semantics: an identical request replays the stored acknowledgment and a
    different request under the same ``operation_id`` is a 409 conflict (it
    does not silently accept duplicates).
    """
    dispute = await file_and_rebut(client)
    dispute_id = dispute["dispute_id"]
    inject_judge(worker_pct=65)

    state = get_app_state()
    task_board = _StrictTaskBoard()
    state.platform_agent.record_ruling = AsyncMock(side_effect=task_board.record_ruling)

    recorded_feedback: set[tuple[str, str]] = set()

    async def fake_submit_feedback(payload: dict[str, object]) -> dict[str, object]:
        key = (str(payload["to_agent_id"]), str(payload["category"]))
        if key in recorded_feedback:
            raise _feedback_exists_error()
        recorded_feedback.add(key)
        return {"status": "ok"}

    state.platform_agent.submit_platform_feedback = AsyncMock(side_effect=fake_submit_feedback)

    original_complete = state.store.complete_ruling
    complete_calls = {"n": 0}

    def crashing_once_complete(*args: object, **kwargs: object) -> None:
        complete_calls["n"] += 1
        if complete_calls["n"] == 1:
            msg = "simulated process kill before the ruled marker lands"
            raise RuntimeError(msg)
        original_complete(*args, **kwargs)

    state.store.complete_ruling = crashing_once_complete

    rule_pay = ruling_payload(dispute_id)
    inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
    first_response = await client.post(f"/disputes/{dispute_id}/rule", json=token_body(rule_pay))
    assert first_response.status_code == 502

    pending = await client.get(f"/disputes/{dispute_id}")
    assert pending.json()["status"] == "judging"

    rule_pay2 = ruling_payload(dispute_id)
    inject_identity_verify(PLATFORM_AGENT_ID, rule_pay2)
    second_response = await client.post(f"/disputes/{dispute_id}/rule", json=token_body(rule_pay2))
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "ruled"
    assert second_response.json()["worker_pct"] == 65

    assert len(task_board.settlements) == 1
    assert len(recorded_feedback) == 2
    state.judges[0].evaluate.assert_awaited_once()


@pytest.mark.unit
async def test_reentrant_rule_call_during_task_fetch_fails_fast(client: AsyncClient) -> None:
    """GAP-A1/GAP-A4: a reentrant /rule call made while the outer one is still
    fetching the task (exactly what Task Board's lazy evaluator does on its own
    ``get_task`` read) must fail fast with ``dispute_not_ready`` -- not recurse.

    The dispute must already read back as ``judging`` at the moment Task Board is
    called, proving the status write landed *before* any Task Board round trip.
    """
    dispute = await file_and_rebut(client)
    dispute_id = dispute["dispute_id"]
    inject_judge(worker_pct=70)

    state = get_app_state()
    reentrant_responses: list[int] = []

    async def fetch_task_and_reenter(_task_id: str) -> dict[str, object]:
        mid_flight = await client.get(f"/disputes/{dispute_id}")
        assert mid_flight.json()["status"] == "judging"

        reentrant_rule_pay = ruling_payload(dispute_id)
        inject_identity_verify(PLATFORM_AGENT_ID, reentrant_rule_pay)
        reentrant = await client.post(
            f"/disputes/{dispute_id}/rule", json=token_body(reentrant_rule_pay)
        )
        reentrant_responses.append(reentrant.status_code)

        return {
            "task_id": _task_id,
            "spec": "Build feature X",
            "title": "Task",
            "reward": 1000,
            "deliverables": [],
            "lifecycle_version": 3,
        }

    state.platform_agent.get_task = AsyncMock(side_effect=fetch_task_and_reenter)

    rule_pay = ruling_payload(dispute_id)
    inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
    response = await client.post(f"/disputes/{dispute_id}/rule", json=token_body(rule_pay))

    assert response.status_code == 200
    assert response.json()["status"] == "ruled"
    assert reentrant_responses == [409]
