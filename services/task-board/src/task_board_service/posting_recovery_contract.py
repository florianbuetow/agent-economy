"""Strict private wire contracts for MP-05 posting recovery."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal, cast

from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import SQLITE_MAX_INTEGER, validate_operation_id
from service_commons.exceptions import ServiceError

_OBSERVATION_ID = re.compile(r"^obs-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$")
_PAGE_KEYS = frozenset({"items", "next_after"})
_ITEM_KEYS = frozenset({"cursor", "kind", "identity"})
_ENVELOPE_KEYS = frozenset(
    {
        "protocol_version",
        "observation_id",
        "task_id",
        "escrow_id",
        "poster_id",
        "amount",
        "observed_fact_sha256",
        "observation_state",
        "refund_operation_id",
        "refund_request",
        "refund_request_sha256",
        "refund_receipt_event_id",
        "refunded_at",
        "result",
        "error",
    }
)
_BLOCK_ERROR = "posting_refund_integrity_blocked"
_BLOCK_MESSAGE = "Posting refund evidence requires reconciliation"
_BLOCK_REASONS = frozenset(
    {
        "observation_invalid",
        "receipt_invalid",
        "escrow_invalid",
        "settlement_conflict",
        "task_escrow_reference_conflict",
    }
)


def _invalid_request(message: str) -> ServiceError:
    return ServiceError("invalid_payload", message, 400, {})


def _invalid_response() -> ServiceError:
    return ServiceError("invalid_gateway_response", "Gateway returned an invalid response", 502, {})


def _observation_id(value: object) -> str:
    if not isinstance(value, str) or _OBSERVATION_ID.fullmatch(value) is None:
        raise ValueError("invalid observation identity")
    return value


def _cursor(value: object) -> tuple[str, Literal["legacy", "native"], str]:
    if not isinstance(value, str):
        raise ValueError("invalid recovery cursor")
    kind, separator, identity = value.partition(":")
    if separator != ":":
        raise ValueError("invalid recovery cursor")
    if kind == "native":
        return value, "native", validate_operation_id(identity)
    if kind == "legacy":
        return value, "legacy", _observation_id(identity)
    raise ValueError("invalid recovery cursor")


def validate_recovery_request(after: object, limit: object) -> tuple[str, int]:
    """Validate the explicit cursor and bound before any Gateway transport."""
    if not isinstance(after, str):
        raise _invalid_request("after must be a recovery cursor")
    if after:
        try:
            _cursor(after)
        except ValueError as exc:
            raise _invalid_request("after must be a valid tagged recovery cursor") from exc
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise _invalid_request("limit must be an integer in 1..1000")
    return after, limit


def validate_legacy_observation_id(value: object) -> str:
    """Validate one private observation identity before URL construction."""
    try:
        return _observation_id(value)
    except ValueError as exc:
        raise _invalid_request("observation_id must be a canonical obs-UUID") from exc


def validate_recovery_page(value: object, *, after: str, limit: int) -> dict[str, Any]:
    """Validate an exact, ordered, bounded recovery page."""
    try:
        if not isinstance(value, dict) or frozenset(value) != _PAGE_KEYS:
            raise ValueError("invalid recovery page")
        items_value = value["items"]
        if not isinstance(items_value, list) or len(items_value) > limit:
            raise ValueError("invalid recovery items")
        items: list[dict[str, str]] = []
        prior = after
        for raw in items_value:
            if not isinstance(raw, dict) or frozenset(raw) != _ITEM_KEYS:
                raise ValueError("invalid recovery item")
            cursor, kind, identity = _cursor(raw["cursor"])
            if raw["kind"] != kind or raw["identity"] != identity or cursor != f"{kind}:{identity}":
                raise ValueError("inconsistent recovery item")
            if cursor <= prior:
                raise ValueError("recovery items are not strictly ordered")
            prior = cursor
            items.append({"cursor": cursor, "kind": kind, "identity": identity})
        next_after = value["next_after"]
        if next_after is not None:
            cursor, _kind, _identity = _cursor(next_after)
            if not items or cursor != items[-1]["cursor"] or len(items) != limit:
                raise ValueError("invalid next recovery cursor")
            next_after = cursor
        return {"items": items, "next_after": next_after}
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid_response() from exc


def _text(value: object) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError("expected nonempty text")
    value.encode("utf-8")
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError("invalid timestamp")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return value


def _hash(value: object) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError("invalid hash")
    return value


def _canonical_hash(value: dict[str, object]) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _refund_request(
    value: object,
    *,
    escrow_id: str,
    poster_id: str,
) -> tuple[dict[str, object], str]:
    required = {
        "request_version",
        "operation_kind",
        "escrow_id",
        "recipient_account_id",
        "source_reference",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("invalid refund request")
    request = cast("dict[str, object]", value)
    if (
        type(request["request_version"]) is not int
        or request["request_version"] != 1
        or type(request["operation_kind"]) is not str
        or request["operation_kind"] != "escrow_release"
        or request["escrow_id"] != escrow_id
        or request["recipient_account_id"] != poster_id
    ):
        raise ValueError("inconsistent refund request")
    _text(request["source_reference"])
    copied = dict(request)
    return copied, _canonical_hash(copied)


def _block_error(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"error", "message", "details"}:
        raise ValueError("invalid block error")
    details = value["details"]
    if (
        value["error"] != _BLOCK_ERROR
        or value["message"] != _BLOCK_MESSAGE
        or not isinstance(details, dict)
        or set(details) != {"reason"}
        or type(details["reason"]) is not str
        or details["reason"] not in _BLOCK_REASONS
    ):
        raise ValueError("invalid block error")
    return cast("dict[str, object]", value)


def _legacy_result(value: object) -> bool:
    return value == {
        "refund_origin": "existing_refund",
        "settlement_provenance_kind": "legacy_observation",
        "escrow_status_at_cutover": "released",
    } and isinstance(value, dict)


def _native_result(value: object) -> bool:
    return value == {"escrow_status_at_commit": "released"} and isinstance(value, dict)


def validate_legacy_posting_envelope(
    value: object,
    *,
    expected_observation_id: str,
) -> dict[str, Any]:
    """Validate one complete private legacy-refund observation envelope."""
    try:
        expected_id = _observation_id(expected_observation_id)
        if not isinstance(value, dict) or frozenset(value) != _ENVELOPE_KEYS:
            raise ValueError("invalid observation envelope")
        envelope = cast("dict[str, object]", value)
        observation_id = _observation_id(envelope["observation_id"])
        # Cutover preserves historical financial references, including values
        # that predate the native posting UUID contract.
        task_id = _text(envelope["task_id"])
        if observation_id != expected_id:
            raise ValueError("mismatched observation envelope")
        if type(envelope["protocol_version"]) is not int or envelope["protocol_version"] != 1:
            raise ValueError("invalid observation version")
        escrow_id = _text(envelope["escrow_id"])
        poster_id = _text(envelope["poster_id"])
        amount = envelope["amount"]
        if type(amount) is not int or not 1 <= amount <= SQLITE_MAX_INTEGER:
            raise ValueError("invalid refund amount")
        _hash(envelope["observed_fact_sha256"])
        state = envelope["observation_state"]
        if type(state) is not str or state not in {
            "refund_pending",
            "refunded",
            "blocked_integrity",
        }:
            raise ValueError("invalid observation state")

        operation_id = envelope["refund_operation_id"]
        request_value = envelope["refund_request"]
        request_hash = envelope["refund_request_sha256"]
        native_identity = (
            operation_id is not None or request_value is not None or request_hash is not None
        )
        request: dict[str, object] | None = None
        if native_identity:
            operation_id = validate_operation_id(operation_id)
            request, computed_hash = _refund_request(
                request_value,
                escrow_id=escrow_id,
                poster_id=poster_id,
            )
            if _hash(request_hash) != computed_hash:
                raise ValueError("refund request hash mismatch")
        elif (operation_id, request_value, request_hash) != (None, None, None):
            raise ValueError("partial refund identity")

        receipt_event_id = envelope["refund_receipt_event_id"]
        refunded_at = envelope["refunded_at"]
        result = envelope["result"]
        error = envelope["error"]
        if state == "refund_pending":
            if (
                not native_identity
                or operation_id != operation_id_for("task-posting-refund", (escrow_id,))
                or request is None
                or request["source_reference"] != f"task:{task_id}:posting_refund:{escrow_id}"
                or any(item is not None for item in (receipt_event_id, refunded_at, result, error))
            ):
                raise ValueError("invalid pending refund")
        elif state == "refunded":
            _timestamp(refunded_at)
            if error is not None:
                raise ValueError("refunded observation has an error")
            if native_identity:
                if (
                    type(receipt_event_id) is not int
                    or not 1 <= receipt_event_id <= SQLITE_MAX_INTEGER
                    or not _native_result(result)
                ):
                    raise ValueError("invalid native refund result")
            elif receipt_event_id is not None or not _legacy_result(result):
                raise ValueError("invalid legacy refund result")
        else:
            if any(item is not None for item in (receipt_event_id, refunded_at, result)):
                raise ValueError("blocked observation has completion fields")
            if native_identity and (
                operation_id != operation_id_for("task-posting-refund", (escrow_id,))
                or request is None
                or request["source_reference"] != f"task:{task_id}:posting_refund:{escrow_id}"
            ):
                raise ValueError("blocked refund identity changed")
            _block_error(error)
        return cast("dict[str, Any]", dict(envelope))
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise _invalid_response() from exc


def validate_legacy_blocked_wrapper(
    value: object,
    *,
    expected_observation_id: str,
) -> ServiceError:
    """Validate and convert the sole acknowledged legacy 409 wrapper."""
    try:
        if not isinstance(value, dict) or set(value) != {"error", "message", "details"}:
            raise ValueError("invalid blocked wrapper")
        details = value["details"]
        if (
            value["error"] != _BLOCK_ERROR
            or value["message"] != _BLOCK_MESSAGE
            or not isinstance(details, dict)
            or set(details) != {"observation"}
        ):
            raise ValueError("invalid blocked wrapper")
        envelope = validate_legacy_posting_envelope(
            details["observation"],
            expected_observation_id=expected_observation_id,
        )
        if envelope["observation_state"] != "blocked_integrity":
            raise ValueError("wrapper does not contain a blocked observation")
        return ServiceError(_BLOCK_ERROR, _BLOCK_MESSAGE, 409, {"observation": envelope})
    except ServiceError as exc:
        if exc.error == "invalid_gateway_response":
            raise
        raise _invalid_response() from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid_response() from exc


__all__ = [
    "validate_legacy_blocked_wrapper",
    "validate_legacy_observation_id",
    "validate_legacy_posting_envelope",
    "validate_recovery_page",
    "validate_recovery_request",
]
