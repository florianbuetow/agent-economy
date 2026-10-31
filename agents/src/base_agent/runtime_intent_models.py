"""Strict immutable values for the durable runtime-intent journal."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from service_auth.financial_responses import validate_operation_id

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | JsonObject | JsonArray
type JsonObject = Mapping[str, JsonValue]
type JsonArray = tuple[JsonValue, ...]

SCHEMA_VERSION = 1
SQLITE_MAX_INTEGER = (1 << 63) - 1
SERVICE_URL_KEYS = frozenset(
    {
        "identity_url",
        "bank_url",
        "task_board_url",
        "reputation_url",
        "court_url",
    }
)
SLOTS = frozenset({"bid", "acceptance", "submission", "review-decision"})
UNFINISHED_STATES = frozenset({"pending", "blocked", "attention_required"})
STATES = UNFINISHED_STATES | {"completed", "definitive_rejection"}
_TASK_ID = re.compile(
    r"^t-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RuntimeIntentError(RuntimeError):
    """A stable local journal failure."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeIntentError("invalid_input", f"{field} must be a nonempty string")
    return value


def require_integer(
    value: object,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = SQLITE_MAX_INTEGER,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise RuntimeIntentError(
            "invalid_input",
            f"{field} must be an integer between {minimum} and {maximum}",
        )
    return value


def require_task_id(value: object) -> str:
    task_id = require_string(value, "task_id")
    if _TASK_ID.fullmatch(task_id) is None:
        raise RuntimeIntentError("invalid_input", "task_id is not canonical")
    return task_id


def require_operation_id(value: object, field: str = "operation_id") -> str:
    operation_id = require_string(value, field)
    try:
        return validate_operation_id(operation_id)
    except ValueError as exc:
        raise RuntimeIntentError("invalid_input", f"{field} is not canonical") from exc


def require_sha256(value: object, field: str) -> str:
    digest = require_string(value, field)
    if _SHA256.fullmatch(digest) is None:
        raise RuntimeIntentError("invalid_input", f"{field} is not a SHA-256 digest")
    return digest


def _freeze_json(value: object, *, field: str) -> JsonValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        number = value
        if not math.isfinite(number):
            raise RuntimeIntentError("invalid_input", f"{field} contains a non-finite number")
        return number
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RuntimeIntentError("invalid_input", f"{field} has a non-string key")
            frozen[key] = _freeze_json(item, field=field)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, field=field) for item in value)
    raise RuntimeIntentError("invalid_input", f"{field} is not a JSON value")


def freeze_object(value: object, *, field: str) -> JsonObject:
    frozen = _freeze_json(value, field=field)
    if not isinstance(frozen, Mapping):
        raise RuntimeIntentError("invalid_input", f"{field} must be a JSON object")
    return frozen


