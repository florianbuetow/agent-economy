"""MP-08 Task Board public financial-effect projection acceptance tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from task_board_service.core.state import init_app_state, reset_app_state
from task_board_service.routers import bids, tasks
from task_board_service.schemas import TaskResponse, TaskSummary
from task_board_service.services.task_helpers import task_to_response, task_to_summary
from task_board_service.services.task_operations import operation_response

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_OPERATION_ID = "op-11111111-1111-4111-8111-111111111111"


def _financial_effect(
    *,
    settlement_state: str,
    receipt_state: str,
    outcome: str | None,
    operation_id: str | None,
    receipt_reference: str | None,
    projection_pending: bool,
    pending_target_status: str | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "settlement_state": settlement_state,
        "receipt_state": receipt_state,
        "outcome": outcome,
        "operation_id": operation_id,
        "receipt_reference": receipt_reference,
        "business_projection_pending": projection_pending,
        "pending_target_status": pending_target_status,
    }


def _row(financial_effect: dict[str, object], *, status: str = "submitted") -> dict[str, Any]:
    return {
        "task_id": "t-11111111-1111-4111-8111-111111111111",
        "poster_id": "a-poster",
        "title": "Task",
        "spec": "Exact specification",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 60,
        "deadline_seconds": 60,
        "review_deadline_seconds": 60,
        "status": status,
        "escrow_id": "esc-task",
        "bid_count": 1,
        "worker_id": "a-worker",
        "accepted_bid_id": "bid-1",
        "created_at": "2026-10-22T00:00:00Z",
        "accepted_at": "2026-10-22T00:01:00Z",
        "submitted_at": "2026-10-22T00:02:00Z",
        "approved_at": None,
        "cancelled_at": None,
        "disputed_at": None,
        "dispute_reason": None,
        "dispute_id": None,
        "rebuttal_deadline": None,
        "rebuttal_submitted_at": None,
        "ruling_id": None,
        "ruled_at": None,
        "worker_pct": None,
        "ruling_summary": None,
        "expired_at": None,
        "escrow_pending": True,
        "lifecycle_version": 2,
        "timing_contract_version": 2,
        "bidding_cutoff_us": 60_000_000,
        "acceptance_cutoff_us": 120_000_000,
        "execution_cutoff_us": 180_000_000,
        "review_cutoff_us": 240_000_000,
        "accepted_reward": 100,
        "accepted_bid_amount": 90,
        "worker_payment": None,
        "poster_refund": None,
        "pending_transition": None,
        "legacy_reconciliations": [],
        "financial_effect": financial_effect,
    }


def _operation(task_id: str) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": _OPERATION_ID,
        "task_id": task_id,
        "command": "approve_task",
        "request_sha256": "a" * 64,
        "operation_state": "claimed",
        "source_status": "submitted",
        "source_lifecycle_version": 2,
        "target_status": "approved",
        "target_lifecycle_version": 3,
        "effect_kind": "bank_release",
        "effect_state": "pending",
        "effect_operation_id": _OPERATION_ID,
        "effect_result_reference": None,
        "effect_intent": {
            "financial_request": {
                "request_version": 1,
                "operation_kind": "escrow_release",
                "escrow_id": "esc-task",
                "recipient_account_id": "a-worker",
                "source_reference": f"task:{task_id}:approved",
            },
            "financial_request_sha256": "b" * 64,
            "expected_escrow": {
                "escrow_id": "esc-task",
                "task_id": task_id,
                "payer_account_id": "a-poster",
                "amount": 100,
            },
        },
        "event_id": None,
        "result": None,
    }


def _completed_accept_operation(task_id: str) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": "op-22222222-2222-4222-8222-222222222222",
        "task_id": task_id,
        "command": "accept_bid",
        "request_sha256": "c" * 64,
        "operation_state": "completed",
        "source_status": "open",
        "source_lifecycle_version": 0,
        "target_status": "accepted",
        "target_lifecycle_version": 1,
        "effect_kind": "none",
        "effect_state": "none",
        "effect_operation_id": None,
        "effect_result_reference": None,
        "effect_intent": None,
        "event_id": 7,
        "result": {
            "task_id": task_id,
            "command": "accept_bid",
            "status": "accepted",
            "lifecycle_version": 1,
            "event_id": 7,
        },
    }


class _RouteManager:
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        self.mutation = {**detail, "operation": _operation(str(detail["task_id"]))}

    async def get_task(self, task_id: str) -> dict[str, Any]:
        assert task_id == self.detail["task_id"]
        return self.detail

    async def list_tasks(self, **_filters: object) -> list[dict[str, Any]]:
        return [task_to_summary(self.detail)]

    async def accept_bid(self, task_id: str, bid_id: str, token: str) -> dict[str, Any]:
        assert task_id == self.detail["task_id"] and bid_id == "bid-1" and token == "token"
        return {
            **self.detail,
            "operation": _completed_accept_operation(str(self.detail["task_id"])),
        }

    async def cancel_task(self, task_id: str, token: str) -> dict[str, Any]:
        return self._mutation(task_id, token)

    async def submit_deliverable(self, task_id: str, token: str) -> dict[str, Any]:
        return self._mutation(task_id, token)

    async def approve_task(self, task_id: str, token: str) -> dict[str, Any]:
        return self._mutation(task_id, token)

    async def dispute_task(self, task_id: str, token: str, _platform: object) -> dict[str, Any]:
        return self._mutation(task_id, token)

    async def record_ruling(self, task_id: str, token: str) -> dict[str, Any]:
        return self._mutation(task_id, token)

    def _mutation(self, task_id: str, token: str) -> dict[str, Any]:
        assert task_id == self.detail["task_id"] and token == "token"
        return self.mutation


class _NoInputsStore:
    def get_task_input_manifest(self, _task_id: str) -> dict[str, object]:
        return {"input_manifest": None}


@pytest.fixture
async def projection_client() -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, object]]]:
    effect = _financial_effect(
        settlement_state="pending",
        receipt_state="confirmed",
        outcome="worker_paid",
        operation_id=_OPERATION_ID,
        receipt_reference=_OPERATION_ID,
        projection_pending=True,
        pending_target_status="approved",
    )
    detail = task_to_response(_row(effect))
    reset_app_state()
    try:
        state = init_app_state()
        state.task_manager = cast("Any", _RouteManager(detail))
        state.store = cast("Any", _NoInputsStore())
        state.platform_agent = cast("Any", object())
        app = FastAPI()
        app.include_router(tasks.router)
        app.include_router(bids.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://task-board.test"
        ) as client:
            yield client, effect
    finally:
        reset_app_state()


@pytest.mark.parametrize(
    "financial_effect",
    [
        _financial_effect(
            settlement_state="locked",
            receipt_state="absent",
            outcome=None,
            operation_id=None,
            receipt_reference=None,
            projection_pending=False,
            pending_target_status=None,
        ),
        _financial_effect(
            settlement_state="pending",
            receipt_state="absent",
            outcome=None,
            operation_id=_OPERATION_ID,
            receipt_reference=None,
            projection_pending=True,
            pending_target_status="approved",
        ),
        _financial_effect(
            settlement_state="pending",
            receipt_state="confirmed",
            outcome="worker_paid",
            operation_id=_OPERATION_ID,
            receipt_reference=_OPERATION_ID,
            projection_pending=True,
            pending_target_status="approved",
        ),
        _financial_effect(
            settlement_state="complete",
            receipt_state="confirmed",
            outcome="worker_paid",
            operation_id=_OPERATION_ID,
            receipt_reference=_OPERATION_ID,
            projection_pending=False,
            pending_target_status=None,
        ),
        _financial_effect(
            settlement_state="attention_required",
            receipt_state="absent",
            outcome=None,
            operation_id=_OPERATION_ID,
            receipt_reference=None,
            projection_pending=True,
            pending_target_status="approved",
        ),
        _financial_effect(
            settlement_state="conflict",
            receipt_state="confirmed",
            outcome="split",
            operation_id=_OPERATION_ID,
            receipt_reference=_OPERATION_ID,
            projection_pending=False,
            pending_target_status=None,
        ),
        _financial_effect(
            settlement_state="historical",
            receipt_state="historical",
            outcome="worker_paid",
            operation_id=None,
            receipt_reference=None,
            projection_pending=False,
            pending_target_status=None,
        ),
    ],
)
def test_detail_and_list_preserve_the_exact_gateway_financial_effect(
    financial_effect: dict[str, object],
) -> None:
    row = _row(financial_effect)

    detail = task_to_response(row)
    summary = task_to_summary(row)

    assert detail["financial_effect"] == financial_effect
    assert summary["financial_effect"] == financial_effect
    assert TaskResponse.model_validate(detail).model_dump()["financial_effect"] == financial_effect
    assert TaskSummary.model_validate(summary).model_dump()["financial_effect"] == financial_effect


def test_terminal_business_status_without_evidence_cannot_be_presented_as_paid() -> None:
    effect = _financial_effect(
        settlement_state="attention_required",
        receipt_state="absent",
        outcome=None,
        operation_id=None,
        receipt_reference=None,
        projection_pending=False,
        pending_target_status=None,
    )
    detail = task_to_response(_row(effect, status="approved"))

    assert detail["status"] == "approved"
    assert detail["financial_effect"] == effect
    assert detail["financial_effect"]["outcome"] is None


def test_all_mutation_responses_preserve_current_financial_truth_beside_operation() -> None:
    effect = _financial_effect(
        settlement_state="pending",
        receipt_state="confirmed",
        outcome="worker_paid",
        operation_id=_OPERATION_ID,
        receipt_reference=_OPERATION_ID,
        projection_pending=True,
        pending_target_status="approved",
    )
    row = _row(effect)
    envelope = {"task_id": row["task_id"], "operation_id": _OPERATION_ID}

    class Store:
        def get_task(self, task_id: str) -> dict[str, Any]:
            assert task_id == row["task_id"]
            return row

    response = operation_response(Store(), envelope)  # type: ignore[arg-type]

    assert response["financial_effect"] == effect
    assert response["operation"] is envelope


@pytest.mark.asyncio
async def test_actual_detail_list_and_six_mutation_routes_preserve_projection(
    projection_client: tuple[httpx.AsyncClient, dict[str, object]],
) -> None:
    client, effect = projection_client
    task_id = "t-11111111-1111-4111-8111-111111111111"
    detail = await client.get(f"/tasks/{task_id}")
    listing = await client.get("/tasks")
    assert detail.status_code == listing.status_code == 200
    assert detail.json()["financial_effect"] == effect
    assert listing.json()["tasks"][0]["financial_effect"] == effect

    paths = (
        f"/tasks/{task_id}/bids/bid-1/accept",
        f"/tasks/{task_id}/cancel",
        f"/tasks/{task_id}/submit",
        f"/tasks/{task_id}/approve",
        f"/tasks/{task_id}/dispute",
        f"/tasks/{task_id}/ruling",
    )
    for path in paths:
        response = await client.post(path, json={"token": "token"})
        expected_status = 200 if "/bids/" in path else 202
        assert response.status_code == expected_status, (path, response.text)
        assert response.json()["financial_effect"] == effect


@pytest.mark.parametrize("mutation", ["missing", "extra", "receipt", "target"])
def test_projection_shape_and_cross_field_truth_are_strict(mutation: str) -> None:
    effect = _financial_effect(
        settlement_state="complete",
        receipt_state="confirmed",
        outcome="worker_paid",
        operation_id=_OPERATION_ID,
        receipt_reference=_OPERATION_ID,
        projection_pending=False,
        pending_target_status=None,
    )
    if mutation == "missing":
        effect.pop("receipt_state")
    elif mutation == "extra":
        effect["extra"] = "forbidden"
    elif mutation == "receipt":
        effect["receipt_reference"] = None
    elif mutation == "target":
        effect["business_projection_pending"] = True
        effect["pending_target_status"] = None
    else:
        raise AssertionError(f"Unknown mutation: {mutation}")

    with pytest.raises((ValidationError, ValueError)):
        TaskResponse.model_validate(task_to_response(_row(effect)))
