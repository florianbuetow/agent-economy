"""Proxy service — delegates task lifecycle operations to UserAgent."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Protocol, cast
from urllib.parse import quote

import httpx
from service_auth import TaskBoardCommandContractError, TaskBoardCommandResponse, public_key_to_b64
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import SQLITE_MAX_INTEGER, validate_operation_id
from service_auth.posting_journal import PostingJournalError, normalize_observation
from service_auth.task_postings import TaskPostingIntent
from service_commons.exceptions import ServiceError

from ui_service.core.state import get_app_state
from ui_service.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from service_auth import UserAgent

    from ui_service.services.task_board_reads import AuthoritativeTaskDetail

# Task Board's upload replay header (task_board_service.schemas.IDEMPOTENCY_KEY_HEADER).
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


class _AuthHeader(Protocol):
    def __call__(
        self,
        payload: dict[str, object],
        *,
        audience: str,
        resource: str,
    ) -> dict[str, str]: ...


class _RawRequest(Protocol):
    async def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response: ...


class _RawUpload(Protocol):
    async def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        content: bytes,
    ) -> httpx.Response: ...


class _OperatorAssetTransport:
    """Typed local bridge to the inherited UserAgent signing and raw-read methods."""

    def __init__(self, agent: UserAgent) -> None:
        self.auth_header: _AuthHeader = agent.__getattribute__("_auth_header")
        self.request_raw: _RawRequest = agent.__getattribute__("_request_raw")
        self.upload_raw: _RawUpload = agent.__getattribute__("_request_raw")
        self.sign_jws: _SignJws = agent.__getattribute__("_sign_jws")


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
_ATTACHMENT_DISPOSITION = re.compile(
    r'^attachment; filename="(?:[^"\\\x00-\x1f\x7f]|\\[\x20-\x7e])*"; '
    r"filename\*=UTF-8''[!#$&+\-.^_`|~%0-9A-Za-z]+$"
)


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
        "cancel_task": ("open", "cancelled", "bank_release"),
        "submit_deliverable": ("accepted", "submitted", "none"),
    }[command]
    if (
        operation["source_status"] != source_status
        or operation["target_status"] != target_status
        or operation["effect_kind"] != effect_kind
        or operation["source_lifecycle_version"] != expected_lifecycle_version
        or operation["target_lifecycle_version"] != expected_lifecycle_version + 1
        or (
            command in {"accept_bid", "submit_deliverable"}
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
        or (command == "cancel_task" and not isinstance(operation["effect_intent"], dict))
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


async def download_asset(task_id: str, asset_id: str) -> httpx.Response:
    """Fetch exact asset bytes through a configured-operator signed Task Board read."""
    agent = _get_user_agent()
    transport = _OperatorAssetTransport(agent)
    resource = f"GET /tasks/{task_id}/assets/{asset_id}"
    encoded_task_id = quote(task_id, safe="")
    encoded_asset_id = quote(asset_id, safe="")
    url = f"{agent.config.task_board_url}/tasks/{encoded_task_id}/assets/{encoded_asset_id}"
    headers = transport.auth_header(
        {"action": "download_asset", "task_id": task_id, "asset_id": asset_id},
        audience="task-board",
        resource=resource,
    )
    try:
        response = await transport.request_raw("GET", url, headers=headers)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, "asset_download_failed")
    except Exception as exc:
        _request_failed("asset_download_failed", exc)
    required_headers = ("content-type", "content-disposition")
    if any(not response.headers.get(name) for name in required_headers):
        raise ServiceError(
            "asset_download_failed",
            "Task Board returned malformed asset headers",
            502,
            {},
        )
    disposition = response.headers["content-disposition"]
    if _ATTACHMENT_DISPOSITION.fullmatch(disposition) is None:
        raise ServiceError(
            "asset_download_failed",
            "Task Board returned an unsafe asset filename header",
            502,
            {},
        )
    return response


def _posting_scope(agent: UserAgent) -> dict[str, object]:
    agent_id = agent.agent_id
    if not isinstance(agent_id, str) or agent_id == "":
        raise ServiceError(
            "user_agent_not_registered",
            "UserAgent is not registered with the Identity service.",
            503,
            {},
        )
    raw_public = base64.b64decode(public_key_to_b64(agent.config.public_key), validate=True)
    return {
        "schema_version": 1,
        "operator_agent_id": agent_id,
        "operator_public_fingerprint_sha256": hashlib.sha256(raw_public).hexdigest(),
        "service_urls": {
            "identity_url": agent.config.identity_url,
            "bank_url": agent.config.bank_url,
            "task_board_url": agent.config.task_board_url,
            "reputation_url": agent.config.reputation_url,
            "court_url": agent.config.court_url,
        },
    }


def prepare_task_posting(
    title: str,
    spec: str,
    reward: int,
    bidding_deadline_seconds: int,
    acceptance_window_seconds: int,
    execution_deadline_seconds: int,
    review_deadline_seconds: int,
) -> dict[str, object]:
    """Allocate a complete posting record without performing transport."""
    agent = _get_user_agent()
    try:
        intent = agent.prepare_task_posting(
            title=title,
            spec=spec,
            reward=reward,
            bidding_deadline_seconds=bidding_deadline_seconds,
            acceptance_window_seconds=acceptance_window_seconds,
            execution_deadline_seconds=execution_deadline_seconds,
            review_deadline_seconds=review_deadline_seconds,
        )
        return {
            "record_version": 1,
            "scope": _posting_scope(agent),
            "intent": intent.as_json(),
            "request_sha256": intent.request_sha256,
        }
    except ServiceError:
        raise
    except Exception as exc:
        _request_failed("task_posting_failed", exc)


def _validated_posting_record(
    *, operation_id: str, record: dict[str, object], agent: UserAgent
) -> TaskPostingIntent:
    try:
        validated_operation_id = validate_operation_id(operation_id)
        intent = TaskPostingIntent.from_json(record["intent"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError(
            "invalid_task_posting_record",
            "Task posting record is invalid",
            422,
            {},
        ) from exc
    if (
        record.get("record_version") != 1
        or record.get("scope") != _posting_scope(agent)
        or intent.operation_id != validated_operation_id
        or intent.actor_id != agent.agent_id
        or record.get("request_sha256") != intent.request_sha256
    ):
        raise ServiceError(
            "invalid_task_posting_record",
            "Task posting record does not match the current operator or intent",
            422,
            {},
        )
    return intent


def _posting_result(
    response: object,
    *,
    intent: TaskPostingIntent,
    observation: Literal["submit", "status"],
) -> tuple[int, dict[str, object]]:
    try:
        status_code, publication, _state = normalize_observation(
            response,
            intent=intent,
            observation=observation,
        )
    except (PostingJournalError, RuntimeError, TypeError, ValueError) as exc:
        raise ServiceError(
            "task_posting_failed",
            "Task Board returned a malformed posting acknowledgment",
            502,
            {},
        ) from exc
    return status_code, {
        "observation": observation,
        "status_code": status_code,
        "publication": publication,
    }


async def submit_task_posting(
    *, operation_id: str, record: dict[str, object]
) -> tuple[int, dict[str, object]]:
    """Submit an exact browser-owned posting intent."""
    agent = _get_user_agent()
    intent = _validated_posting_record(operation_id=operation_id, record=record, agent=agent)
    try:
        response = await agent.submit_task_posting(intent)
    except ServiceError:
        raise
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, "task_posting_failed")
    except Exception as exc:
        _request_failed("task_posting_failed", exc)
    return _posting_result(response, intent=intent, observation="submit")


async def get_task_posting_status(
    *, operation_id: str, record: dict[str, object]
) -> tuple[int, dict[str, object]]:
    """Read and bind an exact browser-owned posting intent."""
    agent = _get_user_agent()
    intent = _validated_posting_record(operation_id=operation_id, record=record, agent=agent)
    try:
        response = await agent.get_task_posting(operation_id)
    except ServiceError:
        raise
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, "task_posting_failed")
    except Exception as exc:
        _request_failed("task_posting_failed", exc)
    return _posting_result(response, intent=intent, observation="status")


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
        get_logger(__name__).warning(
            "Bid listing failed",
            extra={"task_id": task_id, "exception_type": type(exc).__name__, "error": str(exc)},
        )
        raise ServiceError(
            "bid_listing_failed",
            "Task Board could not list the bids for this task.",
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


async def _native_command(
    *,
    error_code: str,
    command: str,
    task_id: str,
    operation_id: str,
    expected_lifecycle_version: int,
    call: Callable[[], Awaitable[object]],
) -> TaskBoardCommandResponse:
    """Run one operator-signed native command and verify its acknowledgment."""
    try:
        result = await call()
        return _ensure_mutation_response(
            result,
            error_code,
            task_id=task_id,
            command=command,
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
    except ServiceError:
        raise
    except TaskBoardCommandContractError as exc:
        _task_board_contract_failed(error_code, exc)
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, error_code)
    except Exception as exc:
        _request_failed(error_code, exc)


async def cancel_task(
    task_id: str, operation_id: str, expected_lifecycle_version: int
) -> TaskBoardCommandResponse:
    """Cancel the operator's own open task; Task Board refunds the escrow."""
    agent = _get_user_agent()
    return await _native_command(
        error_code="task_cancellation_failed",
        command="cancel_task",
        task_id=task_id,
        operation_id=operation_id,
        expected_lifecycle_version=expected_lifecycle_version,
        call=lambda: agent.cancel_task(
            task_id,
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        ),
    )


