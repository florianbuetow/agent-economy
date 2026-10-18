"""Validation of owner-bound intents and public Task Board acknowledgments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from base_agent.runtime_intent_models import (
    SQLITE_MAX_INTEGER,
    FrozenTaskIntent,
    JsonValue,
    NewTaskIntent,
    RuntimeIntentError,
    RuntimeOwner,
    canonical_sha256,
    require_operation_id,
    require_sha256,
    thaw_json,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_TASK_STATUSES = frozenset(
    {"open", "accepted", "submitted", "approved", "cancelled", "disputed", "ruled", "expired"}
)


@dataclass(frozen=True, slots=True)
class AcknowledgmentObservation:
    """The durable state derived from one transport observation."""

    state: str
    result: object | None
    diagnostic: object | None


def _object(value: JsonValue | object) -> Mapping[str, object] | None:
    if not isinstance(value, dict) and not hasattr(value, "items"):
        return None
    mapping = cast("Mapping[object, object]", value)
    if any(not isinstance(key, str) for key in mapping):
        return None
    return cast("Mapping[str, object]", mapping)


def _strict_int(value: object) -> bool:
    return type(value) is int


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _same(actual: object, expected: object) -> bool:
    actual_mapping = _object(actual)
    expected_mapping = _object(expected)
    if actual_mapping is not None or expected_mapping is not None:
        return (
            actual_mapping is not None
            and expected_mapping is not None
            and set(actual_mapping) == set(expected_mapping)
            and all(_same(actual_mapping[key], expected_mapping[key]) for key in expected_mapping)
        )
    if isinstance(actual, (list, tuple)) or isinstance(expected, (list, tuple)):
        return (
            isinstance(actual, (list, tuple))
            and isinstance(expected, (list, tuple))
            and len(actual) == len(expected)
            and all(_same(left, right) for left, right in zip(actual, expected, strict=True))
        )
    return type(actual) is type(expected) and actual == expected


def _exact_mapping(actual: object, expected: Mapping[str, object]) -> bool:
    value = _object(actual)
    return (
        value is not None
        and set(value) == set(expected)
        and all(_same(value[key], expected_value) for key, expected_value in expected.items())
    )


def _context(intent: NewTaskIntent | FrozenTaskIntent) -> Mapping[str, object]:
    return cast("Mapping[str, object]", intent.acknowledgment_context)


def _frozen_facts(intent: NewTaskIntent | FrozenTaskIntent) -> Mapping[str, object]:
    facts = _object(_context(intent).get("frozen_task_facts"))
    if facts is None:
        raise RuntimeIntentError("invalid_input", "frozen_task_facts must be an object")
    return facts


def _result_facts(intent: NewTaskIntent | FrozenTaskIntent) -> Mapping[str, object]:
    facts = _object(_context(intent).get("result_facts"))
    if facts is None:
        raise RuntimeIntentError("invalid_input", "result_facts must be an object")
    return facts


def _native_arguments(intent: NewTaskIntent | FrozenTaskIntent) -> dict[str, object]:
    public = cast("Mapping[str, object]", intent.public_arguments)
    frozen = _frozen_facts(intent)
    if intent.action == "accept_bid":
        return {
            "bid_id": public["bid_id"],
            "worker_id": frozen["worker_id"],
            "escrow_id": frozen["escrow_id"],
            "reward": frozen["accepted_reward"],
            "bid_amount": frozen["selected_bid_amount"],
        }
    if intent.action == "submit_deliverable":
        return {"worker_id": frozen["worker_id"]}
    common = {
        "poster_id": frozen["poster_id"],
        "worker_id": frozen["worker_id"],
        "escrow_id": frozen["escrow_id"],
        "reward": frozen["accepted_reward"],
    }
    if intent.action == "approve_task":
        common["source_reference"] = f"task:{intent.task_id}:approved"
    else:
        common["reason"] = public["reason"]
    return common


def expected_request_sha256(intent: NewTaskIntent | FrozenTaskIntent) -> str:
    context = _context(intent)
    request = {
        "protocol_version": 1,
        "task_id": intent.task_id,
        "command": intent.action,
        "expected_status": intent.source_status,
        "expected_lifecycle_version": intent.expected_lifecycle_version,
        "actor_kind": "agent",
        "actor_id": context["actor_id"],
        "arguments": _native_arguments(intent),
    }
    return canonical_sha256(request)


def validate_owner_intent(owner: RuntimeOwner, intent: NewTaskIntent | FrozenTaskIntent) -> None:
    """Bind a new intent to its owner and independently derive immutable facts."""

    context = _context(intent)
    actor_id = context.get("actor_id")
    if actor_id != owner.registered_agent_id:
        raise RuntimeIntentError(
            "owner_intent_mismatch", "intent actor does not match the journal owner"
        )
    public = cast("Mapping[str, object]", intent.public_arguments)
    frozen = _frozen_facts(intent)
    result = _result_facts(intent)
    if intent.action == "submit_bid":
        _validate_bid_facts(intent, actor_id, public, frozen, result)
    else:
        _validate_mutation_facts(intent, actor_id, public, frozen, result)
    if intent.action != "submit_bid" and context.get("request_sha256") != expected_request_sha256(
        intent
    ):
        raise RuntimeIntentError("invalid_input", "request hash contradicts frozen intent facts")


def _validate_bid_facts(
    intent: NewTaskIntent | FrozenTaskIntent,
    actor_id: object,
    public: Mapping[str, object],
    frozen: Mapping[str, object],
    result: Mapping[str, object],
) -> None:
    expected_frozen = {"bidder_id": actor_id}
    expected_result = {
        "bid_id": f"bid-{intent.operation_id[3:]}",
        "bidder_id": actor_id,
        "amount": public["amount"],
    }
    if not _exact_mapping(frozen, expected_frozen) or not _exact_mapping(result, expected_result):
        raise RuntimeIntentError("invalid_input", "bid facts contradict the intent")


def _validate_mutation_facts(
    intent: NewTaskIntent | FrozenTaskIntent,
    actor_id: object,
    public: Mapping[str, object],
    frozen: Mapping[str, object],
    result: Mapping[str, object],
) -> None:
    expected_frozen_keys = {
        "poster_id",
        "worker_id",
        "escrow_id",
        "accepted_reward",
        "selected_bid_amount",
    }
    if set(frozen) != expected_frozen_keys:
        raise RuntimeIntentError("invalid_input", "frozen task facts are incomplete")
    poster = frozen["poster_id"]
    worker = frozen["worker_id"]
    reward = frozen["accepted_reward"]
    bid_amount = frozen["selected_bid_amount"]
    valid_values = (
        _nonempty_string(poster)
        and _nonempty_string(worker)
        and _nonempty_string(frozen["escrow_id"])
        and type(reward) is int
        and 0 < reward <= SQLITE_MAX_INTEGER
        and type(bid_amount) is int
        and 0 < bid_amount <= SQLITE_MAX_INTEGER
    )
    if not valid_values:
        raise RuntimeIntentError("invalid_input", "frozen task facts are invalid")
    expected_actor = (
        poster if intent.action in {"accept_bid", "approve_task", "dispute_task"} else worker
    )
    if actor_id != expected_actor:
        raise RuntimeIntentError(
            "owner_intent_mismatch", "mutation action is not bound to the owner"
        )
    expected_result: dict[str, object]
    if intent.action == "accept_bid":
        expected_result = {
            "bid_id": public["bid_id"],
            "worker_id": worker,
            "reward": reward,
            "bid_amount": bid_amount,
        }
    elif intent.action == "submit_deliverable":
        expected_result = {"worker_id": worker}
    elif intent.action == "approve_task":
        expected_result = {
            "worker_id": worker,
            "reward": reward,
            "effect_result_reference": intent.operation_id,
        }
    else:
        expected_result = {
            "worker_id": worker,
            "reason": public["reason"],
        }
    if not _exact_mapping(result, expected_result):
        raise RuntimeIntentError("invalid_input", "result facts contradict the intent")


def _expected_effect(
    intent: FrozenTaskIntent, *, dispute_reference: object | None
) -> tuple[object, ...]:
    frozen = _frozen_facts(intent)
    if intent.action in {"accept_bid", "submit_deliverable"}:
        return ("none", "none", None, None, None)
    if intent.action == "approve_task":
        financial = {
            "request_version": 1,
            "operation_kind": "escrow_release",
            "escrow_id": frozen["escrow_id"],
            "source_reference": f"task:{intent.task_id}:approved",
            "recipient_account_id": frozen["worker_id"],
        }
        effect_intent = {
            "financial_request": financial,
            "financial_request_sha256": canonical_sha256(financial),
            "expected_escrow": {
                "escrow_id": frozen["escrow_id"],
                "task_id": intent.task_id,
                "payer_account_id": frozen["poster_id"],
                "amount": frozen["accepted_reward"],
            },
        }
        return (
            "bank_release",
            "succeeded",
            intent.operation_id,
            intent.operation_id,
            effect_intent,
        )
    effect_intent = {
        "court_claim": {
            "task_id": intent.task_id,
            "claimant_id": frozen["poster_id"],
            "respondent_id": frozen["worker_id"],
            "reason": cast("Mapping[str, object]", intent.public_arguments)["reason"],
        },
        "expected_escrow": {
            "escrow_id": frozen["escrow_id"],
            "task_id": intent.task_id,
            "payer_account_id": frozen["poster_id"],
            "amount": frozen["accepted_reward"],
        },
    }
    return ("court_claim", "succeeded", None, dispute_reference, effect_intent)


_OPERATION_KEYS = {
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


def _operation_shape(operation: Mapping[str, object]) -> bool:
    operation_id = operation.get("operation_id")
    task_id = operation.get("task_id")
    request_sha256 = operation.get("request_sha256")
    return (
        set(operation) == _OPERATION_KEYS
        and _strict_int(operation.get("protocol_version"))
        and _is_operation_id(operation_id)
        and _nonempty_string(task_id)
        and _nonempty_string(operation.get("command"))
        and _is_sha256(request_sha256)
        and _nonempty_string(operation.get("operation_state"))
        and _nonempty_string(operation.get("source_status"))
        and _bounded_nonnegative_integer(operation.get("source_lifecycle_version"))
        and _nonempty_string(operation.get("target_status"))
        and _bounded_nonnegative_integer(operation.get("target_lifecycle_version"))
        and _nonempty_string(operation.get("effect_kind"))
        and _nonempty_string(operation.get("effect_state"))
        and (
            operation.get("effect_operation_id") is None
            or _nonempty_string(operation.get("effect_operation_id"))
        )
        and (
            operation.get("effect_result_reference") is None
            or _nonempty_string(operation.get("effect_result_reference"))
        )
        and (
            operation.get("effect_intent") is None
            or _object(operation.get("effect_intent")) is not None
        )
        and (
            operation.get("event_id") is None
            or _bounded_positive_integer(operation.get("event_id"))
        )
        and (operation.get("result") is None or _object(operation.get("result")) is not None)
    )


def _operation_identity_shape(operation: Mapping[str, object]) -> bool:
    return (
        set(operation) == _OPERATION_KEYS
        and operation.get("protocol_version") == 1
        and _is_operation_id(operation.get("operation_id"))
        and _nonempty_string(operation.get("task_id"))
        and _nonempty_string(operation.get("command"))
        and _is_sha256(operation.get("request_sha256"))
        and _nonempty_string(operation.get("source_status"))
        and _bounded_nonnegative_integer(operation.get("source_lifecycle_version"))
        and _nonempty_string(operation.get("target_status"))
        and _bounded_nonnegative_integer(operation.get("target_lifecycle_version"))
    )


def _is_operation_id(value: object) -> bool:
    try:
        require_operation_id(value)
    except RuntimeIntentError:
        return False
    return True


def _is_sha256(value: object) -> bool:
    try:
        require_sha256(value, "request_sha256")
    except RuntimeIntentError:
        return False
    return True


def _bounded_nonnegative_integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= SQLITE_MAX_INTEGER


def _bounded_positive_integer(value: object) -> bool:
    return type(value) is int and 1 <= value <= SQLITE_MAX_INTEGER


def _operation_identity_matches(
    intent: FrozenTaskIntent, body: Mapping[str, object], operation: Mapping[str, object]
) -> bool:
    frozen = _frozen_facts(intent)
    return (
        operation.get("protocol_version") == 1
        and operation.get("operation_id") == intent.operation_id
        and operation.get("task_id") == intent.task_id
        and operation.get("command") == intent.action
        and operation.get("request_sha256") == expected_request_sha256(intent)
        and operation.get("source_status") == intent.source_status
        and operation.get("source_lifecycle_version") == intent.expected_lifecycle_version
        and operation.get("target_status") == intent.target_status
        and operation.get("target_lifecycle_version") == intent.expected_lifecycle_version + 1
        and body.get("task_id") == intent.task_id
        and body.get("worker_id") == frozen["worker_id"]
    )


def _completed_operation_matches(intent: FrozenTaskIntent, operation: Mapping[str, object]) -> bool:
    result = _object(operation.get("result"))
    event_id = operation.get("event_id")
    if result is None or not _bounded_positive_integer(event_id):
        return False
    result_facts = dict(_result_facts(intent))
    dispute_reference = operation.get("effect_result_reference")
    if intent.action == "dispute_task":
        if not _nonempty_string(dispute_reference):
            return False
        result_facts.update(
            dispute_id=dispute_reference,
            effect_result_reference=dispute_reference,
        )
    expected_result = {
        "task_id": intent.task_id,
        "command": intent.action,
        "status": intent.target_status,
        "lifecycle_version": intent.expected_lifecycle_version + 1,
        "event_id": event_id,
        **result_facts,
    }
    if not _exact_mapping(result, expected_result):
        return False
    effect = _expected_effect(intent, dispute_reference=dispute_reference)
    return (
        operation.get("operation_state") == "completed"
        and operation.get("effect_kind") == effect[0]
        and operation.get("effect_state") == effect[1]
        and operation.get("effect_operation_id") == effect[2]
        and operation.get("effect_result_reference") == effect[3]
        and _same(thaw_json(cast("JsonValue", operation.get("effect_intent"))), effect[4])
    )


def _classify_bid(
    intent: FrozenTaskIntent, status_code: int, body: object
) -> AcknowledgmentObservation:
    value = _object(body)
    expected = {
        **dict(_result_facts(intent)),
        "task_id": intent.task_id,
    }
    if status_code == 201 and value is not None:
        shape = (
            set(value) == {"bid_id", "task_id", "bidder_id", "amount", "submitted_at"}
            and _nonempty_string(value.get("bid_id"))
            and _nonempty_string(value.get("task_id"))
            and _nonempty_string(value.get("bidder_id"))
            and _strict_int(value.get("amount"))
            and _nonempty_string(value.get("submitted_at"))
        )
        if shape and all(_same(value.get(key), item) for key, item in expected.items()):
            return AcknowledgmentObservation(
                "completed", {"status_code": 201, "body": dict(value)}, None
            )
        if shape:
            return AcknowledgmentObservation(
                "attention_required", None, {"kind": "foreign_success"}
            )
    return _non_success(intent, status_code, body)


def _classify_mutation(
    intent: FrozenTaskIntent, status_code: int, body: object
) -> AcknowledgmentObservation:
    value = _object(body)
    operation = None if value is None else _object(value.get("operation"))
    if status_code not in {200, 202} or operation is None or value is None:
        return _non_success(intent, status_code, body)
    if not _operation_identity_shape(operation):
        return _non_success(intent, status_code, body)
    if not _operation_identity_matches(intent, value, operation):
        return AcknowledgmentObservation("attention_required", None, {"kind": "foreign_operation"})
    if not _operation_shape(operation):
        return AcknowledgmentObservation("attention_required", None, {"kind": "integrity_conflict"})
    return _matching_mutation_observation(intent, status_code, operation)


def _matching_mutation_observation(
    intent: FrozenTaskIntent,
    status_code: int,
    operation: Mapping[str, object],
) -> AcknowledgmentObservation:
    if status_code == 200:
        if _completed_operation_matches(intent, operation):
            return AcknowledgmentObservation(
                "completed",
                {"status_code": 200, "operation": dict(operation)},
                None,
            )
        return AcknowledgmentObservation("attention_required", None, {"kind": "integrity_conflict"})
    effect = _expected_effect(intent, dispute_reference=None)
    matching_pending = (
        operation.get("operation_state") == "claimed"
        and operation.get("event_id") is None
        and operation.get("result") is None
        and operation.get("effect_kind") == effect[0]
        and operation.get("effect_state") == ("pending" if effect[0] != "none" else "none")
        and operation.get("effect_operation_id") == effect[2]
        and operation.get("effect_result_reference") is None
        and _same(thaw_json(cast("JsonValue", operation.get("effect_intent"))), effect[4])
    )
    if matching_pending:
        return AcknowledgmentObservation("pending", None, {"kind": "remote_pending"})
    return AcknowledgmentObservation("attention_required", None, {"kind": "integrity_conflict"})


def _non_success(
    intent: FrozenTaskIntent, status_code: int, body: object
) -> AcknowledgmentObservation:
    value = _object(body)
    if status_code == 409 and value is not None:
        details = _object(value.get("details"))
        if (
            value.get("error") == "transition_pending"
            and _nonempty_string(value.get("message"))
            and set(value) == {"error", "message", "details"}
            and details is not None
            and set(details) == {"winning_operation_id"}
            and _is_operation_id(details.get("winning_operation_id"))
        ):
            winning_operation_id = details["winning_operation_id"]
            if winning_operation_id == intent.operation_id:
                return AcknowledgmentObservation("pending", None, {"kind": "remote_pending"})
            return AcknowledgmentObservation(
                "blocked",
                None,
                {
                    "kind": "foreign_pending_owner",
                    "operation_id": intent.operation_id,
                    "winning_operation_id": winning_operation_id,
                },
            )
        definitive = _normalized_definitive_error(intent, value, details)
        if definitive is not None:
            return AcknowledgmentObservation(
                "definitive_rejection",
                {"status_code": status_code, "error": definitive},
                {"kind": "definitive_nonapplication", "status_code": status_code},
            )
    return AcknowledgmentObservation(
        "pending", None, {"kind": "pending_unknown", "status_code": status_code}
    )


def _normalized_definitive_error(
    intent: FrozenTaskIntent,
    value: Mapping[str, object],
    details: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if set(value) != {"error", "message", "details"} or details is None:
        return None
    error = value.get("error")
    message = value.get("message")
    if error == "task_version_conflict":
        expected_keys = {
            "expected_lifecycle_version",
            "actual_lifecycle_version",
            "current_status",
        }
        actual = details.get("actual_lifecycle_version")
        valid = (
            _nonempty_string(message)
            and set(details) == expected_keys
            and type(details.get("expected_lifecycle_version")) is int
            and details.get("expected_lifecycle_version") == intent.expected_lifecycle_version
            and type(actual) is int
            and intent.expected_lifecycle_version < actual <= SQLITE_MAX_INTEGER
            and _nonempty_string(details.get("current_status"))
        )
    elif error == "invalid_status":
        expected_status = details.get("expected_status")
        current_status = details.get("current_status")
        valid = (
            _nonempty_string(message)
            and set(details) == {"expected_status", "current_status"}
            and isinstance(expected_status, str)
            and expected_status == intent.source_status
            and isinstance(current_status, str)
            and current_status in _TASK_STATUSES
            and current_status != intent.source_status
        )
    elif error == "cutoff_reached":
        valid = _nonempty_string(message) and not details
    else:
        return None
    if not valid:
        return None
    return {
        "error": error,
        "message": message,
        "details": dict(details),
    }


def classify_acknowledgment(
    intent: FrozenTaskIntent, *, status_code: int, body: object
) -> AcknowledgmentObservation:
    """Classify one public command response without any remote read."""

    if type(status_code) is not int or not 100 <= status_code <= 599:
        raise RuntimeIntentError("invalid_input", "status_code must be a valid HTTP integer")
    if intent.action == "submit_bid":
        return _classify_bid(intent, status_code, body)
    return _classify_mutation(intent, status_code, body)


def validate_stored_intent(owner: RuntimeOwner, intent: FrozenTaskIntent) -> None:
    """Recheck owner binding and normalized mutable fields after every file read."""

    validate_owner_intent(owner, intent)
    _validate_stored_diagnostics(intent)
    _validate_stored_state(intent)
    if intent.state == "definitive_rejection":
        result = _object(intent.result)
        if result is None or set(result) != {"status_code", "error"}:
            raise RuntimeIntentError("corrupt_record", "definitive rejection lacks evidence")
        status_code = result.get("status_code")
        observation = classify_acknowledgment(
            intent,
            status_code=status_code if type(status_code) is int else 0,
            body=result.get("error"),
        )
        if observation.state != "definitive_rejection" or not _same(
            observation.result, thaw_json(intent.result)
        ):
            raise RuntimeIntentError("corrupt_record", "definitive rejection evidence is invalid")
        return
    if intent.state != "completed":
        return
    result = _object(intent.result)
    status_code = None if result is None else result.get("status_code")
    if result is None or type(status_code) is not int:
        raise RuntimeIntentError("corrupt_record", "completed intent result is malformed")
    if intent.action == "submit_bid":
        if set(result) != {"status_code", "body"}:
            raise RuntimeIntentError("corrupt_record", "completed bid result is malformed")
        observation = classify_acknowledgment(intent, status_code=status_code, body=result["body"])
    else:
        if set(result) != {"status_code", "operation"}:
            raise RuntimeIntentError("corrupt_record", "completed mutation result is malformed")
        frozen = _frozen_facts(intent)
        body = {
            "task_id": intent.task_id,
            "worker_id": frozen["worker_id"],
            "operation": result["operation"],
        }
        observation = classify_acknowledgment(intent, status_code=status_code, body=body)
    if observation.state != "completed" or not _same(observation.result, thaw_json(intent.result)):
        raise RuntimeIntentError("corrupt_record", "completed result fails acknowledgment checks")


def _validate_stored_state(intent: FrozenTaskIntent) -> None:
    if intent.revision < len(intent.diagnostics) + 1:
        raise RuntimeIntentError("corrupt_record", "intent revision predates its diagnostics")
    if intent.revision == 1 and (
        intent.state != "pending" or intent.result is not None or intent.diagnostics
    ):
        raise RuntimeIntentError("corrupt_record", "initial intent revision is inconsistent")
    if intent.state not in {"completed", "definitive_rejection"} and intent.result is not None:
        raise RuntimeIntentError("corrupt_record", "unfinished intent has a result")
    if intent.state in {"completed", "definitive_rejection"} and intent.result is None:
        raise RuntimeIntentError("corrupt_record", "terminal intent lacks normalized evidence")
    if not intent.diagnostics:
        if intent.state not in {"pending", "completed"}:
            raise RuntimeIntentError("corrupt_record", "intent state lacks diagnostic evidence")
        return
    last = _object(intent.diagnostics[-1])
    last_kind = None if last is None else last.get("kind")
    permitted = {
        "pending": {"pending_unknown", "remote_pending"},
        "blocked": {"foreign_pending_owner"},
        "attention_required": {
            "foreign_success",
            "foreign_operation",
            "integrity_conflict",
        },
        "definitive_rejection": {"definitive_nonapplication"},
        "completed": {
            "pending_unknown",
            "remote_pending",
            "foreign_pending_owner",
            "foreign_success",
            "foreign_operation",
            "integrity_conflict",
        },
    }
    if last_kind not in permitted[intent.state]:
        raise RuntimeIntentError("corrupt_record", "intent state contradicts its diagnostics")


def _validate_stored_diagnostics(intent: FrozenTaskIntent) -> None:
    simple = {
        "foreign_success",
        "foreign_operation",
        "integrity_conflict",
        "remote_pending",
    }
    for raw in intent.diagnostics:
        diagnostic = _object(raw)
        if diagnostic is None or not isinstance(diagnostic.get("kind"), str):
            raise RuntimeIntentError("corrupt_record", "intent diagnostic is malformed")
        kind = diagnostic["kind"]
        if kind in simple and set(diagnostic) == {"kind"}:
            continue
        if kind in {"pending_unknown", "definitive_nonapplication"} and (
            set(diagnostic) == {"kind", "status_code"}
            and type(diagnostic.get("status_code")) is int
        ):
            continue
        if kind == "foreign_pending_owner" and (
            set(diagnostic) == {"kind", "operation_id", "winning_operation_id"}
            and diagnostic.get("operation_id") == intent.operation_id
            and _is_operation_id(diagnostic.get("winning_operation_id"))
        ):
            continue
        raise RuntimeIntentError("corrupt_record", "intent diagnostic is malformed")
