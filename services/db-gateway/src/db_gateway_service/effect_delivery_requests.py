"""Strict request parsing for MP-08 financial-effect recovery routes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from service_commons.exceptions import ServiceError

from db_gateway_service.task_operation_requests import parse_operation_id

if TYPE_CHECKING:
    from collections.abc import Mapping

_OBSERVATION_ID: Final[re.Pattern[str]] = re.compile(
    r"obs-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_WORKER_ID: Final[re.Pattern[str]] = re.compile(
    r"recovery-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
WORKER_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    {
        "central_bank_unavailable",
        "gateway_unavailable",
        "invalid_gateway_response",
        "request_timeout",
        "unexpected_recovery_error",
    }
)
DELIVERY_STATES: Final[frozenset[str]] = frozenset(
    {"pending", "leased", "dead_letter", "completed", "integrity_blocked"}
)


@dataclass(frozen=True, slots=True)
class LeaseRequest:
    """One validated recovery lease request."""

    worker_id: str


@dataclass(frozen=True, slots=True)
class FailureRequest:
    """One validated fenced attempt-failure request."""

    worker_id: str
    error_code: str


def invalid_payload(message: str) -> ServiceError:
    """Build the stable bad-request response used by strict recovery parsers."""
    return ServiceError("invalid_payload", message, 400, {})


def parse_empty_body(value: Mapping[str, object]) -> None:
    """Require the exact empty-object request contract."""
    if value:
        raise invalid_payload("Request body must be an empty object")


def parse_lease_request(value: Mapping[str, object]) -> LeaseRequest:
    """Parse one exact protocol-v1 lease request."""
    if set(value) != {"protocol_version", "worker_id"}:
        raise invalid_payload("Lease request has missing or extra fields")
    if type(value["protocol_version"]) is not int or value["protocol_version"] != 1:
        raise invalid_payload("protocol_version must be integer 1")
    return LeaseRequest(worker_id=parse_worker_id(value["worker_id"]))


def parse_failure_request(value: Mapping[str, object]) -> FailureRequest:
    """Parse one exact protocol-v1 attempt failure request."""
    if set(value) != {"protocol_version", "worker_id", "error_code"}:
        raise invalid_payload("Failure request has missing or extra fields")
    if type(value["protocol_version"]) is not int or value["protocol_version"] != 1:
        raise invalid_payload("protocol_version must be integer 1")
    error_code = value["error_code"]
    if not isinstance(error_code, str) or error_code not in WORKER_FAILURE_CODES:
        raise invalid_payload("error_code is not worker-reportable")
    return FailureRequest(
        worker_id=parse_worker_id(value["worker_id"]),
        error_code=error_code,
    )


def parse_worker_id(value: object) -> str:
    """Require the process-fence ID's exact canonical UUID4 form."""
    if not isinstance(value, str) or _WORKER_ID.fullmatch(value) is None:
        raise invalid_payload("worker_id must be recovery-<canonical-lowercase-UUID4>")
    return value


def parse_effect_operation_id(value: object) -> str:
    """Parse a canonical financial effect operation identity."""
    try:
        return parse_operation_id(value)
    except ServiceError as exc:
        raise invalid_payload("effect_operation_id must be a canonical operation UUID4") from exc


def parse_observation_id(value: object) -> str:
    """Parse a canonical migrated observation identity."""
    if not isinstance(value, str) or _OBSERVATION_ID.fullmatch(value) is None:
        raise invalid_payload("observation_id must be a canonical observation UUID4")
    return value


def parse_generation(value: object) -> int:
    """Parse a positive SQLite-representable lease generation."""
    if not isinstance(value, str) or re.fullmatch(r"(?:0|[1-9][0-9]{0,18})", value) is None:
        raise invalid_payload("lease_generation must be a nonnegative integer")
    generation = int(value)
    if generation > 9_223_372_036_854_775_807:
        raise invalid_payload("lease_generation exceeds the supported range")
    return generation


__all__ = [
    "DELIVERY_STATES",
    "WORKER_FAILURE_CODES",
    "FailureRequest",
    "LeaseRequest",
    "parse_effect_operation_id",
    "parse_empty_body",
    "parse_failure_request",
    "parse_generation",
    "parse_lease_request",
    "parse_observation_id",
]
