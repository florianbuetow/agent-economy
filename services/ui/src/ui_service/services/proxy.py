"""Proxy service — delegates task lifecycle operations to UserAgent."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn, cast

import httpx
from service_auth import TaskBoardCommandContractError, TaskBoardCommandResponse
from service_auth.financial_responses import SQLITE_MAX_INTEGER, validate_operation_id
from service_commons.exceptions import ServiceError

from ui_service.core.state import get_app_state

if TYPE_CHECKING:
    from service_auth import UserAgent

_OPERATION_FIELDS = {
    "protocol_version",
    "operation_id",
    "task_id",
    "command",
    "request_sha256",
    "operation_state",
    "source_status",
    "source_lifecycle_version",
    "target_status",
    "target_lifecycle_version",
    "effect_kind",
    "effect_state",
    "effect_operation_id",
    "effect_result_reference",
    "effect_intent",
    "event_id",
    "result",
}


def _get_user_agent() -> UserAgent:
    """Get the UserAgent from app state, raising ServiceError if unavailable."""
    state = get_app_state()
    if state.user_agent is None:
        raise ServiceError(
            "user_agent_unavailable",
            "UserAgent is not initialized. Task lifecycle operations are unavailable.",
            503,
            {},
        )
    return state.user_agent


def _ensure_dict_response(value: Any, error_code: str) -> dict[str, Any]:
    """Ensure proxy operations return object payloads."""
    if not isinstance(value, dict):
        raise ServiceError(
            error_code,
            "Unexpected response type from task-board operation.",
            502,
            {},
        )
    return value


def _invalid_response(error_code: str) -> NoReturn:
    raise ServiceError(
        error_code,
        "Task Board returned a malformed success response",
        502,
        {},
    )


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= SQLITE_MAX_INTEGER


def _validate_operation_state(operation: dict[str, Any], error_code: str) -> str:
    state = operation["operation_state"]
    if state == "claimed":
        if (
            operation["effect_kind"] == "none"
            or operation["effect_state"] != "pending"
            or operation["event_id"] is not None
            or operation["result"] is not None
        ):
            _invalid_response(error_code)
    elif state == "completed":
        if operation["event_id"] is None or operation["result"] is None:
            _invalid_response(error_code)
        if operation["effect_kind"] == "none" and operation["effect_state"] != "none":
            _invalid_response(error_code)
        if operation["effect_kind"] != "none" and operation["effect_state"] != "succeeded":
            _invalid_response(error_code)
    else:
        _invalid_response(error_code)
    return cast("str", state)


def _validate_operation(
    operation: dict[str, Any],
    error_code: str,
    *,
    task_id: str,
    command: str,
    operation_id: str,
    expected_lifecycle_version: int,
) -> str:
    try:
        response_operation_id = validate_operation_id(operation["operation_id"])
    except ValueError:
        _invalid_response(error_code)
    if (
        type(operation["protocol_version"]) is not int
        or operation["protocol_version"] != 1
        or response_operation_id != operation_id
        or operation["task_id"] != task_id
        or operation["command"] != command
        or not isinstance(operation["request_sha256"], str)
        or len(operation["request_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in operation["request_sha256"])
        or not isinstance(operation["source_status"], str)
        or not operation["source_status"]
        or not isinstance(operation["target_status"], str)
        or not operation["target_status"]
        or not _integer(operation["source_lifecycle_version"])
        or not _integer(operation["target_lifecycle_version"])
        or operation["target_lifecycle_version"] < operation["source_lifecycle_version"]
        or operation["effect_kind"] not in {"none", "bank_release", "bank_split", "court_claim"}
        or operation["effect_state"] not in {"none", "pending", "succeeded"}
        or (
            operation["effect_operation_id"] is not None
            and not isinstance(operation["effect_operation_id"], str)
        )
        or (
            operation["effect_result_reference"] is not None
            and not isinstance(operation["effect_result_reference"], str)
        )
        or (
            operation["effect_intent"] is not None
            and not isinstance(operation["effect_intent"], dict)
        )
        or (
            operation["event_id"] is not None
            and (not _integer(operation["event_id"]) or operation["event_id"] == 0)
        )
        or (operation["result"] is not None and not isinstance(operation["result"], dict))
    ):
        _invalid_response(error_code)
    source_status, target_status, effect_kind = {
        "accept_bid": ("open", "accepted", "none"),
        "approve_task": ("submitted", "approved", "bank_release"),
        "dispute_task": ("submitted", "disputed", "court_claim"),
    }[command]
    if (
        operation["source_status"] != source_status
        or operation["target_status"] != target_status
        or operation["effect_kind"] != effect_kind
        or operation["source_lifecycle_version"] != expected_lifecycle_version
        or operation["target_lifecycle_version"] != expected_lifecycle_version + 1
        or (
            command == "accept_bid"
            and any(
                operation[field] is not None
                for field in ("effect_operation_id", "effect_result_reference", "effect_intent")
            )
        )
        or (
            command == "approve_task"
            and (
                operation["effect_operation_id"] != operation_id
                or not isinstance(operation["effect_intent"], dict)
            )
        )
        or (
            command == "dispute_task"
            and (
                operation["effect_operation_id"] is not None
                or not isinstance(operation["effect_intent"], dict)
            )
        )
    ):
        _invalid_response(error_code)
    return _validate_operation_state(operation, error_code)


def _ensure_mutation_response(
    value: object,
    error_code: str,
    *,
    task_id: str,
    command: str,
    operation_id: str,
    expected_lifecycle_version: int,
) -> TaskBoardCommandResponse:
    """Validate the body while retaining the SDK's observed HTTP status."""
    if not isinstance(value, TaskBoardCommandResponse):
        _invalid_response(error_code)
    body = value.body
    if "task" in body:
        _invalid_response(error_code)
    operation = body.get("operation")
    if not isinstance(operation, dict) or set(operation) != _OPERATION_FIELDS:
        _invalid_response(error_code)
    state = _validate_operation(
        operation,
        error_code,
        task_id=task_id,
        command=command,
        operation_id=operation_id,
        expected_lifecycle_version=expected_lifecycle_version,
    )
    if (
        body.get("task_id") != task_id
        or not isinstance(body.get("status"), str)
        or not body["status"]
        or not _integer(body.get("lifecycle_version"))
    ):
        _invalid_response(error_code)
    if state == "claimed" and body["lifecycle_version"] != operation["source_lifecycle_version"]:
        _invalid_response(error_code)
    if state == "completed" and body["lifecycle_version"] < operation["target_lifecycle_version"]:
        _invalid_response(error_code)
    expected_status_code = 202 if state == "claimed" else 200
    if type(value.status_code) is not int or value.status_code != expected_status_code:
        _invalid_response(error_code)
    return value


