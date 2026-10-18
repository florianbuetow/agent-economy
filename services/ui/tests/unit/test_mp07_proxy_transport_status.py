"""Failing-first contract for preserving native Task Board transport status."""

from __future__ import annotations

import json
from typing import Any

import pytest
from service_auth import TaskBoardCommandContractError, TaskBoardCommandResponse
from service_commons.exceptions import ServiceError

from ui_service.core.state import get_app_state, init_app_state
from ui_service.routers import proxy as proxy_router
from ui_service.schemas import FileDisputeRequest, NativeMutationRequest
from ui_service.services import proxy as proxy_service

pytestmark = pytest.mark.unit

OPERATION_ID = "op-00000000-0000-4000-8000-000000000789"


@pytest.fixture(autouse=True)
def initialized_app_state() -> None:
    init_app_state()


def _mutation_body(command: str, state: str) -> dict[str, Any]:
    source_status, target_status, effect_kind = {
        "accept_bid": ("open", "accepted", "none"),
        "approve_task": ("submitted", "approved", "bank_release"),
        "dispute_task": ("submitted", "disputed", "court_claim"),
    }[command]
    completed = state == "completed"
    effect_reference = None
    if completed and command == "approve_task":
        effect_reference = OPERATION_ID
    elif completed and command == "dispute_task":
        effect_reference = "clm-ui-status"
    operation: dict[str, Any] = {
        "protocol_version": 1,
        "operation_id": OPERATION_ID,
        "task_id": "t-ui-status",
        "command": command,
        "request_sha256": "a" * 64,
        "operation_state": state,
        "source_status": source_status,
        "source_lifecycle_version": 4,
        "target_status": target_status,
        "target_lifecycle_version": 5,
        "effect_kind": effect_kind,
        "effect_state": (
            "none" if effect_kind == "none" else ("succeeded" if completed else "pending")
        ),
        "effect_operation_id": OPERATION_ID if command == "approve_task" else None,
        "effect_result_reference": effect_reference,
        "effect_intent": None if effect_kind == "none" else {"fixture": command},
        "event_id": 789 if completed else None,
        "result": None,
    }
    if completed:
        operation["result"] = {
            "task_id": "t-ui-status",
            "command": command,
            "status": target_status,
            "lifecycle_version": 5,
            "event_id": 789,
        }
    return {
        "task_id": "t-ui-status",
        "status": target_status if completed else source_status,
        "lifecycle_version": 5 if completed else 4,
        "operation": operation,
    }


class _TransportAgent:
    agent_id = "a-ui-status"

    def __init__(self, response: TaskBoardCommandResponse | Exception) -> None:
        self.response = response

    async def accept_bid(self, **_kwargs: Any) -> TaskBoardCommandResponse:
        return self._result()

    async def approve_task(self, **_kwargs: Any) -> TaskBoardCommandResponse:
        return self._result()

    async def dispute_task(self, **_kwargs: Any) -> TaskBoardCommandResponse:
        return self._result()

    def _result(self) -> TaskBoardCommandResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _install_agent(agent: _TransportAgent) -> None:
    state: Any = get_app_state()
    state.user_agent = agent


@pytest.mark.parametrize(
    ("command", "status_code", "state"),
    [
        ("accept_bid", 200, "completed"),
        ("approve_task", 202, "claimed"),
        ("dispute_task", 200, "completed"),
    ],
)
async def test_service_validates_body_and_retains_actual_transport_status(
    command: str,
    status_code: int,
    state: str,
) -> None:
    body = _mutation_body(command, state)
    _install_agent(_TransportAgent(TaskBoardCommandResponse(status_code=status_code, body=body)))

    if command == "accept_bid":
        result = await proxy_service.accept_bid(
            task_id="t-ui-status",
            bid_id="bid-ui-status",
            operation_id=OPERATION_ID,
            expected_lifecycle_version=4,
        )
    elif command == "approve_task":
        result = await proxy_service.approve_task(
            task_id="t-ui-status",
            operation_id=OPERATION_ID,
            expected_lifecycle_version=4,
        )
    else:
        result = await proxy_service.file_dispute(
            task_id="t-ui-status",
            reason="The deliverable does not meet the specification.",
            operation_id=OPERATION_ID,
            expected_lifecycle_version=4,
        )

    assert result.status_code == status_code
    assert result.body == body


