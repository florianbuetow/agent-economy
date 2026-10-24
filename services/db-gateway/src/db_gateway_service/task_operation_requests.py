"""Strict identity for native Task Board operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from service_commons.exceptions import ServiceError

from db_gateway_service.financial_requests import validate_operation_id
from db_gateway_service.schema import SQLITE_MAX_INTEGER

type TaskArgument = str | int
type TaskCommand = Literal[
    "submit_bid",
    "accept_bid",
    "cancel_task",
    "expire_bidding",
    "expire_acceptance",
    "submit_deliverable",
    "expire_execution",
    "approve_task",
    "auto_approve",
    "dispute_task",
    "record_ruling",
]

TOP_LEVEL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "operation_id",
        "task_id",
        "command",
        "expected_status",
        "expected_lifecycle_version",
        "actor_kind",
        "actor_id",
        "arguments",
    }
)

COMMAND_ARGUMENT_FIELDS: Final[dict[TaskCommand, frozenset[str]]] = {
    "submit_bid": frozenset({"bid_id", "bidder_id", "amount"}),
    "accept_bid": frozenset({"bid_id", "worker_id", "escrow_id", "reward", "bid_amount"}),
    "cancel_task": frozenset({"poster_id", "escrow_id", "reward", "source_reference", "cutoff_us"}),
    "expire_bidding": frozenset(
        {"poster_id", "escrow_id", "reward", "source_reference", "cutoff_us"}
    ),
    "expire_acceptance": frozenset(
        {"poster_id", "escrow_id", "reward", "source_reference", "cutoff_us"}
    ),
    "submit_deliverable": frozenset({"worker_id"}),
    "expire_execution": frozenset(
        {
            "poster_id",
            "worker_id",
            "escrow_id",
            "reward",
            "source_reference",
            "cutoff_us",
        }
    ),
    "approve_task": frozenset(
        {"poster_id", "worker_id", "escrow_id", "reward", "source_reference"}
    ),
    "auto_approve": frozenset(
        {
            "poster_id",
            "worker_id",
            "escrow_id",
            "reward",
            "source_reference",
            "cutoff_us",
        }
    ),
    "dispute_task": frozenset({"poster_id", "worker_id", "escrow_id", "reward", "reason"}),
    "record_ruling": frozenset(
        {
            "poster_id",
            "worker_id",
            "escrow_id",
            "reward",
            "ruling_id",
            "worker_pct",
            "summary",
            "source_reference",
        }
    ),
}
_SUBMISSION_V2_ARGUMENT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "worker_id",
        "manifest_id",
        "accepted_binding_kind",
        "accepted_binding_sha256",
    }
)
_ACCEPTED_BINDING_KINDS: Final[frozenset[str]] = frozenset(
    {"publication_contract", "legacy_accepted_continuation"}
)

COMMAND_SOURCE_STATUS: Final[dict[TaskCommand, str]] = {
    "submit_bid": "open",
    "accept_bid": "open",
    "cancel_task": "open",
    "expire_bidding": "open",
    "expire_acceptance": "open",
    "submit_deliverable": "accepted",
    "expire_execution": "accepted",
    "approve_task": "submitted",
    "auto_approve": "submitted",
    "dispute_task": "submitted",
    "record_ruling": "disputed",
}

COMMAND_TARGET_STATUS: Final[dict[TaskCommand, str]] = {
    "submit_bid": "open",
    "accept_bid": "accepted",
    "cancel_task": "cancelled",
    "expire_bidding": "expired",
    "expire_acceptance": "expired",
    "submit_deliverable": "submitted",
    "expire_execution": "expired",
    "approve_task": "approved",
    "auto_approve": "approved",
    "dispute_task": "disputed",
    "record_ruling": "ruled",
}

COMMAND_ACTOR_KIND: Final[dict[TaskCommand, str]] = {
    "submit_bid": "agent",
    "accept_bid": "agent",
    "cancel_task": "agent",
    "expire_bidding": "system",
    "expire_acceptance": "system",
    "submit_deliverable": "agent",
    "expire_execution": "system",
    "approve_task": "agent",
    "auto_approve": "system",
    "dispute_task": "agent",
    "record_ruling": "platform",
}

_INTEGER_FIELDS: Final[dict[str, tuple[int, int]]] = {
    "expected_lifecycle_version": (0, SQLITE_MAX_INTEGER),
    "amount": (1, SQLITE_MAX_INTEGER),
    "reward": (1, SQLITE_MAX_INTEGER),
    "bid_amount": (0, SQLITE_MAX_INTEGER),
    "cutoff_us": (0, SQLITE_MAX_INTEGER),
    "worker_pct": (0, 100),
}


@dataclass(frozen=True, slots=True)
class TaskOperationRequest:
    """Validated immutable command and its canonical business identity."""

    protocol_version: int
    operation_id: str
    task_id: str
    command: TaskCommand
    expected_status: str
    expected_lifecycle_version: int
    actor_kind: str
    actor_id: str
    arguments: MappingProxyType[str, TaskArgument]
    canonical_json: str
    request_sha256: str

    def argument(self, name: str) -> TaskArgument:
        """Return one already-validated command argument."""
        return self.arguments[name]


def _invalid(message: str, details: dict[str, object]) -> ServiceError:
    return ServiceError("invalid_payload", message, 400, details)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"{field} must be a nonempty string", {"field": field})
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(f"{field} must contain valid UTF-8 text", {"field": field}) from exc
    return value


def _integer(value: object, field: str) -> int:
    minimum, maximum = _INTEGER_FIELDS[field]
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid(
            f"{field} must be an integer in {minimum}..{maximum}",
            {"field": field},
        )
    return value


def parse_operation_id(value: object) -> str:
    """Map the shared canonical operation-ID validation to the Board error contract."""
    try:
        return validate_operation_id(value)
    except ValueError as exc:
        raise ServiceError(
            "invalid_operation_id",
            "operation_id must be a canonical op-UUID string",
            400,
            {"field": "operation_id"},
        ) from exc


def _command(value: object) -> TaskCommand:
    if isinstance(value, str):
        for command in COMMAND_ARGUMENT_FIELDS:
            if command == value:
                return command
    raise _invalid("Unknown task operation command", {"field": "command"})


def _arguments(
    command: TaskCommand,
    protocol_version: int,
    value: object,
) -> MappingProxyType[str, TaskArgument]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _invalid("arguments must be an object", {"field": "arguments"})
    raw_arguments: dict[str, object] = dict(value)
    expected = (
        _SUBMISSION_V2_ARGUMENT_FIELDS
        if command == "submit_deliverable" and protocol_version == 2
        else COMMAND_ARGUMENT_FIELDS[command]
    )
    if tuple(sorted(raw_arguments)) != tuple(sorted(expected)):
        raise _invalid(
            "arguments fields do not match the command",
            {
                "field": "arguments",
                "required_fields": sorted(expected),
                "provided_fields": sorted(raw_arguments),
            },
        )
    validated: dict[str, TaskArgument] = {}
    for field in sorted(expected):
        raw = raw_arguments[field]
        if field in _INTEGER_FIELDS:
            validated[field] = _integer(raw, field)
        else:
            validated[field] = _string(raw, field)
    binding_kind = validated.get("accepted_binding_kind")
    if binding_kind is not None and binding_kind not in _ACCEPTED_BINDING_KINDS:
        raise _invalid(
            "accepted_binding_kind is not supported",
            {"field": "accepted_binding_kind"},
        )
    binding_sha256 = validated.get("accepted_binding_sha256")
    if binding_sha256 is not None and (
        not isinstance(binding_sha256, str)
        or len(binding_sha256) != 64
        or any(character not in "0123456789abcdef" for character in binding_sha256)
    ):
        raise _invalid(
            "accepted_binding_sha256 must be lowercase hexadecimal SHA-256",
            {"field": "accepted_binding_sha256"},
        )
    return MappingProxyType(validated)


def parse_task_operation_request(payload: object) -> TaskOperationRequest:
    """Reject coercions/unknown fields and hash every field except operation_id."""
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise _invalid("Task operation body must be an object", {})
    raw_payload: dict[str, object] = dict(payload)
    if tuple(sorted(raw_payload)) != tuple(sorted(TOP_LEVEL_FIELDS)):
        raise _invalid(
            "Task operation fields do not match the protocol contract",
            {
                "required_fields": sorted(TOP_LEVEL_FIELDS),
                "provided_fields": sorted(raw_payload),
            },
        )
    protocol_version = raw_payload["protocol_version"]
    if type(protocol_version) is not int or protocol_version not in {1, 2}:
        raise _invalid("protocol_version must be integer 1 or 2", {"field": "protocol_version"})

    operation_id = parse_operation_id(raw_payload["operation_id"])
    command = _command(raw_payload["command"])
    if protocol_version == 2 and command != "submit_deliverable":
        raise _invalid(
            "protocol_version 2 is supported only for submit_deliverable",
            {"field": "protocol_version", "command": command},
        )
    task_id = _string(raw_payload["task_id"], "task_id")
    expected_status = _string(raw_payload["expected_status"], "expected_status")
    if expected_status != COMMAND_SOURCE_STATUS[command]:
        raise _invalid(
            "expected_status does not match the command",
            {"field": "expected_status", "command": command},
        )
    lifecycle_version = _integer(
        raw_payload["expected_lifecycle_version"], "expected_lifecycle_version"
    )
    actor_kind = _string(raw_payload["actor_kind"], "actor_kind")
    if actor_kind != COMMAND_ACTOR_KIND[command]:
        raise _invalid(
            "actor_kind does not match the command",
            {"field": "actor_kind", "command": command},
        )
    actor_id = _string(raw_payload["actor_id"], "actor_id")
    arguments = _arguments(command, protocol_version, raw_payload["arguments"])

    canonical_payload: dict[str, object] = {
        "actor_id": actor_id,
        "actor_kind": actor_kind,
        "arguments": dict(arguments),
        "command": command,
        "expected_lifecycle_version": lifecycle_version,
        "expected_status": expected_status,
        "protocol_version": protocol_version,
        "task_id": task_id,
    }
    canonical_json = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return TaskOperationRequest(
        protocol_version=protocol_version,
        operation_id=operation_id,
        task_id=task_id,
        command=command,
        expected_status=expected_status,
        expected_lifecycle_version=lifecycle_version,
        actor_kind=actor_kind,
        actor_id=actor_id,
        arguments=arguments,
        canonical_json=canonical_json,
        request_sha256=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "COMMAND_ACTOR_KIND",
    "COMMAND_ARGUMENT_FIELDS",
    "COMMAND_SOURCE_STATUS",
    "COMMAND_TARGET_STATUS",
    "TOP_LEVEL_FIELDS",
    "TaskOperationRequest",
    "parse_operation_id",
    "parse_task_operation_request",
]
