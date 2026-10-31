"""Build caller-owned native Task Board intents from public snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING

from base_agent.runtime_intent_models import JsonObject, canonical_sha256
from base_agent.runtime_intents import NewTaskIntent, RuntimeIntentError

if TYPE_CHECKING:
    from collections.abc import Mapping

_MAX_INTEGER = 9_223_372_036_854_775_807


def _text(source: Mapping[str, object], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or value == "":
        raise RuntimeIntentError("invalid_input", f"{key} must be a nonempty string")
    return value


def _integer(source: Mapping[str, object], key: str, *, minimum: int) -> int:
    value = source.get(key)
    if type(value) is not int or not minimum <= value <= _MAX_INTEGER:
        raise RuntimeIntentError(
            "invalid_input", f"{key} must be an integer between {minimum} and {_MAX_INTEGER}"
        )
    return value


def _actor(value: object) -> str:
    if not isinstance(value, str) or value == "":
        raise RuntimeIntentError("invalid_input", "actor_id must be a nonempty string")
    return value


def _task_identity(task: Mapping[str, object], expected_status: str) -> tuple[str, int]:
    task_id = _text(task, "task_id")
    status = _text(task, "status")
    if status != expected_status:
        raise RuntimeIntentError("invalid_input", f"task status must be {expected_status}")
    return task_id, _integer(task, "lifecycle_version", minimum=0)


def _mutation_intent(
    *,
    task_id: str,
    slot: str,
    action: str,
    source_status: str,
    target_status: str,
    source_version: int,
    actor_id: str,
    operation_id: str,
    public_arguments: JsonObject,
    frozen_task_facts: JsonObject,
    native_arguments: JsonObject,
    result_facts: JsonObject,
    protocol_version: int,
) -> NewTaskIntent:
    native_request: JsonObject = {
        "protocol_version": protocol_version,
        "task_id": task_id,
        "command": action,
        "expected_status": source_status,
        "expected_lifecycle_version": source_version,
        "actor_kind": "agent",
        "actor_id": actor_id,
        "arguments": native_arguments,
    }
    acknowledgment_context: JsonObject = {
        "response_kind": "task_mutation",
        "operation_id": operation_id,
        "task_id": task_id,
        "command": action,
        "source_status": source_status,
        "source_lifecycle_version": source_version,
        "target_status": target_status,
        "target_lifecycle_version": source_version + 1,
        "result_facts": result_facts,
        "actor_id": actor_id,
        "frozen_task_facts": frozen_task_facts,
        "request_sha256": canonical_sha256(native_request),
    }
    return NewTaskIntent(
        task_id=task_id,
        slot=slot,
        action=action,
        source_status=source_status,
        target_status=target_status,
        operation_id=operation_id,
        expected_lifecycle_version=source_version,
        public_arguments=public_arguments,
        acknowledgment_context=acknowledgment_context,
    )


def bid_intent(
    *, task: Mapping[str, object], actor_id: str, amount: int, operation_id: str
) -> NewTaskIntent:
    """Freeze one bid intent from an exact open-task snapshot."""
    task_id, source_version = _task_identity(task, "open")
    actor_id = _actor(actor_id)
    if type(amount) is not int or not 1 <= amount <= _MAX_INTEGER:
        raise RuntimeIntentError("invalid_input", "amount must be a positive integer")
    return NewTaskIntent(
        task_id=task_id,
        slot="bid",
        action="submit_bid",
        source_status="open",
        target_status="open",
        operation_id=operation_id,
        expected_lifecycle_version=source_version,
        public_arguments={
            "task_id": task_id,
            "amount": amount,
            "operation_id": operation_id,
            "expected_lifecycle_version": source_version,
        },
        acknowledgment_context={
            "response_kind": "bid",
            "operation_id": operation_id,
            "task_id": task_id,
            "command": "submit_bid",
            "source_status": "open",
            "source_lifecycle_version": source_version,
            "target_status": "open",
            "target_lifecycle_version": source_version,
            "result_facts": {
                "bid_id": f"bid-{operation_id[3:]}",
                "bidder_id": actor_id,
                "amount": amount,
            },
            "actor_id": actor_id,
            "frozen_task_facts": {"bidder_id": actor_id},
        },
    )


def acceptance_intent(
    *,
    task: Mapping[str, object],
    actor_id: str,
    bid: Mapping[str, object],
    operation_id: str,
) -> NewTaskIntent:
    """Freeze an acceptance intent and its selected bid terms."""
    task_id, source_version = _task_identity(task, "open")
    actor_id = _actor(actor_id)
    poster_id = _text(task, "poster_id")
    if actor_id != poster_id:
        raise RuntimeIntentError("invalid_input", "actor_id must match task poster_id")
    bid_id = _text(bid, "bid_id")
    worker_id = _text(bid, "bidder_id")
    escrow_id = _text(task, "escrow_id")
    reward = _integer(task, "reward", minimum=1)
    bid_amount = _integer(bid, "amount", minimum=1)
    frozen: JsonObject = {
        "poster_id": poster_id,
        "worker_id": worker_id,
        "escrow_id": escrow_id,
        "accepted_reward": reward,
        "selected_bid_amount": bid_amount,
    }
    return _mutation_intent(
        task_id=task_id,
        slot="acceptance",
        action="accept_bid",
        source_status="open",
        target_status="accepted",
        source_version=source_version,
        actor_id=actor_id,
        operation_id=operation_id,
        public_arguments={
            "task_id": task_id,
            "bid_id": bid_id,
            "operation_id": operation_id,
            "expected_lifecycle_version": source_version,
        },
        frozen_task_facts=frozen,
        native_arguments={
            "bid_id": bid_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "reward": reward,
            "bid_amount": bid_amount,
        },
        result_facts={
            "bid_id": bid_id,
            "worker_id": worker_id,
            "reward": reward,
            "bid_amount": bid_amount,
        },
        protocol_version=1,
    )


def submission_intent(
    *, task: Mapping[str, object], actor_id: str, operation_id: str
) -> NewTaskIntent:
    """Freeze a deliverable-submission intent from accepted task terms."""
    task_id, source_version = _task_identity(task, "accepted")
    actor_id = _actor(actor_id)
    poster_id = _text(task, "poster_id")
    worker_id = _text(task, "worker_id")
    if actor_id != worker_id:
        raise RuntimeIntentError("invalid_input", "actor_id must match task worker_id")
    escrow_id = _text(task, "escrow_id")
    reward = _integer(task, "accepted_reward", minimum=1)
    bid_amount = _integer(task, "accepted_bid_amount", minimum=1)
    binding = _submission_binding(task, task_id)
    native_arguments: JsonObject = {"worker_id": worker_id}
    if binding is not None:
        # Governed task: the submit is protocol 2, bound to the accepted terms the
        # task read reported, so the frozen request hash covers the binding too.
        native_arguments = {"worker_id": worker_id, **binding}
    return _mutation_intent(
        task_id=task_id,
        slot="submission",
        action="submit_deliverable",
        source_status="accepted",
        target_status="submitted",
        source_version=source_version,
        actor_id=actor_id,
        operation_id=operation_id,
        public_arguments={
            "task_id": task_id,
            "operation_id": operation_id,
            "expected_lifecycle_version": source_version,
        },
        frozen_task_facts={
            "poster_id": poster_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "accepted_reward": reward,
            "selected_bid_amount": bid_amount,
            "submission_binding": binding,
        },
        native_arguments=native_arguments,
        result_facts={"worker_id": worker_id},
        protocol_version=1 if binding is None else 2,
    )


def review_intent(
    *,
    task: Mapping[str, object],
    actor_id: str,
    action: str,
    reason: str | None,
    operation_id: str,
) -> NewTaskIntent:
    """Freeze one approval or dispute decision from submitted task terms."""
    task_id, source_version = _task_identity(task, "submitted")
    actor_id = _actor(actor_id)
    poster_id = _text(task, "poster_id")
    if actor_id != poster_id:
        raise RuntimeIntentError("invalid_input", "actor_id must match task poster_id")
    worker_id = _text(task, "worker_id")
    escrow_id = _text(task, "escrow_id")
    reward = _integer(task, "accepted_reward", minimum=1)
    bid_amount = _integer(task, "accepted_bid_amount", minimum=1)
    if action == "approve_task":
        if reason is not None:
            raise RuntimeIntentError("invalid_input", "approval reason must be None")
        target_status = "approved"
        native_arguments: JsonObject = {
            "poster_id": poster_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "reward": reward,
            "source_reference": f"task:{task_id}:approved",
        }
        result_facts: JsonObject = {
            "worker_id": worker_id,
            "reward": reward,
            "effect_result_reference": operation_id,
            "accepted_price": bid_amount,
            "worker_payment": bid_amount,
            "poster_refund": reward - bid_amount,
        }
        public_arguments: JsonObject = {
            "task_id": task_id,
            "operation_id": operation_id,
            "expected_lifecycle_version": source_version,
        }
    elif action == "dispute_task":
        if not isinstance(reason, str) or reason == "":
            raise RuntimeIntentError("invalid_input", "dispute reason must be nonempty")
        target_status = "disputed"
        native_arguments = {
            "poster_id": poster_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "reward": reward,
            "reason": reason,
        }
        result_facts = {"worker_id": worker_id, "reason": reason}
        public_arguments = {
            "task_id": task_id,
            "reason": reason,
            "operation_id": operation_id,
            "expected_lifecycle_version": source_version,
        }
    else:
        raise RuntimeIntentError("invalid_input", "review action is unsupported")
    return _mutation_intent(
        task_id=task_id,
        slot="review-decision",
        action=action,
        source_status="submitted",
        target_status=target_status,
        source_version=source_version,
        actor_id=actor_id,
        operation_id=operation_id,
        public_arguments=public_arguments,
        frozen_task_facts={
            "poster_id": poster_id,
            "worker_id": worker_id,
            "escrow_id": escrow_id,
            "accepted_reward": reward,
            "selected_bid_amount": bid_amount,
        },
        native_arguments=native_arguments,
        result_facts=result_facts,
        protocol_version=1,
    )


def _submission_binding(task: Mapping[str, object], task_id: str) -> JsonObject | None:
    """The governed submission binding from the task read, or None for protocol 1."""
    raw = task.get("submission_binding")
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {
        "manifest_id",
        "accepted_binding_kind",
        "accepted_binding_sha256",
    }:
        raise RuntimeIntentError("invalid_input", "submission_binding is malformed")
    manifest_id = raw["manifest_id"]
    kind = raw["accepted_binding_kind"]
    digest = raw["accepted_binding_sha256"]
    if (
        manifest_id != f"manifest-submission-{task_id}"
        or not isinstance(kind, str)
        or not kind
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise RuntimeIntentError("invalid_input", "submission_binding is invalid")
    return {
        "manifest_id": manifest_id,
        "accepted_binding_kind": kind,
        "accepted_binding_sha256": digest,
    }