def _raise_task_board_error(exc: httpx.HTTPStatusError, error_code: str) -> NoReturn:
    response = exc.response
    try:
        body: object = response.json()
    except (ValueError, TypeError):
        body = None
    if (
        400 <= response.status_code < 500
        and isinstance(body, dict)
        and set(body) == {"error", "message", "details"}
        and isinstance(body["error"], str)
        and bool(body["error"])
        and isinstance(body["message"], str)
        and bool(body["message"])
        and isinstance(body["details"], dict)
    ):
        raise ServiceError(
            body["error"],
            body["message"],
            response.status_code,
            body["details"],
        ) from exc
    raise ServiceError(
        error_code,
        "Task Board returned a malformed error response",
        502,
        {},
    ) from exc


def _request_failed(error_code: str, exc: Exception) -> NoReturn:
    raise ServiceError(error_code, "Task Board request failed", 502, {}) from exc


def _task_board_contract_failed(error_code: str, exc: TaskBoardCommandContractError) -> NoReturn:
    raise ServiceError(
        error_code,
        "Task Board returned a malformed success response",
        502,
        {},
    ) from exc


async def get_identity() -> str:
    """Return the UserAgent's agent_id."""
    agent = _get_user_agent()
    agent_id = agent.agent_id
    if not isinstance(agent_id, str) or agent_id == "":
        raise ServiceError(
            "user_agent_not_registered",
            "UserAgent is not registered with the Identity service.",
            503,
            {},
        )
    return agent_id