def thaw_json(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def canonical_json_bytes(value: object) -> bytes:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeIntentError("invalid_input", "value is not canonical JSON") from exc
    return rendered.encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _strict_keys(value: Mapping[str, JsonValue], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise RuntimeIntentError("invalid_input", f"{field} has unexpected fields")


def _mapping(value: JsonValue | None, field: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise RuntimeIntentError("invalid_input", f"{field} must be an object")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeOwner:
    """The exact local identity and deployment scope owning one journal."""

    schema_version: int
    environment_id: str
    role: str
    profile: str
    handle: str
    registered_agent_id: str
    key_fingerprint: str
    service_urls: JsonObject

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise RuntimeIntentError("invalid_input", "owner schema_version must be integer 1")
        for field in (
            "environment_id",
            "role",
            "profile",
            "handle",
            "registered_agent_id",
        ):
            require_string(getattr(self, field), field)
        fingerprint = require_string(self.key_fingerprint, "key_fingerprint")
        if _SHA256.fullmatch(fingerprint) is None:
            raise RuntimeIntentError("invalid_input", "key_fingerprint must be lowercase SHA-256")
        urls = freeze_object(self.service_urls, field="service_urls")
        if frozenset(urls) != SERVICE_URL_KEYS or any(
            not isinstance(url, str) or not url.strip() for url in urls.values()
        ):
            raise RuntimeIntentError(
                "invalid_input", "service_urls must contain the exact five nonempty URL strings"
            )
        object.__setattr__(self, "service_urls", urls)

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "environment_id": self.environment_id,
            "role": self.role,
            "profile": self.profile,
            "handle": self.handle,
            "registered_agent_id": self.registered_agent_id,
            "key_fingerprint": self.key_fingerprint,
            "service_urls": thaw_json(self.service_urls),
        }


@dataclass(frozen=True, slots=True)
class NewTaskIntent:
    """A complete caller-owned task mutation prepared before transport."""

    task_id: str
    slot: str
    action: str
    source_status: str
    target_status: str
    operation_id: str
    expected_lifecycle_version: int
    public_arguments: JsonObject
    acknowledgment_context: JsonObject

    def __post_init__(self) -> None:
        task_id = require_task_id(self.task_id)
        operation_id = require_operation_id(self.operation_id)
        version = require_integer(self.expected_lifecycle_version, "expected_lifecycle_version")
        if self.slot not in SLOTS:
            raise RuntimeIntentError("invalid_input", "slot is unsupported")
        arguments = freeze_object(self.public_arguments, field="public_arguments")
        context = freeze_object(self.acknowledgment_context, field="acknowledgment_context")
        _validate_action_contract(
            task_id=task_id,
            slot=self.slot,
            action=self.action,
            source_status=self.source_status,
            target_status=self.target_status,
            operation_id=operation_id,
            expected_lifecycle_version=version,
            arguments=arguments,
            context=context,
        )
        object.__setattr__(self, "public_arguments", arguments)
        object.__setattr__(self, "acknowledgment_context", context)

    def semantic_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "slot": self.slot,
            "action": self.action,
            "source_status": self.source_status,
            "target_status": self.target_status,
            "operation_id": self.operation_id,
            "expected_lifecycle_version": self.expected_lifecycle_version,
            "public_arguments": thaw_json(self.public_arguments),
            "acknowledgment_context": thaw_json(self.acknowledgment_context),
        }


@dataclass(frozen=True, slots=True)
class FrozenTaskIntent:
    """One validated immutable journal revision."""

    schema_version: int
    revision: int
    owner_sha256: str
    semantic_sha256: str
    task_id: str
    slot: str
    action: str
    source_status: str
    target_status: str
    operation_id: str
    expected_lifecycle_version: int
    public_arguments: JsonObject
    acknowledgment_context: JsonObject
    state: str
    result: JsonObject | None
    diagnostics: JsonArray

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise RuntimeIntentError("invalid_input", "intent schema_version must be integer 1")
        require_integer(self.revision, "revision", minimum=1)
        require_sha256(self.owner_sha256, "owner_sha256")
        require_sha256(self.semantic_sha256, "semantic_sha256")
        new_intent = NewTaskIntent(
            task_id=self.task_id,
            slot=self.slot,
            action=self.action,
            source_status=self.source_status,
            target_status=self.target_status,
            operation_id=self.operation_id,
            expected_lifecycle_version=self.expected_lifecycle_version,
            public_arguments=self.public_arguments,
            acknowledgment_context=self.acknowledgment_context,
        )
        if canonical_sha256(new_intent.semantic_json()) != self.semantic_sha256:
            raise RuntimeIntentError("invalid_input", "semantic_sha256 does not match intent")
        if self.state not in STATES:
            raise RuntimeIntentError("invalid_input", "intent state is unsupported")
        result = None if self.result is None else freeze_object(self.result, field="result")
        diagnostics_value = _freeze_json(self.diagnostics, field="diagnostics")
        if not isinstance(diagnostics_value, tuple) or any(
            not isinstance(item, Mapping) for item in diagnostics_value
        ):
            raise RuntimeIntentError("invalid_input", "diagnostics must be an array of objects")
        object.__setattr__(self, "public_arguments", new_intent.public_arguments)
        object.__setattr__(self, "acknowledgment_context", new_intent.acknowledgment_context)
        object.__setattr__(self, "result", result)
        object.__setattr__(self, "diagnostics", diagnostics_value)

    @classmethod
    def pending(cls, intent: NewTaskIntent, *, owner_sha256: str) -> FrozenTaskIntent:
        semantic_sha256 = canonical_sha256(intent.semantic_json())
        return cls(
            schema_version=SCHEMA_VERSION,
            revision=1,
            owner_sha256=owner_sha256,
            semantic_sha256=semantic_sha256,
            task_id=intent.task_id,
            slot=intent.slot,
            action=intent.action,
            source_status=intent.source_status,
            target_status=intent.target_status,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
            public_arguments=intent.public_arguments,
            acknowledgment_context=intent.acknowledgment_context,
            state="pending",
            result=None,
            diagnostics=(),
        )

    def with_observation(
        self,
        *,
        state: str,
        result: object | None,
        diagnostic: object | None,
        diagnostic_limit: int,
    ) -> FrozenTaskIntent:
        thawed_diagnostics = thaw_json(self.diagnostics)
        if not isinstance(thawed_diagnostics, list):
            raise RuntimeIntentError("corrupt_record", "diagnostics are not an array")
        diagnostics = thawed_diagnostics
        if diagnostic is not None:
            diagnostics.append(diagnostic)
            diagnostics = diagnostics[-diagnostic_limit:]
        return FrozenTaskIntent(
            schema_version=self.schema_version,
            revision=self.revision + 1,
            owner_sha256=self.owner_sha256,
            semantic_sha256=self.semantic_sha256,
            task_id=self.task_id,
            slot=self.slot,
            action=self.action,
            source_status=self.source_status,
            target_status=self.target_status,
            operation_id=self.operation_id,
            expected_lifecycle_version=self.expected_lifecycle_version,
            public_arguments=self.public_arguments,
            acknowledgment_context=self.acknowledgment_context,
            state=state,
            result=None if result is None else freeze_object(result, field="result"),
            diagnostics=tuple(cast("JsonValue", item) for item in diagnostics),
        )

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "owner_sha256": self.owner_sha256,
            "semantic_sha256": self.semantic_sha256,
            "task_id": self.task_id,
            "slot": self.slot,
            "action": self.action,
            "source_status": self.source_status,
            "target_status": self.target_status,
            "operation_id": self.operation_id,
            "expected_lifecycle_version": self.expected_lifecycle_version,
            "public_arguments": thaw_json(self.public_arguments),
            "acknowledgment_context": thaw_json(self.acknowledgment_context),
            "state": self.state,
            "result": None if self.result is None else thaw_json(self.result),
            "diagnostics": thaw_json(self.diagnostics),
        }