@pytest.mark.parametrize(
    ("status_code", "state"),
    [(200, "claimed"), (202, "completed"), (201, "completed")],
)
async def test_status_and_operation_state_contradictions_fail_closed(
    status_code: int,
    state: str,
) -> None:
    _install_agent(
        _TransportAgent(
            TaskBoardCommandResponse(
                status_code=status_code,
                body=_mutation_body("approve_task", state),
            )
        )
    )

    with pytest.raises(ServiceError) as exc_info:
        await proxy_service.approve_task(
            task_id="t-ui-status",
            operation_id=OPERATION_ID,
            expected_lifecycle_version=4,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "task_approval_failed"
    assert exc_info.value.message == "Task Board returned a malformed success response"
    assert exc_info.value.details == {}


async def test_sdk_contract_error_is_sanitized_as_ui_502() -> None:
    _install_agent(
        _TransportAgent(
            TaskBoardCommandContractError(
                status_code=200,
                reason="private upstream parser detail",
            )
        )
    )

    with pytest.raises(ServiceError) as exc_info:
        await proxy_service.approve_task(
            task_id="t-ui-status",
            operation_id=OPERATION_ID,
            expected_lifecycle_version=4,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "task_approval_failed"
    assert exc_info.value.message == "Task Board returned a malformed success response"
    assert exc_info.value.details == {}
    assert "private upstream parser detail" not in str(exc_info.value)


async def test_typed_transport_does_not_bypass_existing_body_validation() -> None:
    body = _mutation_body("approve_task", "completed")
    body["operation"]["task_id"] = "t-foreign"
    _install_agent(_TransportAgent(TaskBoardCommandResponse(status_code=200, body=body)))

    with pytest.raises(ServiceError) as exc_info:
        await proxy_service.approve_task(
            task_id="t-ui-status",
            operation_id=OPERATION_ID,
            expected_lifecycle_version=4,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "task_approval_failed"
    assert exc_info.value.message == "Task Board returned a malformed success response"


@pytest.mark.parametrize(
    ("command", "status_code", "state"),
    [
        ("accept_bid", 200, "completed"),
        ("approve_task", 202, "claimed"),
        ("dispute_task", 200, "completed"),
    ],
)
async def test_router_forwards_validated_body_with_exact_transport_status(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    status_code: int,
    state: str,
) -> None:
    transport_response = TaskBoardCommandResponse(
        status_code=status_code,
        body=_mutation_body(command, state),
    )

    async def result(**_kwargs: Any) -> TaskBoardCommandResponse:
        return transport_response

    request = NativeMutationRequest(
        operation_id=OPERATION_ID,
        expected_lifecycle_version=4,
    )
    if command == "accept_bid":
        monkeypatch.setattr(proxy_service, "accept_bid", result)
        response = await proxy_router.accept_bid(
            task_id="t-ui-status",
            bid_id="bid-ui-status",
            body=request,
        )
    elif command == "approve_task":
        monkeypatch.setattr(proxy_service, "approve_task", result)
        response = await proxy_router.approve_task(task_id="t-ui-status", body=request)
    else:
        monkeypatch.setattr(proxy_service, "file_dispute", result)
        response = await proxy_router.file_dispute(
            task_id="t-ui-status",
            body=FileDisputeRequest(
                reason="The deliverable does not meet the specification.",
                operation_id=OPERATION_ID,
                expected_lifecycle_version=4,
            ),
        )

    assert response.status_code == status_code
    assert json.loads(bytes(response.body)) == transport_response.body
