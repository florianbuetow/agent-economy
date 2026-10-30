"""Validate the versioned Gateway task-command wire contract at the client boundary."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from service_commons.exceptions import ServiceError

MAX_INTEGER = (1 << 63) - 1
Integer = Annotated[int, Field(strict=True, ge=0, le=MAX_INTEGER)]
Text = Annotated[str, Field(strict=True, min_length=1)]
OperationId = Annotated[
    str,
    Field(pattern=r"^op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

# These are protocol constants, not configurable application defaults.
COMMANDS: dict[str, tuple[str, str, str, frozenset[str]]] = {
    "submit_bid": ("open", "open", "agent", frozenset({"bid_id", "bidder_id", "amount"})),
    "accept_bid": (
        "open",
        "accepted",
        "agent",
        frozenset({"bid_id", "worker_id", "escrow_id", "reward", "bid_amount"}),
    ),
    "cancel_task": (
        "open",
        "cancelled",
        "agent",
        frozenset({"poster_id", "escrow_id", "reward", "source_reference", "cutoff_us"}),
    ),
    "expire_bidding": (
        "open",
        "expired",
        "system",
        frozenset({"poster_id", "escrow_id", "reward", "source_reference", "cutoff_us"}),
    ),
    "expire_acceptance": (
        "open",
        "expired",
        "system",
        frozenset({"poster_id", "escrow_id", "reward", "source_reference", "cutoff_us"}),
    ),
    "submit_deliverable": ("accepted", "submitted", "agent", frozenset({"worker_id"})),
    "expire_execution": (
        "accepted",
        "expired",
        "system",
        frozenset(
            {"poster_id", "worker_id", "escrow_id", "reward", "source_reference", "cutoff_us"}
        ),
    ),
    "approve_task": (
        "submitted",
        "approved",
        "agent",
        frozenset({"poster_id", "worker_id", "escrow_id", "reward", "source_reference"}),
    ),
    "auto_approve": (
        "submitted",
        "approved",
        "system",
        frozenset(
            {"poster_id", "worker_id", "escrow_id", "reward", "source_reference", "cutoff_us"}
        ),
    ),
    "dispute_task": (
        "submitted",
        "disputed",
        "agent",
        frozenset({"poster_id", "worker_id", "escrow_id", "reward", "reason"}),
    ),
    "record_ruling": (
        "disputed",
        "ruled",
        "platform",
        frozenset(
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
    ),
}
# Protocol 2 exists only for submit_deliverable: it freezes the governed output manifest.
SUBMIT_V2_FIELDS: frozenset[str] = frozenset(
    {"worker_id", "manifest_id", "accepted_binding_kind", "accepted_binding_sha256"}
)
SUBMIT_V2_RESULT_FIELDS: frozenset[str] = frozenset({"worker_id", "manifest_id", "manifest_sha256"})
ACCEPT_BINDING_RESULT_FIELDS: frozenset[str] = frozenset(
    {"accepted_binding_kind", "accepted_binding_sha256"}
)
# G01: final settlements report the accepted price, the worker payment and the poster refund.
SETTLEMENT_RESULT_FIELDS: frozenset[str] = frozenset(
    {"accepted_price", "worker_payment", "poster_refund"}
)


class StrictModel(BaseModel):
    """Wire objects reject coercions and unrecognized fields."""

    model_config = ConfigDict(strict=True, extra="forbid")


class TaskCommand(StrictModel):
    """Complete semantic input, excluding transport signing metadata."""

    protocol_version: Annotated[int, Field(ge=1, le=2)]
    operation_id: OperationId
    task_id: Text
    command: Text
    expected_status: Text
    expected_lifecycle_version: Integer
    actor_kind: Literal["agent", "platform", "system"]
    actor_id: Text
    arguments: dict[str, str | int]


class OperationEnvelope(StrictModel):
    """Original command outcome, separate from the task's mutable read projection."""

    protocol_version: Annotated[int, Field(ge=1, le=2)]
    operation_id: OperationId
    task_id: Text
    command: Text
    request_sha256: Hash
    operation_state: Literal["claimed", "completed"]
    source_status: Text
    source_lifecycle_version: Integer
    target_status: Text
    target_lifecycle_version: Integer
    effect_kind: Literal["none", "bank_release", "bank_split", "court_claim"]
    effect_state: Literal["none", "pending", "succeeded"]
    effect_operation_id: OperationId | None
    effect_result_reference: Text | None
    effect_intent: dict[str, Any] | None
    event_id: Annotated[int, Field(gt=0, le=MAX_INTEGER)] | None
    result: dict[str, Any] | None


