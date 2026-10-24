"""Strict canonical requests for protocol-V2 prepublication commands."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast

from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.task_operation_requests import parse_operation_id

_TASK_ID: Final[re.Pattern[str]] = re.compile(
    r"\At-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
_HASH: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]{64}\Z")
_MAX_DURATION_SECONDS: Final[int] = SQLITE_MAX_INTEGER // 1_000_000
_ADMISSION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "publication_operation_id",
        "task_id",
        "actor_kind",
        "actor_id",
        "action",
        "resource",
        "lock_operation_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "acceptance_window_seconds",
        "execution_deadline_seconds",
        "review_deadline_seconds",
        "expected_input_count",
    }
)
_FREEZE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "freeze_operation_id",
        "publication_operation_id",
        "task_id",
        "actor_id",
        "action",
        "resource",
        "input_manifest_id",
    }
)
_PUBLISH_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "publish_operation_id",
        "publication_operation_id",
        "task_id",
        "actor_id",
        "action",
        "resource",
        "lock_operation_id",
        "publication_contract_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class PublicationAdmissionV2:
    protocol_version: Literal[2]
    publication_operation_id: str
    task_id: str
    actor_kind: Literal["agent"]
    actor_id: str
    action: Literal["task_publication_admit"]
    resource: Literal["POST /task-publications"]
    lock_operation_id: str
    title: str
    spec: str
    reward: int
    bidding_deadline_seconds: int
    acceptance_window_seconds: int
    execution_deadline_seconds: int
    review_deadline_seconds: int
    expected_input_count: int
    canonical_request_json: str
    request_sha256: str
    base_request_sha256: str


@dataclass(frozen=True, slots=True)
class InputManifestFreezeV2:
    protocol_version: Literal[2]
    freeze_operation_id: str
    publication_operation_id: str
    task_id: str
    actor_id: str
    action: Literal["task_publication_inputs_freeze"]
    resource: str
    input_manifest_id: str
    canonical_freeze_request_json: str
    freeze_request_sha256: str


@dataclass(frozen=True, slots=True)
class PublicationPublishV2:
    protocol_version: Literal[2]
    publish_operation_id: str
    publication_operation_id: str
    task_id: str
    actor_id: str
    action: Literal["task_publication_publish"]
    resource: Literal["POST /tasks"]
    lock_operation_id: str
    publication_contract_sha256: str
    canonical_publish_request_json: str
    publish_request_sha256: str


def _invalid(message: str, field: str | None) -> ServiceError:
    return ServiceError(
        "invalid_payload",
        message,
        400,
        {} if field is None else {"field": field},
    )


def _mapping(value: object, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise _invalid("Request body must be an object", None)
    if frozenset(value) != fields:
        raise _invalid("Request fields do not match the protocol contract", None)
    return cast("Mapping[str, object]", value)


def _text(value: object, field: str, *, maximum: int | None) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"{field} must be a nonempty string", field)
    if maximum is not None and len(value) > maximum:
        raise _invalid(f"{field} exceeds its maximum length", field)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(f"{field} must contain valid UTF-8", field) from exc
    return value


def _integer(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid(f"{field} is outside the supported integer range", field)
    return value


def _operation(value: object, field: str) -> str:
    try:
        return parse_operation_id(value)
    except ServiceError as exc:
        raise _invalid(f"{field} must be a canonical operation ID", field) from exc


def _task(value: object) -> str:
    task_id = _text(value, "task_id", maximum=None)
    if _TASK_ID.fullmatch(task_id) is None:
        raise _invalid("task_id must be a canonical lowercase t-UUID", "task_id")
    return task_id


def _hash(value: object, field: str) -> str:
    text = _text(value, field, maximum=None)
    if _HASH.fullmatch(text) is None:
        raise _invalid(f"{field} must be lowercase hexadecimal SHA-256", field)
    return text


def _protocol(value: object) -> Literal[2]:
    if type(value) is not int or value != 2:
        raise _invalid("protocol_version must be integer 2", "protocol_version")
    return 2


def _canonical(values: Mapping[str, object]) -> tuple[str, str]:
    rendered = json.dumps(
        dict(values),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return rendered, hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def parse_publication_admission_v2(value: object) -> PublicationAdmissionV2:
    raw = _mapping(value, _ADMISSION_FIELDS)
    protocol = _protocol(raw["protocol_version"])
    publication_id = _operation(raw["publication_operation_id"], "publication_operation_id")
    task_id = _task(raw["task_id"])
    actor_kind = _text(raw["actor_kind"], "actor_kind", maximum=None)
    if actor_kind != "agent":
        raise _invalid("actor_kind must be agent", "actor_kind")
    actor_id = _text(raw["actor_id"], "actor_id", maximum=None)
    action = _text(raw["action"], "action", maximum=None)
    resource = _text(raw["resource"], "resource", maximum=None)
    if action != "task_publication_admit" or resource != "POST /task-publications":
        raise _invalid("Admission action or resource is invalid", None)
    lock_id = _operation(raw["lock_operation_id"], "lock_operation_id")
    if lock_id != operation_id_for("task-lock", (task_id, actor_id)) or lock_id == publication_id:
        raise _invalid("lock_operation_id does not match the admission", "lock_operation_id")
    title = _text(raw["title"], "title", maximum=200)
    spec = _text(raw["spec"], "spec", maximum=10_000)
    reward = _integer(raw["reward"], "reward", minimum=1, maximum=SQLITE_MAX_INTEGER)
    durations = {
        field: _integer(raw[field], field, minimum=1, maximum=_MAX_DURATION_SECONDS)
        for field in (
            "bidding_deadline_seconds",
            "acceptance_window_seconds",
            "execution_deadline_seconds",
            "review_deadline_seconds",
        )
    }
    expected_count = _integer(
        raw["expected_input_count"],
        "expected_input_count",
        minimum=0,
        maximum=SQLITE_MAX_INTEGER,
    )
    canonical, digest = _canonical(raw)
    return PublicationAdmissionV2(
        protocol,
        publication_id,
        task_id,
        "agent",
        actor_id,
        "task_publication_admit",
        "POST /task-publications",
        lock_id,
        title,
        spec,
        reward,
        durations["bidding_deadline_seconds"],
        durations["acceptance_window_seconds"],
        durations["execution_deadline_seconds"],
        durations["review_deadline_seconds"],
        expected_count,
        canonical,
        digest,
        digest,
    )


def parse_input_manifest_freeze_v2(value: object) -> InputManifestFreezeV2:
    raw = _mapping(value, _FREEZE_FIELDS)
    protocol = _protocol(raw["protocol_version"])
    freeze_id = _operation(raw["freeze_operation_id"], "freeze_operation_id")
    publication_id = _operation(raw["publication_operation_id"], "publication_operation_id")
    task_id = _task(raw["task_id"])
    actor_id = _text(raw["actor_id"], "actor_id", maximum=None)
    action = _text(raw["action"], "action", maximum=None)
    resource = _text(raw["resource"], "resource", maximum=None)
    expected_resource = f"POST /task-publications/{publication_id}/inputs/freeze"
    lock_id = operation_id_for("task-lock", (task_id, actor_id))
    if action != "task_publication_inputs_freeze" or resource != expected_resource:
        raise _invalid("Freeze action or resource is invalid", None)
    if freeze_id in {publication_id, lock_id}:
        raise _invalid("freeze_operation_id conflicts with another command", "freeze_operation_id")
    manifest_id = _text(raw["input_manifest_id"], "input_manifest_id", maximum=None)
    canonical, digest = _canonical(raw)
    return InputManifestFreezeV2(
        protocol,
        freeze_id,
        publication_id,
        task_id,
        actor_id,
        "task_publication_inputs_freeze",
        resource,
        manifest_id,
        canonical,
        digest,
    )


def parse_publication_publish_v2(value: object) -> PublicationPublishV2:
    raw = _mapping(value, _PUBLISH_FIELDS)
    protocol = _protocol(raw["protocol_version"])
    publish_id = _operation(raw["publish_operation_id"], "publish_operation_id")
    publication_id = _operation(raw["publication_operation_id"], "publication_operation_id")
    task_id = _task(raw["task_id"])
    actor_id = _text(raw["actor_id"], "actor_id", maximum=None)
    action = _text(raw["action"], "action", maximum=None)
    resource = _text(raw["resource"], "resource", maximum=None)
    lock_id = _operation(raw["lock_operation_id"], "lock_operation_id")
    if action != "task_publication_publish" or resource != "POST /tasks":
        raise _invalid("Publish action or resource is invalid", None)
    expected_lock = operation_id_for("task-lock", (task_id, actor_id))
    if lock_id != expected_lock or publish_id in {publication_id, lock_id}:
        raise _invalid("Publish operation identities are invalid", None)
    contract_sha256 = _hash(raw["publication_contract_sha256"], "publication_contract_sha256")
    canonical, digest = _canonical(raw)
    return PublicationPublishV2(
        protocol,
        publish_id,
        publication_id,
        task_id,
        actor_id,
        "task_publication_publish",
        "POST /tasks",
        lock_id,
        contract_sha256,
        canonical,
        digest,
    )


__all__ = [
    "InputManifestFreezeV2",
    "PublicationAdmissionV2",
    "PublicationPublishV2",
    "parse_input_manifest_freeze_v2",
    "parse_publication_admission_v2",
    "parse_publication_publish_v2",
]