async def submit_deliverable(
    task_id: str, operation_id: str, expected_lifecycle_version: int
) -> TaskBoardCommandResponse:
    """Submit the operator's uploaded deliverables for the poster's review."""
    agent = _get_user_agent()
    return await _native_command(
        error_code="deliverable_submission_failed",
        command="submit_deliverable",
        task_id=task_id,
        operation_id=operation_id,
        expected_lifecycle_version=expected_lifecycle_version,
        call=lambda: agent.submit_deliverable(
            task_id,
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        ),
    )


async def submit_bid(
    task_id: str, amount: int, operation_id: str, expected_lifecycle_version: int
) -> dict[str, Any]:
    """Place a binding operator bid and verify Task Board recorded exactly it."""
    agent = _get_user_agent()
    error_code = "bid_submission_failed"
    try:
        result = await agent.submit_bid(
            task_id,
            amount,
            operation_id=operation_id,
            expected_lifecycle_version=expected_lifecycle_version,
        )
    except ServiceError:
        raise
    except TaskBoardCommandContractError as exc:
        _task_board_contract_failed(error_code, exc)
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, error_code)
    except Exception as exc:
        _request_failed(error_code, exc)
    body = result.body
    if (
        result.status_code != 201
        or not isinstance(body.get("bid_id"), str)
        or body.get("task_id") != task_id
        or body.get("bidder_id") != agent.agent_id
        or body.get("amount") != amount
        or not isinstance(body.get("submitted_at"), str)
    ):
        _invalid_response(error_code)
    return body


