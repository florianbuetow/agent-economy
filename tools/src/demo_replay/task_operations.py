"""Native task intent snapshots and public response checks for the demo driver."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from service_auth.financial_responses import SQLITE_MAX_INTEGER, validate_operation_id

PHASES = {
    "bid": ("submit_bid", "open", "open"),
    "accept_bid": ("accept_bid", "open", "accepted"),
    "submit_deliverable": ("submit_deliverable", "accepted", "submitted"),
    "approve": ("approve_task", "submitted", "approved"),
    "dispute": ("dispute_task", "submitted", "disputed"),
}


def canonical_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def required_text(values: dict[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a nonempty string")
    return value


def exact_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def operation_identity(operation_id: str, expected_lifecycle_version: int) -> tuple[str, int]:
    return validate_operation_id(operation_id), exact_integer(
        expected_lifecycle_version, "expected_lifecycle_version", 0, SQLITE_MAX_INTEGER
    )


def posting_duration(value: object, name: str) -> int:
    return exact_integer(value, name, 1, SQLITE_MAX_INTEGER // 1_000_000)


def posting_terms(step: dict[str, Any]) -> dict[str, int]:
    return {
        name: posting_duration(step.get(name), name)
        for name in (
            "bidding_deadline_seconds",
            "acceptance_window_seconds",
            "execution_deadline_seconds",
            "review_deadline_seconds",
        )
    }


def step_identity(step: dict[str, Any]) -> str:
    """Freeze business input; presentation delay does not change the signed request."""
    return canonical_json({key: value for key, value in step.items() if key != "delay"})


def _object(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Stored task intent is not an object")
    return value


@dataclass(frozen=True)
class TaskIntent:
    action: str
    step_json: str
    operation_id: str
    expected_lifecycle_version: int
    actor_handle: str
    actor_id: str
    actor_public_key: str
    task_id: str
    arguments_json: str
    observed_task_json: str

    @classmethod
    def from_json(cls, value: object) -> TaskIntent:
        if not isinstance(value, dict):
            raise ValueError("Stored task intent must be an object")
        expected = {
            "action",
            "step_json",
            "operation_id",
            "expected_lifecycle_version",
            "actor_handle",
            "actor_id",
            "actor_public_key",
            "task_id",
            "arguments_json",
            "observed_task_json",
        }
        if set(value) != expected:
            raise ValueError("Stored task intent fields differ")
        texts = expected - {"expected_lifecycle_version"}
        if any(not isinstance(value[field], str) or not value[field] for field in texts):
            raise ValueError("Stored task intent text is invalid")
        operation_id, version = operation_identity(
            value["operation_id"], value["expected_lifecycle_version"]
        )
        intent = cls(
            action=value["action"],
            step_json=value["step_json"],
            operation_id=operation_id,
            expected_lifecycle_version=version,
            actor_handle=value["actor_handle"],
            actor_id=value["actor_id"],
            actor_public_key=value["actor_public_key"],
            task_id=value["task_id"],
            arguments_json=value["arguments_json"],
            observed_task_json=value["observed_task_json"],
        )
        intent.arguments()
        intent.observed_task()
        return intent

    def as_json(self) -> dict[str, object]:
        return {
            "action": self.action,
            "step_json": self.step_json,
            "operation_id": self.operation_id,
            "expected_lifecycle_version": self.expected_lifecycle_version,
            "actor_handle": self.actor_handle,
            "actor_id": self.actor_id,
            "actor_public_key": self.actor_public_key,
            "task_id": self.task_id,
            "arguments_json": self.arguments_json,
            "observed_task_json": self.observed_task_json,
        }

    def arguments(self) -> dict[str, Any]:
        return _object(self.arguments_json)

    def observed_task(self) -> dict[str, Any]:
        return _object(self.observed_task_json)

    def verify_actor(self, actor_handle: str, actor_id: str, actor_public_key: str) -> None:
        if (
            actor_handle != self.actor_handle
            or actor_id != self.actor_id
            or actor_public_key != self.actor_public_key
        ):
            raise ValueError("The original task intent actor changed")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"Invalid task operation response: {message}")


def completed_bid(intent: TaskIntent, value: object) -> dict[str, Any]:
    _require(isinstance(value, dict), "bid must be an object")
    if not isinstance(value, dict):
        raise RuntimeError("Invalid bid response")
    expected = {
        "bid_id": f"bid-{intent.operation_id[3:]}",
        "task_id": intent.task_id,
        "bidder_id": intent.actor_id,
        "amount": intent.arguments()["amount"],
    }
    _require(
        set(value) == {"bid_id", "task_id", "bidder_id", "amount", "submitted_at"}, "bid fields"
    )
    for key, item in expected.items():
        _require(type(value.get(key)) is type(item) and value.get(key) == item, f"bid {key}")
    required_text(value, "submitted_at")
    return dict(value)


def _native_arguments(intent: TaskIntent) -> dict[str, Any]:
    observed = intent.observed_task()
    args = intent.arguments()
    if intent.action == "accept_bid":
        return {
            "bid_id": args["bid_id"],
            "worker_id": args["worker_id"],
            "escrow_id": observed["escrow_id"],
            "reward": observed["reward"],
            "bid_amount": args["bid_amount"],
        }
    if intent.action == "submit_deliverable":
        return {"worker_id": intent.actor_id}
    common = {
        "poster_id": observed["poster_id"],
        "worker_id": observed["worker_id"],
        "escrow_id": observed["escrow_id"],
        "reward": observed["accepted_reward"],
    }
    if intent.action == "approve":
        common["source_reference"] = f"task:{intent.task_id}:approved"
    else:
        common["reason"] = args["reason"]
    return common


def completed_mutation(intent: TaskIntent, value: object) -> tuple[dict[str, Any], dict[str, Any]]:
    """Confirm this operation; preserve the separate, possibly later current task view."""
    _require(isinstance(value, dict), "task must be an object")
    if not isinstance(value, dict):
        raise RuntimeError("Invalid task response")
    operation = value.get("operation")
    _require(isinstance(operation, dict), "operation envelope is missing")
    if not isinstance(operation, dict):
        raise RuntimeError("Invalid operation response")
    command, source, target = PHASES[intent.action]
    version = intent.expected_lifecycle_version
    arguments = _native_arguments(intent)
    native = {
        "protocol_version": 1,
        "task_id": intent.task_id,
        "command": command,
        "expected_status": source,
        "expected_lifecycle_version": version,
        "actor_kind": "agent",
        "actor_id": intent.actor_id,
        "arguments": arguments,
    }
    expected = {
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "command": command,
        "source_status": source,
        "source_lifecycle_version": version,
        "target_status": target,
        "target_lifecycle_version": version + 1,
        "request_sha256": _hash(native),
    }
    for key, item in expected.items():
        _require(type(operation.get(key)) is type(item) and operation.get(key) == item, key)
    state = operation.get("operation_state")
    if state == "claimed":
        raise RuntimeError(
            f"Task operation {intent.operation_id} is pending; the demo cannot advance"
        )
    _require(state == "completed", "operation is not completed")
    event_id = exact_integer(operation.get("event_id"), "event_id", 1, SQLITE_MAX_INTEGER)
    result = operation.get("result")
    _require(isinstance(result, dict), "completed result is missing")
    if not isinstance(result, dict):
        raise RuntimeError("Invalid completed result")
    expected_result = {
        "task_id": intent.task_id,
        "command": command,
        "status": target,
        "lifecycle_version": version + 1,
        "event_id": event_id,
    }
    for key, item in expected_result.items():
        _require(type(result.get(key)) is type(item) and result.get(key) == item, f"result {key}")
    expected_worker = intent.arguments().get("worker_id", intent.observed_task().get("worker_id"))
    if intent.action == "submit_deliverable":
        expected_worker = intent.actor_id
    _require(isinstance(expected_worker, str) and bool(expected_worker), "expected worker")
    _require(result.get("worker_id") == expected_worker, "result worker")
    _require(
        value.get("task_id") == intent.task_id and value.get("worker_id") == expected_worker,
        "current task identity",
    )
    current_version = exact_integer(
        value.get("lifecycle_version"), "lifecycle_version", 0, SQLITE_MAX_INTEGER
    )
    _require(current_version >= version + 1, "current version precedes completion")
    following = {
        "accepted": {"accepted", "submitted", "approved", "expired", "disputed", "ruled"},
        "submitted": {"submitted", "approved", "disputed", "ruled"},
        "approved": {"approved"},
        "disputed": {"disputed", "ruled"},
    }
    _require(value.get("status") in following[target], "current task phase")
    _validate_effect(intent, operation, result, arguments)
    if intent.action == "accept_bid":
        for key in ("bid_id", "reward", "bid_amount"):
            _require(
                type(result.get(key)) is type(arguments[key]) and result.get(key) == arguments[key],
                f"acceptance {key}",
            )
    return dict(result), dict(value)


def _validate_effect(
    intent: TaskIntent, operation: dict[str, Any], result: dict[str, Any], arguments: dict[str, Any]
) -> None:
    if intent.action in {"accept_bid", "submit_deliverable"}:
        _require(
            operation.get("effect_kind") == "none" and operation.get("effect_state") == "none",
            "pure effect state",
        )
        _require(
            all(
                operation.get(key) is None
                for key in ("effect_operation_id", "effect_result_reference", "effect_intent")
            ),
            "pure effect metadata",
        )
        return
    escrow = {
        "escrow_id": arguments["escrow_id"],
        "task_id": intent.task_id,
        "payer_account_id": arguments["poster_id"],
        "amount": arguments["reward"],
    }
    _require(operation.get("effect_state") == "succeeded", "effect is not confirmed")
    if intent.action == "approve":
        financial = {
            "request_version": 1,
            "operation_kind": "escrow_release",
            "escrow_id": arguments["escrow_id"],
            "recipient_account_id": arguments["worker_id"],
            "source_reference": arguments["source_reference"],
        }
        expected_intent = {
            "financial_request": financial,
            "financial_request_sha256": _hash(financial),
            "expected_escrow": escrow,
        }
        _require(operation.get("effect_kind") == "bank_release", "approval effect kind")
        _require(operation.get("effect_operation_id") == intent.operation_id, "financial identity")
        reference = intent.operation_id
        _require(
            type(result.get("reward")) is int and result["reward"] == arguments["reward"],
            "settled reward",
        )
    else:
        expected_intent = {
            "court_claim": {
                "task_id": intent.task_id,
                "claimant_id": arguments["poster_id"],
                "respondent_id": arguments["worker_id"],
                "reason": arguments["reason"],
            },
            "expected_escrow": escrow,
        }
        _require(
            operation.get("effect_kind") == "court_claim"
            and operation.get("effect_operation_id") is None,
            "Court effect identity",
        )
        reference = required_text(result, "dispute_id")
        _require(result.get("reason") == arguments["reason"], "dispute reason")
    _require(
        canonical_json(operation.get("effect_intent")) == canonical_json(expected_intent),
        "frozen effect intent",
    )
    _require(
        operation.get("effect_result_reference") == reference
        and result.get("effect_result_reference") == reference,
        "confirmed effect reference",
    )