def _validate_action_contract(
    *,
    task_id: str,
    slot: str,
    action: str,
    source_status: str,
    target_status: str,
    operation_id: str,
    expected_lifecycle_version: int,
    arguments: JsonObject,
    context: JsonObject,
) -> None:
    contracts = {
        "submit_bid": ("bid", "open", "open", {"amount"}),
        "accept_bid": ("acceptance", "open", "accepted", {"bid_id"}),
        "submit_deliverable": ("submission", "accepted", "submitted", set()),
        "approve_task": ("review-decision", "submitted", "approved", set()),
        "dispute_task": ("review-decision", "submitted", "disputed", {"reason"}),
    }
    contract = contracts.get(action)
    if contract is None or (slot, source_status, target_status) != contract[:3]:
        raise RuntimeIntentError("invalid_input", "action, slot, or lifecycle states conflict")
    expected_argument_keys = {
        "task_id",
        "operation_id",
        "expected_lifecycle_version",
        *contract[3],
    }
    _strict_keys(arguments, expected_argument_keys, "public_arguments")
    argument_version = require_integer(
        arguments["expected_lifecycle_version"], "public expected_lifecycle_version"
    )
    if (
        arguments["task_id"] != task_id
        or arguments["operation_id"] != operation_id
        or argument_version != expected_lifecycle_version
    ):
        raise RuntimeIntentError("invalid_input", "public_arguments identity does not match intent")
    if action == "submit_bid":
        require_integer(arguments["amount"], "amount", minimum=1)
    elif action == "accept_bid":
        require_string(arguments["bid_id"], "bid_id")
    elif action == "dispute_task":
        require_string(arguments["reason"], "reason")

    context_keys = {
        "response_kind",
        "operation_id",
        "task_id",
        "command",
        "source_status",
        "source_lifecycle_version",
        "target_status",
        "target_lifecycle_version",
        "result_facts",
        "actor_id",
        "frozen_task_facts",
    }
    if action != "submit_bid":
        context_keys.add("request_sha256")
    _strict_keys(context, context_keys, "acknowledgment_context")
    source_version = require_integer(
        context["source_lifecycle_version"], "source_lifecycle_version"
    )
    target_version = require_integer(
        context["target_lifecycle_version"], "target_lifecycle_version"
    )
    if (
        context["response_kind"] != ("bid" if action == "submit_bid" else "task_mutation")
        or context["operation_id"] != operation_id
        or context["task_id"] != task_id
        or context["command"] != action
        or context["source_status"] != source_status
        or source_version != expected_lifecycle_version
        or context["target_status"] != target_status
        or target_version != expected_lifecycle_version + (0 if action == "submit_bid" else 1)
    ):
        raise RuntimeIntentError("invalid_input", "acknowledgment context conflicts with intent")
    require_string(context["actor_id"], "actor_id")
    _mapping(context["result_facts"], "result_facts")
    frozen_facts = _mapping(context["frozen_task_facts"], "frozen_task_facts")
    expected_actor = (
        frozen_facts.get("poster_id")
        if action in {"accept_bid", "approve_task", "dispute_task"}
        else frozen_facts.get("bidder_id")
        if action == "submit_bid"
        else frozen_facts.get("worker_id")
    )
    if context["actor_id"] != expected_actor:
        raise RuntimeIntentError("invalid_input", "intent actor contradicts frozen task facts")
    if action != "submit_bid":
        require_sha256(context["request_sha256"], "request_sha256")


def owner_from_json(value: object) -> RuntimeOwner:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "environment_id",
        "role",
        "profile",
        "handle",
        "registered_agent_id",
        "key_fingerprint",
        "service_urls",
    }:
        raise RuntimeIntentError("corrupt_record", "owner record has an invalid shape")
    try:
        return RuntimeOwner(**value)
    except (RuntimeIntentError, TypeError) as exc:
        raise RuntimeIntentError("corrupt_record", "owner record is invalid") from exc


def intent_from_json(value: object) -> FrozenTaskIntent:
    expected = {
        "schema_version",
        "revision",
        "owner_sha256",
        "semantic_sha256",
        "task_id",
        "slot",
        "action",
        "source_status",
        "target_status",
        "operation_id",
        "expected_lifecycle_version",
        "public_arguments",
        "acknowledgment_context",
        "state",
        "result",
        "diagnostics",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeIntentError("corrupt_record", "intent record has an invalid shape")
    try:
        return FrozenTaskIntent(**value)
    except (RuntimeIntentError, TypeError) as exc:
        raise RuntimeIntentError("corrupt_record", "intent record is invalid") from exc