async def upload_asset(
    task_id: str,
    *,
    content: bytes,
    content_type: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Forward one multipart upload to Task Board signed as the operator."""
    agent = _get_user_agent()
    transport = _OperatorAssetTransport(agent)
    error_code = "asset_upload_failed"
    url = f"{agent.config.task_board_url}/tasks/{quote(task_id, safe='')}/assets"
    headers = transport.auth_header(
        {"action": "upload_asset", "task_id": task_id},
        audience="task-board",
        resource=f"POST /tasks/{task_id}/assets",
    )
    headers["Content-Type"] = content_type
    if idempotency_key is not None:
        headers[IDEMPOTENCY_KEY_HEADER] = idempotency_key
    try:
        response = await transport.upload_raw("POST", url, headers=headers, content=content)
        response.raise_for_status()
        body: object = response.json()
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, error_code)
    except Exception as exc:
        _request_failed(error_code, exc)
    if response.status_code != 201 or not isinstance(body, dict):
        _invalid_response(error_code)
    return cast("dict[str, Any]", body)


async def submit_rebuttal(task: AuthoritativeTaskDetail, rebuttal: str) -> dict[str, Any]:
    """Rebut the dispute recorded on the operator's own disputed task."""
    agent = _get_user_agent()
    dispute_id = (task.model_extra or {}).get("dispute_id")
    if task.status != "disputed" or not isinstance(dispute_id, str) or dispute_id == "":
        raise ServiceError(
            "invalid_status",
            "Only a disputed task with a recorded dispute can be rebutted",
            409,
            {"task_id": task.task_id, "status": task.status},
        )
    if task.worker_id != agent.agent_id:
        raise ServiceError("forbidden", "Only the task's worker can submit a rebuttal", 403, {})
    error_code = "rebuttal_submission_failed"
    try:
        result = await agent.submit_worker_rebuttal(task.task_id, dispute_id, rebuttal)
    except ServiceError:
        raise
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, error_code)
    except Exception as exc:
        _request_failed(error_code, exc)
    return result


