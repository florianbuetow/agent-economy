"""Validate Task Board acknowledgments for one native Court ruling attempt."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    import httpx

_MAX_INTEGER = (1 << 63) - 1
_HASH_LENGTH = 64


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Invalid Task Board ruling acknowledgment")


def _strict_integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("Invalid strict integer")
    return value


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected mapping")
    return value


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _validate_effect_intent(
    operation: dict[str, Any],
    *,
    task_id: str,
    ruling_id: str,
    poster_id: str,
    worker_id: str,
    worker_pct: int,
    reward: int,
) -> None:
    intent = _mapping(operation.get("effect_intent"))
    _require(set(intent) == {"financial_request", "financial_request_sha256", "expected_escrow"})
    escrow = _mapping(intent.get("expected_escrow"))
    _require(set(escrow) == {"escrow_id", "task_id", "payer_account_id", "amount"})
    escrow_id = escrow.get("escrow_id")
    _require(isinstance(escrow_id, str) and bool(escrow_id))
    _require(escrow.get("task_id") == task_id)
    _require(escrow.get("payer_account_id") == poster_id)
    _require(_strict_integer(escrow.get("amount"), 1, _MAX_INTEGER) == reward)

    financial = _mapping(intent.get("financial_request"))
    _require(intent.get("financial_request_sha256") == _canonical_hash(financial))
    common = {
        "request_version": 1,
        "escrow_id": escrow_id,
        "source_reference": f"task:{task_id}:ruling:{ruling_id}",
    }
    if worker_pct in {0, 100}:
        expected = {
            **common,
            "operation_kind": "escrow_release",
            "recipient_account_id": worker_id if worker_pct == 100 else poster_id,
        }
        _require(operation.get("effect_kind") == "bank_release")
    else:
        expected = {
            **common,
            "operation_kind": "escrow_split",
            "worker_account_id": worker_id,
            "poster_account_id": poster_id,
            "worker_pct": worker_pct,
        }
        _require(operation.get("effect_kind") == "bank_split")
    _require(financial == expected)


def _validate_result(
    operation: dict[str, Any],
    *,
    operation_id: str,
    task_id: str,
    target_version: int,
    ruling_id: str,
    worker_id: str,
    worker_pct: int,
    ruling_summary: str,
    reward: int,
) -> None:
    event_id = _strict_integer(operation.get("event_id"), 1, _MAX_INTEGER)
    result = _mapping(operation.get("result"))
    expected = {
        "task_id": task_id,
        "command": "record_ruling",
        "status": "ruled",
        "lifecycle_version": target_version,
        "event_id": event_id,
        "effect_result_reference": operation_id,
        "worker_id": worker_id,
        "ruling_id": ruling_id,
        "worker_pct": worker_pct,
        "summary": ruling_summary,
        "reward": reward,
    }
    _require(result == expected)


def validate_ruling_acknowledgment(
    response: object,
    *,
    operation_id: str,
    task_id: str,
    source_version: int,
    ruling_id: str,
    poster_id: str,
    worker_id: str,
    worker_pct: int,
    ruling_summary: str,
    reward: int,
) -> None:
    """Require a completed immutable result for the exact attempted ruling."""
    try:
        body = _mapping(response)
        _require(body.get("task_id") == task_id)
        operation = _mapping(body.get("operation"))
        target_version = source_version + 1
        request_hash = operation.get("request_sha256")
        _require(type(operation.get("protocol_version")) is int)
        _require(operation.get("protocol_version") == 1)
        _require(operation.get("operation_id") == operation_id)
        _require(operation.get("task_id") == task_id)
        _require(operation.get("command") == "record_ruling")
        _require(
            isinstance(request_hash, str)
            and len(request_hash) == _HASH_LENGTH
            and all(character in "0123456789abcdef" for character in request_hash)
        )
        _require(operation.get("operation_state") == "completed")
        _require(operation.get("source_status") == "disputed")
        _require(
            _strict_integer(operation.get("source_lifecycle_version"), 0, _MAX_INTEGER)
            == source_version
        )
        _require(operation.get("target_status") == "ruled")
        _require(
            _strict_integer(operation.get("target_lifecycle_version"), 0, _MAX_INTEGER)
            == target_version
        )
        _require(operation.get("effect_state") == "succeeded")
        _require(operation.get("effect_operation_id") == operation_id)
        _require(operation.get("effect_result_reference") == operation_id)
        _validate_effect_intent(
            operation,
            task_id=task_id,
            ruling_id=ruling_id,
            poster_id=poster_id,
            worker_id=worker_id,
            worker_pct=worker_pct,
            reward=reward,
        )
        _validate_result(
            operation,
            operation_id=operation_id,
            task_id=task_id,
            target_version=target_version,
            ruling_id=ruling_id,
            worker_id=worker_id,
            worker_pct=worker_pct,
            ruling_summary=ruling_summary,
            reward=reward,
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ServiceError(
            "invalid_task_board_ack",
            "Task Board returned an invalid ruling acknowledgment",
            502,
            {},
        ) from exc


def task_version_conflict_from_http_error(
    exc: httpx.HTTPStatusError,
    *,
    expected_version: int,
) -> ServiceError | None:
    """Preserve one fully validated native stale-version conflict."""
    if exc.response.status_code != 409:
        return None
    try:
        body = _mapping(exc.response.json())
        _require(set(body) == {"error", "message", "details"})
        _require(body.get("error") == "task_version_conflict")
        message_value = body.get("message")
        if not isinstance(message_value, str) or not message_value:
            raise ValueError("Invalid error message")
        message = message_value
        details = _mapping(body.get("details"))
        _require(
            set(details)
            == {"expected_lifecycle_version", "actual_lifecycle_version", "current_status"}
        )
        observed_expected = _strict_integer(
            details.get("expected_lifecycle_version"), 0, _MAX_INTEGER
        )
        actual = _strict_integer(details.get("actual_lifecycle_version"), 0, _MAX_INTEGER)
        current_status = details.get("current_status")
        _require(observed_expected == expected_version and actual != observed_expected)
        _require(isinstance(current_status, str) and bool(current_status))
    except (TypeError, ValueError, OverflowError):
        return None
    return ServiceError("task_version_conflict", message, 409, details)
