"""Immutable task-publication intents and strict public response parsing."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import SQLITE_MAX_INTEGER, validate_operation_id

if TYPE_CHECKING:
    import httpx

_MAX_DURATION_SECONDS = SQLITE_MAX_INTEGER // 1_000_000
_TASK_ID_PATTERN = re.compile(
    r"^t-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_PUBLICATION_STATES = frozenset({"awaiting_lock", "published", "blocked_integrity"})
_BLOCK_REASONS = frozenset(
    {
        "receipt_invalid",
        "escrow_invalid",
        "settlement_conflict",
        "publication_conflict",
        "clock_out_of_range",
    }
)
_SEMANTIC_KEYS = frozenset(
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
_ENVELOPE_KEYS = frozenset(
    {
        "protocol_version",
        "operation_id",
        "task_id",
        "request_sha256",
        "publication_state",
        "lock_operation_id",
        "escrow_id",
        "admitted_at",
        "published_at",
        "result",
        "error",
    }
)
_RESULT_KEYS = frozenset(
    {
        "task_id",
        "poster_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "acceptance_window_seconds",
        "execution_deadline_seconds",
        "review_deadline_seconds",
        "status",
        "escrow_id",
        "created_at",
        "bidding_deadline",
        "acceptance_deadline",
        "bidding_cutoff_us",
        "acceptance_cutoff_us",
        "lifecycle_version",
        "timing_contract_version",
        "event_id",
    }
)
_BLOCK_ERROR = "publication_integrity_blocked"
_BLOCK_MESSAGE = "Publication evidence requires reconciliation"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _exact_keys(value: dict[str, object], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} must contain exactly the required fields")


def _text(value: object, field: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if value == "":
        raise ValueError(f"{field} must be nonempty")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{field} exceeds its maximum length")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8") from exc
    return value


def _integer(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer in [{minimum}, {maximum}]")
    return value


def _task_id(value: object) -> str:
    if not isinstance(value, str) or _TASK_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("task_id must be a canonical t-UUID string")
    return value


def _request_hash(value: dict[str, object]) -> str:
    semantic = {key: item for key, item in value.items() if key != "operation_id"}
    try:
        encoded = json.dumps(
            semantic,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("task posting intent must be finite canonical UTF-8 JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _validated_intent(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("task posting intent must be an object")
    _exact_keys(value, _SEMANTIC_KEYS, "task posting intent")
    protocol_version = _integer(value["protocol_version"], "protocol_version", minimum=1, maximum=1)
    operation_id = validate_operation_id(value["operation_id"])
    task_id = _task_id(value["task_id"])
    if value["actor_kind"] != "agent" or type(value["actor_kind"]) is not str:
        raise ValueError("actor_kind must be 'agent'")
    actor_id = _text(value["actor_id"], "actor_id")
    lock_operation_id = validate_operation_id(value["lock_operation_id"])
    expected_lock_id = operation_id_for("task-lock", (task_id, actor_id))
    if lock_operation_id != expected_lock_id:
        raise ValueError("lock_operation_id does not match the task posting intent")
    if operation_id == lock_operation_id:
        raise ValueError("operation_id and lock_operation_id must differ")
    title = _text(value["title"], "title", maximum=200)
    spec = _text(value["spec"], "spec", maximum=10_000)
    reward = _integer(value["reward"], "reward", minimum=1, maximum=SQLITE_MAX_INTEGER)
    durations = {
        field: _integer(value[field], field, minimum=1, maximum=_MAX_DURATION_SECONDS)
        for field in (
            "bidding_deadline_seconds",
            "acceptance_window_seconds",
            "execution_deadline_seconds",
            "review_deadline_seconds",
        )
    }
    return {
        "protocol_version": protocol_version,
        "operation_id": operation_id,
        "task_id": task_id,
        "actor_kind": "agent",
        "actor_id": actor_id,
        "lock_operation_id": lock_operation_id,
        "title": title,
        "spec": spec,
        "reward": reward,
        **durations,
    }


@dataclass(frozen=True, slots=True)
class TaskPostingIntent:
    """One immutable, caller-owned logical task publication."""

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

    def __post_init__(self) -> None:
        _validated_intent(self.as_json())

    @classmethod
    def from_json(cls, value: object) -> TaskPostingIntent:
        """Parse an exact semantic request without coercion or normalization."""
        parsed = _validated_intent(value)
        return cls(
            protocol_version=cast("int", parsed["protocol_version"]),
            operation_id=cast("str", parsed["operation_id"]),
            task_id=cast("str", parsed["task_id"]),
            actor_kind="agent",
            actor_id=cast("str", parsed["actor_id"]),
            lock_operation_id=cast("str", parsed["lock_operation_id"]),
            title=cast("str", parsed["title"]),
            spec=cast("str", parsed["spec"]),
            reward=cast("int", parsed["reward"]),
            bidding_deadline_seconds=cast("int", parsed["bidding_deadline_seconds"]),
            acceptance_window_seconds=cast("int", parsed["acceptance_window_seconds"]),
            execution_deadline_seconds=cast("int", parsed["execution_deadline_seconds"]),
            review_deadline_seconds=cast("int", parsed["review_deadline_seconds"]),
        )

    def as_json(self) -> dict[str, object]:
        """Return a new exact JSON-compatible semantic request object."""
        return {
            "protocol_version": self.protocol_version,
            "operation_id": self.operation_id,
            "task_id": self.task_id,
            "actor_kind": self.actor_kind,
            "actor_id": self.actor_id,
            "lock_operation_id": self.lock_operation_id,
            "title": self.title,
            "spec": self.spec,
            "reward": self.reward,
            "bidding_deadline_seconds": self.bidding_deadline_seconds,
            "acceptance_window_seconds": self.acceptance_window_seconds,
            "execution_deadline_seconds": self.execution_deadline_seconds,
            "review_deadline_seconds": self.review_deadline_seconds,
        }

    @property
    def request_sha256(self) -> str:
        """Hash all semantic request fields except the replay operation identity."""
        return _request_hash(self.as_json())


@dataclass(frozen=True, slots=True)
class TaskPostingResponse:
    """Actual HTTP status and a fully validated publication response body."""

    status_code: int
    body: dict[str, Any]


def _timestamp(value: object, field: str) -> tuple[str, datetime, int]:
    if not isinstance(value, str) or _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise RuntimeError(f"Task publication {field} is not an exact UTC microsecond timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise RuntimeError(f"Task publication {field} is invalid") from exc
    delta = parsed - _EPOCH
    microseconds = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    if not 0 <= microseconds <= SQLITE_MAX_INTEGER:
        raise RuntimeError(f"Task publication {field} is outside the supported clock range")
    return value, parsed, microseconds


def _response_object(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError("Task publication response is not valid JSON") from exc
    if not isinstance(body, dict):
        raise RuntimeError("Task publication response must be an object")
    return cast("dict[str, object]", body)


def _blocked_error(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("Task publication blocked error must be an object")
    if set(value) != {"error", "message", "details"}:
        raise RuntimeError("Task publication blocked error has invalid fields")
    details = value["details"]
    if (
        value["error"] != _BLOCK_ERROR
        or value["message"] != _BLOCK_MESSAGE
        or not isinstance(details, dict)
        or set(details) != {"reason"}
        or type(details["reason"]) is not str
        or details["reason"] not in _BLOCK_REASONS
    ):
        raise RuntimeError("Task publication blocked error is invalid")
    return cast("dict[str, object]", value)


def _result_integer(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise RuntimeError(f"Task publication result {field} is invalid")
    return value


def _publication_result(
    value: object,
    *,
    envelope: dict[str, object],
    published_at: datetime,
    published_at_us: int,
    intent: TaskPostingIntent | None,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _RESULT_KEYS:
        raise RuntimeError("Task publication result must contain exactly the required fields")
    result = cast("dict[str, object]", value)
    try:
        task_id = _task_id(result["task_id"])
        poster_id = _text(result["poster_id"], "poster_id")
        title = _text(result["title"], "title", maximum=200)
        spec = _text(result["spec"], "spec", maximum=10_000)
        escrow_id = _text(result["escrow_id"], "escrow_id")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Task publication result contains invalid text or identity") from exc
    reward = _result_integer(result["reward"], "reward", minimum=1, maximum=SQLITE_MAX_INTEGER)
    durations = {
        field: _result_integer(result[field], field, minimum=1, maximum=_MAX_DURATION_SECONDS)
        for field in (
            "bidding_deadline_seconds",
            "acceptance_window_seconds",
            "execution_deadline_seconds",
            "review_deadline_seconds",
        )
    }
    created_at, created, created_us = _timestamp(result["created_at"], "created_at")
    _bidding_text, bidding, bidding_us = _timestamp(result["bidding_deadline"], "bidding_deadline")
    _acceptance_text, acceptance, acceptance_us = _timestamp(
        result["acceptance_deadline"], "acceptance_deadline"
    )
    bidding_cutoff_us = _result_integer(
        result["bidding_cutoff_us"],
        "bidding_cutoff_us",
        minimum=1,
        maximum=SQLITE_MAX_INTEGER,
    )
    acceptance_cutoff_us = _result_integer(
        result["acceptance_cutoff_us"],
        "acceptance_cutoff_us",
        minimum=1,
        maximum=SQLITE_MAX_INTEGER,
    )
    lifecycle_version = _result_integer(
        result["lifecycle_version"], "lifecycle_version", minimum=0, maximum=0
    )
    timing_version = _result_integer(
        result["timing_contract_version"],
        "timing_contract_version",
        minimum=2,
        maximum=2,
    )
    event_id = _result_integer(
        result["event_id"], "event_id", minimum=1, maximum=SQLITE_MAX_INTEGER
    )
    try:
        expected_bidding = published_at + timedelta(seconds=durations["bidding_deadline_seconds"])
        expected_acceptance = expected_bidding + timedelta(
            seconds=durations["acceptance_window_seconds"]
        )
    except OverflowError as exc:
        raise RuntimeError("Task publication deadline arithmetic is out of range") from exc
    valid = (
        task_id == envelope["task_id"]
        and result["status"] == "open"
        and type(result["status"]) is str
        and escrow_id == envelope["escrow_id"]
        and created_at == envelope["published_at"]
        and created == published_at
        and created_us == published_at_us
        and bidding == expected_bidding
        and acceptance == expected_acceptance
        and bidding_cutoff_us == bidding_us
        and acceptance_cutoff_us == acceptance_us
        and lifecycle_version == 0
        and timing_version == 2
        and event_id >= 1
        and envelope["lock_operation_id"] == operation_id_for("task-lock", (task_id, poster_id))
    )
    if intent is not None:
        valid = valid and (
            poster_id == intent.actor_id
            and title == intent.title
            and spec == intent.spec
            and reward == intent.reward
            and durations["bidding_deadline_seconds"] == intent.bidding_deadline_seconds
            and durations["acceptance_window_seconds"] == intent.acceptance_window_seconds
            and durations["execution_deadline_seconds"] == intent.execution_deadline_seconds
            and durations["review_deadline_seconds"] == intent.review_deadline_seconds
        )
    semantic = {
        "protocol_version": 1,
        "operation_id": envelope["operation_id"],
        "task_id": task_id,
        "actor_kind": "agent",
        "actor_id": poster_id,
        "lock_operation_id": envelope["lock_operation_id"],
        "title": title,
        "spec": spec,
        "reward": reward,
        **durations,
    }
    valid = valid and _request_hash(semantic) == envelope["request_sha256"]
    if not valid:
        raise RuntimeError("Task publication result contradicts the acknowledged intent")
    return result


def _publication_envelope(
    value: object,
    *,
    expected_operation_id: str,
    intent: TaskPostingIntent | None,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ENVELOPE_KEYS:
        raise RuntimeError("Task publication envelope must contain exactly the required fields")
    envelope = cast("dict[str, object]", value)
    if type(envelope["protocol_version"]) is not int or envelope["protocol_version"] != 1:
        raise RuntimeError("Task publication protocol version is invalid")
    try:
        operation_id = validate_operation_id(envelope["operation_id"])
        task_id = _task_id(envelope["task_id"])
        lock_operation_id = validate_operation_id(envelope["lock_operation_id"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Task publication identity is invalid") from exc
    request_sha256 = envelope["request_sha256"]
    state = envelope["publication_state"]
    if (
        operation_id != expected_operation_id
        or operation_id == lock_operation_id
        or not isinstance(request_sha256, str)
        or _HASH_PATTERN.fullmatch(request_sha256) is None
        or type(state) is not str
        or state not in _PUBLICATION_STATES
    ):
        raise RuntimeError("Task publication identity or state is invalid")
    if intent is not None and (
        task_id != intent.task_id
        or request_sha256 != intent.request_sha256
        or lock_operation_id != intent.lock_operation_id
    ):
        raise RuntimeError("Task publication acknowledgment does not match the submitted intent")
    admitted_at, admitted, _admitted_us = _timestamp(envelope["admitted_at"], "admitted_at")
    del admitted_at, _admitted_us
    escrow_id = envelope["escrow_id"]
    published_value = envelope["published_at"]
    result = envelope["result"]
    error = envelope["error"]
    if state == "published":
        if (
            not isinstance(escrow_id, str)
            or escrow_id == ""
            or published_value is None
            or error is not None
        ):
            raise RuntimeError("Published task response has contradictory nullability")
        _published_text, published, published_us = _timestamp(published_value, "published_at")
        if admitted > published:
            raise RuntimeError("Task publication precedes its admission")
        _publication_result(
            result,
            envelope=envelope,
            published_at=published,
            published_at_us=published_us,
            intent=intent,
        )
    elif state == "awaiting_lock":
        if any(item is not None for item in (escrow_id, published_value, result, error)):
            raise RuntimeError("Pending task response has contradictory nullability")
    else:
        if any(item is not None for item in (escrow_id, published_value, result)):
            raise RuntimeError("Blocked task response has contradictory nullability")
        _blocked_error(error)
    return envelope


def parse_task_posting_submit_response(
    response: httpx.Response, intent: TaskPostingIntent
) -> TaskPostingResponse:
    """Validate an exact POST acknowledgment while preserving its real status."""
    status_code = response.status_code
    if status_code == 409:
        try:
            candidate = response.json()
        except ValueError:
            response.raise_for_status()
            raise AssertionError("unreachable") from None
        if not isinstance(candidate, dict) or candidate.get("error") != _BLOCK_ERROR:
            response.raise_for_status()
            raise AssertionError("unreachable")
        body = cast("dict[str, object]", candidate)
        if set(body) != {"error", "message", "details"} or body["message"] != _BLOCK_MESSAGE:
            raise RuntimeError("Blocked task publication wrapper is invalid")
        details = body["details"]
        if not isinstance(details, dict) or set(details) != {"publication"}:
            raise RuntimeError("Blocked task publication wrapper is invalid")
        envelope = _publication_envelope(
            details["publication"],
            expected_operation_id=intent.operation_id,
            intent=intent,
        )
        if envelope["publication_state"] != "blocked_integrity":
            raise RuntimeError("Blocked wrapper does not contain a blocked publication")
        return TaskPostingResponse(status_code=status_code, body=copy.deepcopy(body))
    response.raise_for_status()
    body = _response_object(response)
    expected_state = {201: "published", 202: "awaiting_lock"}.get(status_code)
    if expected_state is None:
        raise RuntimeError("Unexpected task publication success status")
    envelope = _publication_envelope(body, expected_operation_id=intent.operation_id, intent=intent)
    if envelope["publication_state"] != expected_state:
        raise RuntimeError("Task publication state contradicts its HTTP status")
    return TaskPostingResponse(status_code=status_code, body=copy.deepcopy(body))


def parse_task_posting_status_response(
    response: httpx.Response, expected_operation_id: str
) -> TaskPostingResponse:
    """Validate an exact owner status response while preserving its real status."""
    response.raise_for_status()
    if response.status_code != 200:
        raise RuntimeError("Unexpected task publication status success code")
    body = _response_object(response)
    _publication_envelope(body, expected_operation_id=expected_operation_id, intent=None)
    return TaskPostingResponse(status_code=response.status_code, body=copy.deepcopy(body))