_FEEDBACK_ELIGIBLE_STATUSES = frozenset({"approved", "ruled"})


async def submit_feedback(
    task: AuthoritativeTaskDetail, rating: str, comment: str | None
) -> dict[str, Any]:
    """Rate the counterpart: a poster rates delivery, a worker rates the spec."""
    agent = _get_user_agent()
    if agent.agent_id == task.poster_id and task.worker_id is not None:
        to_agent_id, category = task.worker_id, "delivery_quality"
    elif agent.agent_id is not None and agent.agent_id == task.worker_id:
        to_agent_id, category = task.poster_id, "spec_quality"
    else:
        raise ServiceError(
            "not_task_party",
            "Feedback must be between the task's poster and worker",
            403,
            {"task_id": task.task_id},
        )
    if task.status not in _FEEDBACK_ELIGIBLE_STATUSES:
        raise ServiceError(
            "task_not_eligible",
            "Feedback is accepted only for approved or ruled tasks",
            409,
            {"task_id": task.task_id, "status": task.status},
        )
    error_code = "feedback_submission_failed"
    try:
        result = await agent.submit_feedback(
            task_id=task.task_id,
            to_agent_id=to_agent_id,
            category=category,
            rating=rating,
            comment=comment,
        )
    except ServiceError:
        raise
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, error_code)
    except Exception as exc:
        _request_failed(error_code, exc)
    return result


# ---------------------------------------------------------------------------
# Protocol-2 posting with input attachments (lane U, completion plan 4.4 / G03).
# Used only when the browser attaches files; the protocol-1 flow above is unchanged.
# ---------------------------------------------------------------------------

_INPUT_ADMIT_RESOURCE = "POST /task-publications"
_INPUT_ENVELOPE_STATES = frozenset(
    {"awaiting_inputs", "awaiting_publish", "awaiting_lock", "published", "blocked_integrity"}
)


class _SignJws(Protocol):
    def __call__(self, payload: dict[str, object], *, audience: str, resource: str) -> str: ...


def _canonical_sha256(value: object) -> str:
    rendered = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _invalid_input_record(message: str) -> NoReturn:
    raise ServiceError("invalid_task_posting_record", message, 422, {})


