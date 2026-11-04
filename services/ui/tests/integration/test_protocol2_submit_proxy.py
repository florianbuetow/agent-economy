"""Governed protocol-2 submissions remain valid through the operator proxy."""

from __future__ import annotations

from typing import Any

import pytest
from service_auth.task_board_responses import TaskBoardCommandResponse

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration

_TASK_ID = "t-protocol2-submit"
_OPERATION_ID = "op-00000000-0000-4000-8000-0000000002a0"


class _Protocol2Operator:
    agent_id = "a-operator"

    async def submit_deliverable(
        self,
        task_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        assert task_id == _TASK_ID
        assert operation_id == _OPERATION_ID
        assert expected_lifecycle_version == 4
        return TaskBoardCommandResponse(
            200,
            {
                "task_id": task_id,
                "status": "submitted",
                "lifecycle_version": 5,
                "operation": {
                    "protocol_version": 2,
                    "operation_id": operation_id,
                    "task_id": task_id,
                    "command": "submit_deliverable",
                    "request_sha256": "a" * 64,
                    "operation_state": "completed",
                    "source_status": "accepted",
                    "source_lifecycle_version": 4,
                    "target_status": "submitted",
                    "target_lifecycle_version": 5,
                    "effect_kind": "none",
                    "effect_state": "none",
                    "effect_operation_id": None,
                    "effect_result_reference": None,
                    "effect_intent": None,
                    "event_id": 22,
                    "result": {
                        "task_id": task_id,
                        "command": "submit_deliverable",
                        "status": "submitted",
                        "lifecycle_version": 5,
                        "event_id": 22,
                        "worker_id": "a-operator",
                        "manifest_id": "manifest-output",
                        "manifest_sha256": "b" * 64,
                    },
                },
            },
        )


@pytest.mark.asyncio
async def test_protocol2_submission_success_passes_through_proxy(client: Any) -> None:
    get_app_state().user_agent = _Protocol2Operator()  # type: ignore[assignment]

    response = await client.post(
        f"/api/proxy/tasks/{_TASK_ID}/submit",
        json={
            "operation_id": _OPERATION_ID,
            "expected_lifecycle_version": 4,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "submitted"
    assert body["operation"]["protocol_version"] == 2
    assert body["operation"]["result"]["manifest_id"] == "manifest-output"


class _MalformedProtocol2Operator(_Protocol2Operator):
    async def submit_deliverable(
        self,
        task_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        response = await super().submit_deliverable(
            task_id,
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
        result = response.body["operation"]["result"]
        assert isinstance(result, dict)
        result.pop("manifest_sha256")
        return response


class _WrongWorkerProtocol2Operator(_Protocol2Operator):
    async def submit_deliverable(
        self,
        task_id: str,
        *,
        operation_id: str,
        expected_lifecycle_version: int,
    ) -> TaskBoardCommandResponse:
        response = await super().submit_deliverable(
            task_id,
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
        result = response.body["operation"]["result"]
        assert isinstance(result, dict)
        result["worker_id"] = "a-different-worker"
        return response


@pytest.mark.asyncio
async def test_protocol2_submission_requires_complete_manifest_binding(client: Any) -> None:
    get_app_state().user_agent = _MalformedProtocol2Operator()  # type: ignore[assignment]

    response = await client.post(
        f"/api/proxy/tasks/{_TASK_ID}/submit",
        json={
            "operation_id": _OPERATION_ID,
            "expected_lifecycle_version": 4,
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "error": "deliverable_submission_failed",
        "message": "Task Board returned a malformed success response",
        "details": {},
    }


@pytest.mark.asyncio
async def test_protocol2_submission_worker_must_match_operator(client: Any) -> None:
    get_app_state().user_agent = _WrongWorkerProtocol2Operator()  # type: ignore[assignment]

    response = await client.post(
        f"/api/proxy/tasks/{_TASK_ID}/submit",
        json={
            "operation_id": _OPERATION_ID,
            "expected_lifecycle_version": 4,
        },
    )

    assert response.status_code == 502
    assert response.json()["error"] == "deliverable_submission_failed"