async def create_task(
    title: str,
    spec: str,
    reward: int,
    bidding_deadline_seconds: int,
    acceptance_window_seconds: int,
    execution_deadline_seconds: int,
    review_deadline_seconds: int,
) -> dict[str, Any]:
    """Post a new task via UserAgent."""
    agent = _get_user_agent()
    try:
        result = await agent.post_task(
            title=title,
            spec=spec,
            reward=reward,
            bidding_deadline_seconds=bidding_deadline_seconds,
            acceptance_window_seconds=acceptance_window_seconds,
            execution_deadline_seconds=execution_deadline_seconds,
            review_deadline_seconds=review_deadline_seconds,
        )
        return _ensure_dict_response(result, "task_creation_failed")
    except ServiceError:
        raise
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, "task_creation_failed")
    except Exception as exc:
        _request_failed("task_creation_failed", exc)


async def list_bids(task_id: str) -> list[dict[str, Any]]:
    """List bids through the operator's poster-authorized Task Board client."""
    agent = _get_user_agent()
    untyped_agent: Any = agent
    try:
        result: object = await untyped_agent.list_bids(task_id=task_id)
    except ServiceError:
        raise
    except Exception as exc:
        response: Any = getattr(exc, "response", None)
        if getattr(response, "status_code", None) == 403:
            try:
                body = response.json()
            except Exception:
                body = None
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("error"), str)
                or not isinstance(body.get("message"), str)
                or not isinstance(body.get("details"), dict)
            ):
                raise ServiceError(
                    "bid_listing_failed",
                    "Task Board returned a malformed authorization error.",
                    502,
                    {},
                ) from exc
            raise ServiceError(
                body["error"],
                body["message"],
                403,
                body["details"],
            ) from exc
        raise ServiceError(
            "bid_listing_failed",
            f"Failed to list bids: {exc}",
            502,
            {},
        ) from exc
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        raise ServiceError(
            "bid_listing_failed",
            "Unexpected response type from task-board operation.",
            502,
            {},
        )
    return cast("list[dict[str, Any]]", result)


async def accept_bid(
    task_id: str,
    bid_id: str,
    operation_id: str,
    expected_lifecycle_version: int,
) -> TaskBoardCommandResponse:
    """Accept a bid on a task via UserAgent."""
    agent = _get_user_agent()
    try:
        result = await agent.accept_bid(
            task_id=task_id,
            bid_id=bid_id,
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
        return _ensure_mutation_response(
            result,
            "bid_acceptance_failed",
            task_id=task_id,
            command="accept_bid",
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
    except ServiceError:
        raise
    except TaskBoardCommandContractError as exc:
        _task_board_contract_failed("bid_acceptance_failed", exc)
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, "bid_acceptance_failed")
    except Exception as exc:
        _request_failed("bid_acceptance_failed", exc)


async def approve_task(
    task_id: str,
    operation_id: str,
    expected_lifecycle_version: int,
) -> TaskBoardCommandResponse:
    """Approve a submitted task via UserAgent."""
    agent = _get_user_agent()
    try:
        result = await agent.approve_task(
            task_id=task_id,
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
        return _ensure_mutation_response(
            result,
            "task_approval_failed",
            task_id=task_id,
            command="approve_task",
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
    except ServiceError:
        raise
    except TaskBoardCommandContractError as exc:
        _task_board_contract_failed("task_approval_failed", exc)
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, "task_approval_failed")
    except Exception as exc:
        _request_failed("task_approval_failed", exc)


async def file_dispute(
    task_id: str,
    reason: str,
    operation_id: str,
    expected_lifecycle_version: int,
) -> TaskBoardCommandResponse:
    """File a dispute on a task via UserAgent."""
    agent = _get_user_agent()
    try:
        result = await agent.dispute_task(
            task_id=task_id,
            reason=reason,
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
        return _ensure_mutation_response(
            result,
            "dispute_filing_failed",
            task_id=task_id,
            command="dispute_task",
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
    except ServiceError:
        raise
    except TaskBoardCommandContractError as exc:
        _task_board_contract_failed("dispute_filing_failed", exc)
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, "dispute_filing_failed")
    except Exception as exc:
        _request_failed("dispute_filing_failed", exc)