def prepare_input_posting(
    *,
    title: str,
    spec: str,
    reward: int,
    bidding_deadline_seconds: int,
    acceptance_window_seconds: int,
    execution_deadline_seconds: int,
    review_deadline_seconds: int,
    expected_input_count: int,
) -> dict[str, object]:
    """Allocate a protocol-2 record whose intent is the exact Gateway admission request."""
    agent = _get_user_agent()
    scope = _posting_scope(agent)
    agent_id = cast("str", scope["operator_agent_id"])
    task_id = f"t-{uuid.uuid4()}"
    intent: dict[str, object] = {
        "protocol_version": 2,
        "publication_operation_id": f"op-{uuid.uuid4()}",
        "task_id": task_id,
        "actor_kind": "agent",
        "actor_id": agent_id,
        "action": "task_publication_admit",
        "resource": _INPUT_ADMIT_RESOURCE,
        "lock_operation_id": operation_id_for("task-lock", (task_id, agent_id)),
        "title": title,
        "spec": spec,
        "reward": reward,
        "bidding_deadline_seconds": bidding_deadline_seconds,
        "acceptance_window_seconds": acceptance_window_seconds,
        "execution_deadline_seconds": execution_deadline_seconds,
        "review_deadline_seconds": review_deadline_seconds,
        "expected_input_count": expected_input_count,
    }
    return {
        "record_version": 2,
        "scope": scope,
        "intent": intent,
        "request_sha256": _canonical_sha256(intent),
    }


def _validated_input_record(
    *, publication_operation_id: str, record: dict[str, object], agent: UserAgent
) -> dict[str, object]:
    intent = record.get("intent")
    if not isinstance(intent, dict):
        _invalid_input_record("Task posting record is invalid")
    intent = cast("dict[str, object]", intent)
    task_id = intent.get("task_id")
    operator_id = agent.agent_id
    if (
        record.get("record_version") != 2
        or record.get("scope") != _posting_scope(agent)
        or intent.get("publication_operation_id") != publication_operation_id
        or not isinstance(operator_id, str)
        or intent.get("actor_id") != operator_id
        or not isinstance(task_id, str)
        or intent.get("lock_operation_id") != operation_id_for("task-lock", (task_id, operator_id))
        or record.get("request_sha256") != _canonical_sha256(intent)
    ):
        _invalid_input_record("Task posting record does not match the current operator or intent")
    return intent


def _input_envelope(response: httpx.Response, record: dict[str, object]) -> dict[str, object]:
    body: object = response.json()
    intent = cast("dict[str, object]", record["intent"])
    if (
        not isinstance(body, dict)
        or body.get("protocol_version") != 2
        or body.get("publication_operation_id") != intent["publication_operation_id"]
        or body.get("task_id") != intent["task_id"]
        or body.get("request_sha256") != record["request_sha256"]
        or body.get("publication_state") not in _INPUT_ENVELOPE_STATES
    ):
        _invalid_response("task_posting_failed")
    return cast("dict[str, object]", body)


async def _input_post(
    transport: _OperatorAssetTransport,
    url: str,
    *,
    json_body: dict[str, object],
    ok_statuses: frozenset[int],
) -> httpx.Response:
    error_code = "task_posting_failed"
    try:
        response = await transport.upload_raw(
            "POST",
            url,
            headers={"Content-Type": "application/json"},
            content=json.dumps(json_body).encode("utf-8"),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, error_code)
    except Exception as exc:
        _request_failed(error_code, exc)
    if response.status_code not in ok_statuses:
        _invalid_response(error_code)
    return response


async def admit_input_posting(
    *, publication_operation_id: str, record: dict[str, object]
) -> dict[str, object]:
    """Admit the exact signed intent; replays return the same publication."""
    agent = _get_user_agent()
    intent = _validated_input_record(
        publication_operation_id=publication_operation_id, record=record, agent=agent
    )
    transport = _OperatorAssetTransport(agent)
    payload: dict[str, object] = {
        "action": "task_publication_admit",
        "publication_operation_id": intent["publication_operation_id"],
        "task_id": intent["task_id"],
        "poster_id": intent["actor_id"],
        **{
            field: intent[field]
            for field in (
                "title",
                "spec",
                "reward",
                "bidding_deadline_seconds",
                "acceptance_window_seconds",
                "execution_deadline_seconds",
                "review_deadline_seconds",
                "expected_input_count",
            )
        },
    }
    token = transport.sign_jws(payload, audience="task-board", resource=_INPUT_ADMIT_RESOURCE)
    response = await _input_post(
        transport,
        f"{agent.config.task_board_url}/task-publications",
        json_body={"token": token},
        ok_statuses=frozenset({202}),
    )
    return _input_envelope(response, record)


