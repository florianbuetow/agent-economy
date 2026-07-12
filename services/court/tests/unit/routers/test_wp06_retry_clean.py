"""WP-06.2 (T-040/GAP-A4): retry-clean ruling — Reputation 409 is success.

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


@pytest.mark.unit
async def test_kill_between_tb_record_and_dispute_persist_converges_on_retry(
    client: AsyncClient,
) -> None:
    """T-040/GAP-A4: a crash after TB records the ruling but before Court's own
    ruling-persist commits must converge to ``ruled`` on retry, with exactly one
    ledger settlement and one feedback pair -- never a second settlement.

    A real process kill cannot be raised in-process, so it is simulated by making
    the court-side local persist fail exactly once (the write furthest downstream,
    right after the external TB/Reputation side effects already committed). This
    exercises the same "external call already durably applied, court's own record
    of it has not yet landed" window that a genuine crash would leave behind.
    TB's ``record_ruling`` and Reputation's feedback are modeled here exactly as
    their real idempotent implementations behave (deduped by ruling_id / by
    to_agent+category), which is what actually protects against a double-settle.
    """
    dispute = await file_and_rebut(client)
    dispute_id = dispute["dispute_id"]
    inject_judge(worker_pct=65)

    state = get_app_state()

    settlements: list[str] = []
    seen_rulings: set[str] = set()

    async def fake_record_ruling(_task_id: str, payload: dict[str, object]) -> dict[str, object]:
        ruling_id = str(payload["ruling_id"])
        if ruling_id not in seen_rulings:
            seen_rulings.add(ruling_id)
            settlements.append(ruling_id)
        return {"status": "ok"}

    state.platform_agent.record_ruling = AsyncMock(side_effect=fake_record_ruling)

    recorded_feedback: set[tuple[str, str]] = set()

    async def fake_submit_feedback(payload: dict[str, object]) -> dict[str, object]:
        key = (str(payload["to_agent_id"]), str(payload["category"]))
        if key in recorded_feedback:
            raise _feedback_exists_error()
        recorded_feedback.add(key)
        return {"status": "ok"}

    state.platform_agent.submit_platform_feedback = AsyncMock(side_effect=fake_submit_feedback)

    original_persist_ruling = state.store.persist_ruling
    persist_calls = {"n": 0}

    def failing_once_persist_ruling(*args: object, **kwargs: object) -> None:
        persist_calls["n"] += 1
        if persist_calls["n"] == 1:
            msg = "simulated process kill before the court-side ruling record lands"
            raise RuntimeError(msg)
        original_persist_ruling(*args, **kwargs)

    state.store.persist_ruling = failing_once_persist_ruling

    rule_pay = ruling_payload(dispute_id)
    inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
    first_response = await client.post(f"/disputes/{dispute_id}/rule", json=token_body(rule_pay))
    assert first_response.status_code == 502

    reverted = await client.get(f"/disputes/{dispute_id}")
    assert reverted.json()["status"] == "rebuttal_pending"

    rule_pay2 = ruling_payload(dispute_id)
    inject_identity_verify(PLATFORM_AGENT_ID, rule_pay2)
    second_response = await client.post(f"/disputes/{dispute_id}/rule", json=token_body(rule_pay2))
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "ruled"

    # Exactly one ledger settlement and one feedback pair despite the retry.
    assert settlements == [dispute_id]
    assert len(recorded_feedback) == 2


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
            "reward": 100,
            "deliverables": [],
        }

    state.platform_agent.get_task = AsyncMock(side_effect=fetch_task_and_reenter)

    rule_pay = ruling_payload(dispute_id)
    inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
    response = await client.post(f"/disputes/{dispute_id}/rule", json=token_body(rule_pay))

    assert response.status_code == 200
    assert response.json()["status"] == "ruled"
    assert reentrant_responses == [409]
