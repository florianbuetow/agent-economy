"""Strict immutable request identity for native task publication."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final, Literal

from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.task_operation_requests import parse_operation_id

_MAX_DURATION_SECONDS: Final[int] = SQLITE_MAX_INTEGER // 1_000_000
_TASK_ID: Final[re.Pattern[str]] = re.compile(
    r"\At-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)
_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "operation_id",
        "task_id",
        "actor_kind",
        "actor_id",
        "lock_operation_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "acceptance_window_seconds",
        "execution_deadline_seconds",
        "review_deadline_seconds",
    }
)


def _invalid(message: str, details: dict[str, object]) -> ServiceError:
    return ServiceError("invalid_payload", message, 400, details)


def _text(value: object, field: str, maximum: int | None) -> str:
    if not isinstance(value, str) or value == "":
        raise _invalid(f"{field} must be a nonempty string", {"field": field})
    if maximum is not None and len(value) > maximum:
        raise _invalid(f"{field} exceeds its maximum length", {"field": field})
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(f"{field} must be valid UTF-8", {"field": field}) from exc
    return value


def _integer(value: object, field: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _invalid(
            f"{field} must be a positive integer in the supported range",
            {"field": field},
        )
    return value


def _operation_id(value: object, field: str) -> str:
    try:
        return parse_operation_id(value)
    except ServiceError as exc:
        raise _invalid(f"{field} must be a canonical operation ID", {"field": field}) from exc


@dataclass(frozen=True, slots=True)
class TaskPublicationRequest:
    """Validated publication terms plus their stable semantic identity."""

    protocol_version: int
    operation_id: str
    task_id: str
    actor_kind: Literal["agent"]
    actor_id: str
    lock_operation_id: str
    title: str
    spec: str
    reward: int
    bidding_deadline_seconds: int
    acceptance_window_seconds: int
    execution_deadline_seconds: int
    review_deadline_seconds: int
    canonical_json: str
    request_sha256: str


def parse_task_publication_request(raw: object) -> TaskPublicationRequest:
    """Parse an exact protocol-v1 request without coercion or normalization."""
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise _invalid("Task publication body must be an object", {})
    payload: dict[str, object] = dict(raw)
    if set(payload) != set(_FIELDS):
        raise _invalid(
            "Task publication fields do not match protocol version 1",
            {
                "required_fields": sorted(_FIELDS),
                "provided_fields": sorted(str(key) for key in payload),
            },
        )
    if type(payload["protocol_version"]) is not int or payload["protocol_version"] != 1:
        raise _invalid("protocol_version must be integer 1", {"field": "protocol_version"})
    operation_id = _operation_id(payload["operation_id"], "operation_id")
    task_id = payload["task_id"]
    if not isinstance(task_id, str) or _TASK_ID.fullmatch(task_id) is None:
        raise _invalid("task_id must be a t-UUID string", {"field": "task_id"})
    if type(payload["actor_kind"]) is not str or payload["actor_kind"] != "agent":
        raise _invalid("actor_kind must be agent", {"field": "actor_kind"})
    actor_id = _text(payload["actor_id"], "actor_id", None)
    lock_operation_id = _operation_id(payload["lock_operation_id"], "lock_operation_id")
    expected_lock_id = operation_id_for("task-lock", (task_id, actor_id))
    if lock_operation_id != expected_lock_id:
        raise _invalid(
            "lock_operation_id does not match the publication terms",
            {"field": "lock_operation_id"},
        )
    if operation_id == lock_operation_id:
        raise _invalid(
            "operation_id and lock_operation_id must differ",
            {"field": "operation_id"},
        )
    title = _text(payload["title"], "title", 200)
    spec = _text(payload["spec"], "spec", 10_000)
    reward = _integer(payload["reward"], "reward", SQLITE_MAX_INTEGER)
    durations = {
        field: _integer(payload[field], field, _MAX_DURATION_SECONDS)
        for field in (
            "bidding_deadline_seconds",
            "acceptance_window_seconds",
            "execution_deadline_seconds",
            "review_deadline_seconds",
        )
    }
    semantic: dict[str, object] = {
        "protocol_version": 1,
        "task_id": task_id,
        "actor_kind": "agent",
        "actor_id": actor_id,
        "lock_operation_id": lock_operation_id,
        "title": title,
        "spec": spec,
        "reward": reward,
        **durations,
    }
    semantic_json = json.dumps(
        semantic,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    canonical_json = json.dumps(
        {**semantic, "operation_id": operation_id},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return TaskPublicationRequest(
        protocol_version=1,
        operation_id=operation_id,
        task_id=task_id,
        actor_kind="agent",
        actor_id=actor_id,
        lock_operation_id=lock_operation_id,
        title=title,
        spec=spec,
        reward=reward,
        bidding_deadline_seconds=durations["bidding_deadline_seconds"],
        acceptance_window_seconds=durations["acceptance_window_seconds"],
        execution_deadline_seconds=durations["execution_deadline_seconds"],
        review_deadline_seconds=durations["review_deadline_seconds"],
        canonical_json=canonical_json,
        request_sha256=hashlib.sha256(semantic_json.encode("utf-8")).hexdigest(),
    )


__all__ = ["TaskPublicationRequest", "parse_task_publication_request"]
