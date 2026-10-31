"""UI proxy mutations preserve Task Board financial projections exactly."""

from __future__ import annotations

from typing import Any

import pytest
from service_auth.task_board_responses import TaskBoardCommandResponse

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration

_OPERATION_ID = "op-00000000-0000-4000-8000-000000000208"
_TASK_ID = "t-00000000-0000-4000-8000-000000000208"
_FINANCIAL_EFFECTS: dict[str, dict[str, object]] = {
    "accept_bid": {
        "schema_version": 1,
        "settlement_state": "locked",
        "receipt_state": "absent",
        "outcome": None,
        "operation_id": None,
        "receipt_reference": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "approve_task": {
        "schema_version": 1,
        "settlement_state": "complete",
        "receipt_state": "confirmed",
        "outcome": "worker_paid",
        "operation_id": _OPERATION_ID,
        "receipt_reference": _OPERATION_ID,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
    "dispute_task": {
        "schema_version": 1,
        "settlement_state": "locked",
        "receipt_state": "absent",
        "outcome": None,
        "operation_id": None,
        "receipt_reference": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    },
}


def _mutation(command: str, task_id: str) -> dict[str, Any]:
    source_status, target_status, effect_kind = {
        "accept_bid": ("open", "accepted", "none"),
        "approve_task": ("submitted", "approved", "bank_release"),
        "dispute_task": ("submitted", "disputed", "court_claim"),
    }[command]
    effect_reference = (
        _OPERATION_ID
        if command == "approve_task"
        else "clm-00000000-0000-4000-8000-000000000208"
        if command == "dispute_task"
        else None
    )
    return {
        "task_id": task_id,
        "status": target_status,
        "lifecycle_version": 8,
        "financial_effect": _FINANCIAL_EFFECTS[command],
        "operation": {
            "protocol_version": 1,
            "operation_id": _OPERATION_ID,
            "task_id": task_id,
            "command": command,
            "request_sha256": "a" * 64,
            "operation_state": "completed",
            "source_status": source_status,
            "source_lifecycle_version": 7,
            "target_status": target_status,
            "target_lifecycle_version": 8,
            "effect_kind": effect_kind,
            "effect_state": "none" if effect_kind == "none" else "succeeded",
            "effect_operation_id": _OPERATION_ID if command == "approve_task" else None,
            "effect_result_reference": effect_reference,
            "effect_intent": None if effect_kind == "none" else {"fixture": command},
            "event_id": 208,
            "result": {
                "task_id": task_id,
                "command": command,
                "status": target_status,
                "lifecycle_version": 8,
                "event_id": 208,
            },
        },
    }


class _FinancialProjectionAgent:
    agent_id = "a-ui-mp08"

    async def accept_bid(self, **kwargs: Any) -> TaskBoardCommandResponse:
        return TaskBoardCommandResponse(
            status_code=200,
            body=_mutation("accept_bid", kwargs["task_id"]),
        )

    async def approve_task(self, **kwargs: Any) -> TaskBoardCommandResponse:
        return TaskBoardCommandResponse(
            status_code=200,
            body=_mutation("approve_task", kwargs["task_id"]),
        )

    async def dispute_task(self, **kwargs: Any) -> TaskBoardCommandResponse:
        return TaskBoardCommandResponse(
            status_code=200,
            body=_mutation("dispute_task", kwargs["task_id"]),
        )


@pytest.mark.parametrize(
    ("command", "path", "body"),
    [
        (
            "accept_bid",
            f"/api/proxy/tasks/{_TASK_ID}/bids/bid-mp08/accept",
            {
                "operation_id": _OPERATION_ID,
                "expected_lifecycle_version": 7,
            },
        ),
        (
            "approve_task",
            f"/api/proxy/tasks/{_TASK_ID}/approve",
            {
                "operation_id": _OPERATION_ID,
                "expected_lifecycle_version": 7,
            },
        ),
        (
            "dispute_task",
            f"/api/proxy/tasks/{_TASK_ID}/dispute",
            {
                "reason": "Preserve the exact upstream financial truth.",
                "operation_id": _OPERATION_ID,
                "expected_lifecycle_version": 7,
            },
        ),
    ],
)
async def test_proxy_preserves_financial_effect_exactly(
    client: Any,
    command: str,
    path: str,
    body: dict[str, object],
) -> None:
    get_app_state().user_agent = _FinancialProjectionAgent()  # type: ignore[assignment]

    response = await client.post(path, json=body)

    assert response.status_code == 200, response.text
    assert response.json()["financial_effect"] == _FINANCIAL_EFFECTS[command]