class GatewayError(StrictModel):
    """Preserve typed upstream errors, including actual competing operation identities."""

    error: Text
    message: Text
    details: dict[str, Any]


def invalid_gateway_response() -> ServiceError:
    """Do not expose a malformed upstream body or treat it as success."""
    return ServiceError("invalid_gateway_response", "Gateway returned an invalid response", 502, {})


def canonical_hash(value: dict[str, Any]) -> str:
    """Hash semantic JSON with the same version-1 encoding as the producer."""
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Inconsistent task operation contract")


def validate_command(payload: object) -> TaskCommand:
    """Reject invalid caller input before signing or transport."""
    try:
        command = TaskCommand.model_validate(payload)
        source, _target, actor, fields = COMMANDS[command.command]
        if command.protocol_version == 2:
            _require(command.command == "submit_deliverable")
            fields = SUBMIT_V2_FIELDS
        _require(command.expected_status == source and command.actor_kind == actor)
        _require(frozenset(command.arguments) == fields)
        ranges = {
            "reward": (1, MAX_INTEGER),
            "amount": (1, MAX_INTEGER),
            "bid_amount": (0, MAX_INTEGER),
            "cutoff_us": (0, MAX_INTEGER),
            "worker_pct": (0, 100),
        }
        for name, value in command.arguments.items():
            if name in ranges:
                low, high = ranges[name]
                _require(type(value) is int and low <= value <= high)
            else:
                _require(isinstance(value, str) and bool(value))
        canonical_hash(command.model_dump(exclude={"operation_id"}))
    except (ValidationError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ServiceError("invalid_payload", "Invalid task operation command", 400, {}) from exc
    return command


def validate_operation_id(operation_id: object) -> str:
    """A finalization identifier must be a canonical native operation identifier."""
    # Reuse the exact same field validator as the command model.
    try:
        return TypeAdapter(OperationId).validate_python(operation_id, strict=True)
    except ValidationError as exc:
        raise ServiceError(
            "invalid_operation_id", "Invalid task operation identifier", 400, {}
        ) from exc


def _validate_intent(envelope: OperationEnvelope) -> None:
    intent = envelope.effect_intent
    if envelope.effect_kind == "none":
        _require(intent is None and envelope.effect_operation_id is None)
        _require(envelope.effect_state == "none" and envelope.effect_result_reference is None)
        return
    _require(isinstance(intent, dict))
    if intent is None:
        raise ValueError("Missing effect intent")
    escrow = intent.get("expected_escrow")
    _require(
        isinstance(escrow, dict)
        and set(escrow) == {"escrow_id", "task_id", "payer_account_id", "amount"}
    )
    if not isinstance(escrow, dict):
        raise ValueError("Missing escrow predicate")
    _require(escrow["task_id"] == envelope.task_id)
    _require(type(escrow["amount"]) is int and 0 < escrow["amount"] <= MAX_INTEGER)
    _require(
        all(
            isinstance(escrow[key], str) and escrow[key]
            for key in ("escrow_id", "task_id", "payer_account_id")
        )
    )
    if envelope.effect_kind == "court_claim":
        _require(envelope.command == "dispute_task" and envelope.effect_operation_id is None)
        _require(set(intent) == {"court_claim", "expected_escrow"})
        claim = intent["court_claim"]
        _require(
            isinstance(claim, dict)
            and set(claim) == {"task_id", "claimant_id", "respondent_id", "reason"}
        )
        _require(
            claim["task_id"] == envelope.task_id
            and claim["claimant_id"] == escrow["payer_account_id"]
        )
        _require(all(isinstance(value, str) and value for value in claim.values()))
        return
    _require(envelope.effect_operation_id == envelope.operation_id)
    _require(set(intent) == {"financial_request", "financial_request_sha256", "expected_escrow"})
    financial = intent["financial_request"]
    _require(isinstance(financial, dict))
    _require(canonical_hash(financial) == intent["financial_request_sha256"])
    _require(financial["escrow_id"] == escrow["escrow_id"])
    _require(type(financial["request_version"]) is int and financial["request_version"] == 1)
    fields = {"request_version", "operation_kind", "escrow_id", "source_reference"}
    if envelope.effect_kind == "bank_release":
        _require(financial["operation_kind"] == "escrow_release")
        fields.add("recipient_account_id")
    else:
        _require(
            financial["operation_kind"] == "escrow_split"
            and envelope.command in {"approve_task", "auto_approve", "record_ruling"}
        )
        if "worker_amount" in financial:
            # G01: the Gateway prices the settlement from the accepted bid.
            fields.update({"worker_account_id", "poster_account_id", "worker_amount"})
            _require(
                type(financial["worker_amount"]) is int
                and 0 < financial["worker_amount"] < escrow["amount"]
            )
        else:
            _require(envelope.command == "record_ruling")
            fields.update({"worker_account_id", "poster_account_id", "worker_pct"})
            _require(type(financial["worker_pct"]) is int and 0 < financial["worker_pct"] < 100)
    _require(set(financial) == fields)
    _require(
        all(
            isinstance(value, str) and value
            for key, value in financial.items()
            if key not in {"request_version", "worker_pct", "worker_amount"}
        )
    )


def _validate_result(envelope: OperationEnvelope, status_code: int) -> None:
    if envelope.operation_state == "claimed":
        _require(status_code == 202 and envelope.effect_kind != "none")
        _require(envelope.effect_state == "pending" and envelope.effect_result_reference is None)
        _require(envelope.event_id is None and envelope.result is None)
        return
    _require(status_code == 200 and envelope.event_id is not None)
    result = envelope.result
    _require(isinstance(result, dict))
    if result is None:
        raise ValueError("Missing completed result")
    expected = {
        "task_id": envelope.task_id,
        "command": envelope.command,
        "status": envelope.target_status,
        "lifecycle_version": envelope.target_lifecycle_version,
        "event_id": envelope.event_id,
    }
    _require(
        all(
            type(result.get(key)) is type(value) and result.get(key) == value
            for key, value in expected.items()
        )
    )
    if envelope.effect_kind != "none":
        _require(
            envelope.effect_state == "succeeded" and envelope.effect_result_reference is not None
        )
        _require(result.get("effect_result_reference") == envelope.effect_result_reference)
        if envelope.effect_kind != "court_claim":
            _require(envelope.effect_result_reference == envelope.operation_id)


def _bind_intent_to_command(envelope: OperationEnvelope, command: TaskCommand) -> None:
    """A valid self-hash cannot authorize a different effect than the submitted command."""
    if envelope.effect_kind == "none":
        return
    args = command.arguments
    escrow = {
        "escrow_id": args["escrow_id"],
        "task_id": command.task_id,
        "payer_account_id": args["poster_id"],
        "amount": args["reward"],
    }
    if command.command == "dispute_task":
        expected: dict[str, Any] = {
            "expected_escrow": escrow,
            "court_claim": {
                "task_id": command.task_id,
                "claimant_id": args["poster_id"],
                "respondent_id": args["worker_id"],
                "reason": args["reason"],
            },
        }
    else:
        financial: dict[str, Any] = {
            "request_version": 1,
            "escrow_id": args["escrow_id"],
            "source_reference": args["source_reference"],
        }
        settles = command.command in {"approve_task", "auto_approve", "record_ruling"}
        stored = envelope.effect_intent
        stored_financial = stored.get("financial_request") if isinstance(stored, dict) else None
        if envelope.effect_kind == "bank_split":
            # The Gateway prices the two-sided settlement from the frozen accepted bid.
            _require(settles and isinstance(stored_financial, dict))
            assert isinstance(stored_financial, dict)
            share = "worker_amount" if "worker_amount" in stored_financial else "worker_pct"
            _require(share == "worker_amount" or stored_financial[share] == args["worker_pct"])
            financial.update(
                {
                    "operation_kind": "escrow_split",
                    "worker_account_id": args["worker_id"],
                    "poster_account_id": args["poster_id"],
                    share: stored_financial[share],
                }
            )
        else:
            _require(envelope.effect_kind == "bank_release")
            pays_worker = command.command in {"approve_task", "auto_approve"} or (
                command.command == "record_ruling"
                and args["worker_pct"] != 0
                and isinstance(stored_financial, dict)
                and stored_financial.get("recipient_account_id") == args["worker_id"]
            )
            financial.update(
                {
                    "operation_kind": "escrow_release",
                    "recipient_account_id": args["worker_id"] if pays_worker else args["poster_id"],
                }
            )
        expected = {
            "expected_escrow": escrow,
            "financial_request": financial,
            "financial_request_sha256": canonical_hash(financial),
        }
    _require(envelope.effect_intent == expected)


def _validate_result_facts(envelope: OperationEnvelope, command: TaskCommand | None) -> None:
    result = envelope.result
    if result is None:
        return
    fields = {"task_id", "command", "status", "lifecycle_version", "event_id"}
    command_fields = {
        "submit_bid": {"bid_id", "bidder_id", "amount"},
        "accept_bid": {"bid_id", "worker_id", "reward", "bid_amount"},
        "submit_deliverable": {"worker_id"},
        "cancel_task": {"reward"},
        "expire_bidding": {"reward"},
        "expire_acceptance": {"reward"},
        "expire_execution": {"reward", "worker_id"},
        "approve_task": {"reward", "worker_id"},
        "auto_approve": {"reward", "worker_id"},
        "dispute_task": {"dispute_id", "reason", "worker_id"},
        "record_ruling": {"reward", "worker_id", "ruling_id", "worker_pct", "summary"},
    }[envelope.command]
    if envelope.protocol_version == 2:
        _require(envelope.command == "submit_deliverable")
        command_fields = set(SUBMIT_V2_RESULT_FIELDS)
    # Governed (V6) acceptance reports the artifact binding later uploads/submit carry.
    if envelope.command == "accept_bid" and set(ACCEPT_BINDING_RESULT_FIELDS) <= set(result):
        command_fields = command_fields | set(ACCEPT_BINDING_RESULT_FIELDS)
    # G01 accepted-bid pricing facts; results committed before G01 carry none of them.
    priced_fields = {
        "accept_bid": {"accepted_price"},
        "approve_task": set(SETTLEMENT_RESULT_FIELDS),
        "auto_approve": set(SETTLEMENT_RESULT_FIELDS),
        "record_ruling": set(SETTLEMENT_RESULT_FIELDS),
    }.get(envelope.command, set())
    if priced_fields and priced_fields <= set(result):
        command_fields = command_fields | priced_fields
    fields.update(command_fields)
    if envelope.effect_kind != "none":
        fields.add("effect_result_reference")
    _require(set(result) == fields)
    for field in command_fields:
        value = result[field]
        if field in {"amount", "reward", "bid_amount", "worker_pct", *SETTLEMENT_RESULT_FIELDS}:
            low = 1 if field in {"amount", "reward"} else 0
            high = 100 if field == "worker_pct" else MAX_INTEGER
            _require(type(value) is int and low <= value <= high)
        else:
            _require(isinstance(value, str) and bool(value))
        if command is not None and field in command.arguments:
            _require(value == command.arguments[field])
    if "accepted_price" in command_fields and envelope.command == "accept_bid":
        _require(result["accepted_price"] == result["bid_amount"])
    intent = envelope.effect_intent
    if intent is None:
        return
    if envelope.effect_kind == "court_claim":
        claim = intent["court_claim"]
        _require(result["dispute_id"] == envelope.effect_result_reference)
        _require(
            result["reason"] == claim["reason"] and result["worker_id"] == claim["respondent_id"]
        )
        return
    _require(result["reward"] == intent["expected_escrow"]["amount"])
    financial = intent["financial_request"]
    if envelope.command in {"approve_task", "auto_approve", "record_ruling"}:
        _validate_settlement_facts(envelope, result, intent)
    if envelope.command == "record_ruling":
        _require(
            financial["source_reference"] == f"task:{envelope.task_id}:ruling:{result['ruling_id']}"
        )
        if envelope.effect_kind == "bank_split" and "worker_pct" in financial:
            _require(result["worker_pct"] == financial["worker_pct"])


def _validate_settlement_facts(
    envelope: OperationEnvelope,
    result: dict[str, Any],
    intent: dict[str, Any],
) -> None:
    """Bind the final payment and refund to the exact stored Bank intent."""
    financial = intent["financial_request"]
    escrow_amount = intent["expected_escrow"]["amount"]
    poster_id = intent["expected_escrow"]["payer_account_id"]
    if envelope.effect_kind == "bank_split":
        _require(result["worker_id"] == financial["worker_account_id"])
        worker_payment = financial.get("worker_amount")
    else:
        recipient = financial["recipient_account_id"]
        _require(recipient in {result["worker_id"], poster_id})
        worker_payment = escrow_amount if recipient == result["worker_id"] else 0
    if envelope.command in {"approve_task", "auto_approve"}:
        _require(worker_payment != 0)
    if "worker_payment" not in result:
        return
    _require(result["worker_payment"] + result["poster_refund"] == escrow_amount)
    _require(0 < result["accepted_price"] <= escrow_amount)
    if worker_payment is not None:
        _require(result["worker_payment"] == worker_payment)
    else:
        _require(result["worker_payment"] == result["accepted_price"] * result["worker_pct"] // 100)


def validate_envelope(
    data: object,
    *,
    status_code: int,
    operation_id: str,
    command: TaskCommand | None,
) -> dict[str, Any]:
    """Reject mismatched identity, protocol, state or immutable result facts."""
    try:
        envelope = OperationEnvelope.model_validate(data)
        _require(envelope.operation_id == operation_id)
        if command is not None:
            _require(envelope.protocol_version == command.protocol_version)
        source, target, _actor, _fields = COMMANDS[envelope.command]
        _require((envelope.source_status, envelope.target_status) == (source, target))
        increment = 0 if envelope.command == "submit_bid" else 1
        _require(envelope.target_lifecycle_version == envelope.source_lifecycle_version + increment)
        pure = envelope.command in {"submit_bid", "accept_bid", "submit_deliverable"}
        _require((envelope.effect_kind == "none") == pure)
        _require((envelope.effect_kind == "court_claim") == (envelope.command == "dispute_task"))
        if command is not None:
            _require(
                (
                    envelope.task_id,
                    envelope.command,
                    envelope.source_status,
                    envelope.source_lifecycle_version,
                )
                == (
                    command.task_id,
                    command.command,
                    command.expected_status,
                    command.expected_lifecycle_version,
                )
            )
            _require(
                envelope.request_sha256
                == canonical_hash(command.model_dump(exclude={"operation_id"}))
            )
        _validate_intent(envelope)
        _validate_result(envelope, status_code)
        if command is not None:
            _bind_intent_to_command(envelope, command)
        _validate_result_facts(envelope, command)
    except (ValidationError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise invalid_gateway_response() from exc
    return envelope.model_dump()