async def upload_posting_input(
    publication_operation_id: str,
    *,
    content: bytes,
    content_type: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Forward one multipart input file into the operator's draft input manifest."""
    agent = _get_user_agent()
    publication_id = validate_operation_id(publication_operation_id)
    transport = _OperatorAssetTransport(agent)
    error_code = "input_upload_failed"
    resource = f"POST /task-publications/{publication_id}/inputs"
    headers = transport.auth_header(
        {
            "action": "task_publication_upload_input",
            "publication_operation_id": publication_id,
            "poster_id": cast("str", agent.agent_id),
        },
        audience="task-board",
        resource=resource,
    )
    headers["Content-Type"] = content_type
    if idempotency_key is not None:
        headers[IDEMPOTENCY_KEY_HEADER] = idempotency_key
    url = f"{agent.config.task_board_url}/task-publications/{quote(publication_id, safe='')}/inputs"
    try:
        response = await transport.upload_raw("POST", url, headers=headers, content=content)
        response.raise_for_status()
        body: object = response.json()
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, error_code)
    except Exception as exc:
        _request_failed(error_code, exc)
    if response.status_code != 201 or not isinstance(body, dict):
        _invalid_response(error_code)
    return cast("dict[str, Any]", body)


async def publish_input_posting(
    *, publication_operation_id: str, record: dict[str, object]
) -> tuple[int, dict[str, object]]:
    """Freeze inputs, lock escrow and publish; 201 once published, 202 while pending."""
    agent = _get_user_agent()
    intent = _validated_input_record(
        publication_operation_id=publication_operation_id, record=record, agent=agent
    )
    transport = _OperatorAssetTransport(agent)
    publication_id = cast("str", intent["publication_operation_id"])
    task_token = transport.sign_jws(
        {
            "action": "task_publication_publish",
            "publication_operation_id": publication_id,
            "task_id": intent["task_id"],
            "poster_id": intent["actor_id"],
        },
        audience="task-board",
        resource=f"POST /task-publications/{publication_id}/publish",
    )
    escrow_token = transport.sign_jws(
        {
            "action": "escrow_lock",
            "operation_id": intent["lock_operation_id"],
            "task_id": intent["task_id"],
            "agent_id": intent["actor_id"],
            "amount": intent["reward"],
        },
        audience="central-bank",
        resource="POST /escrow/lock",
    )
    response = await _input_post(
        transport,
        f"{agent.config.task_board_url}/task-publications/{quote(publication_id, safe='')}/publish",
        json_body={"task_token": task_token, "escrow_token": escrow_token},
        ok_statuses=frozenset({201, 202}),
    )
    return response.status_code, _input_envelope(response, record)


async def get_task_inputs(task_id: str) -> dict[str, Any]:
    """Read a task's public frozen input manifest from Task Board."""
    agent = _get_user_agent()
    transport = _OperatorAssetTransport(agent)
    error_code = "task_inputs_unavailable"
    url = f"{agent.config.task_board_url}/tasks/{quote(task_id, safe='')}/inputs"
    try:
        response = await transport.request_raw("GET", url, headers={})
        response.raise_for_status()
        body: object = response.json()
    except httpx.HTTPStatusError as exc:
        _raise_task_board_error(exc, error_code)
    except Exception as exc:
        _request_failed(error_code, exc)
    if not isinstance(body, dict) or body.get("task_id") != task_id:
        _invalid_response(error_code)
    return cast("dict[str, Any]", body)
