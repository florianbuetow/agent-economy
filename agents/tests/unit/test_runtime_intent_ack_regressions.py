"""Focused regressions for durable runtime-intent acknowledgment admission."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

import pytest

from base_agent.runtime_intents import NewTaskIntent, RuntimeIntentError
from tests.unit.test_runtime_intents import (
    _OPERATION_ID,
    _TASK_ID,
    _action_case,
    _canonical_hash,
    _intent,
    _owner_for_action,
    _store,
)

pytestmark = pytest.mark.unit

if TYPE_CHECKING:
    from pathlib import Path

_SQLITE_MAX = (1 << 63) - 1


def _prepared(action: str) -> tuple[NewTaskIntent, dict[str, object]]:
    descriptor, _arguments, acknowledgment = _action_case(action)
    return NewTaskIntent(**descriptor), acknowledgment


def test_dispute_intent_does_not_require_future_court_generated_identity(tmp_path: Path) -> None:
    descriptor, _arguments, acknowledgment = _action_case("dispute_task")
    context = descriptor["acknowledgment_context"]
    assert isinstance(context, dict)
    expected = context["result_facts"]
    assert isinstance(expected, dict)
    assert "dispute_id" not in expected
    assert "effect_result_reference" not in expected

    with _store(tmp_path, owner=_owner_for_action("dispute_task")) as store:
        pending = store.create(NewTaskIntent(**descriptor))
        completed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="review-decision",
            expected_revision=pending.revision,
            status_code=200,
            body=acknowledgment,
        )

    operation = acknowledgment["operation"]
    assert isinstance(operation, dict)
    result = operation["result"]
    assert isinstance(result, dict)
    assert completed.state == "completed"
    assert result["dispute_id"] == operation["effect_result_reference"]
    assert result["effect_result_reference"] == operation["effect_result_reference"]


def test_bool_financial_request_version_with_integer_hash_cannot_complete(tmp_path: Path) -> None:
    intent, base = _prepared("approve_task")
    body = copy.deepcopy(base)
    operation = body["operation"]
    assert isinstance(operation, dict)
    effect = operation["effect_intent"]
    assert isinstance(effect, dict)
    financial = effect["financial_request"]
    assert isinstance(financial, dict)
    integer_hash = effect["financial_request_sha256"]
    financial["request_version"] = True
    assert effect["financial_request_sha256"] == integer_hash
    assert _canonical_hash(financial) != integer_hash

    with _store(tmp_path, owner=_owner_for_action("approve_task")) as store:
        pending = store.create(intent)
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="review-decision",
            expected_revision=pending.revision,
            status_code=200,
            body=body,
        )
    assert observed.state == "attention_required"
    assert observed.result is None


def test_definitive_rejection_persists_normalized_evidence_and_reopens(tmp_path: Path) -> None:
    error = {
        "error": "task_version_conflict",
        "message": "Task lifecycle version differs from the command precondition",
        "details": {
            "expected_lifecycle_version": 7,
            "actual_lifecycle_version": 8,
            "current_status": "open",
        },
    }
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        rejected = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=409,
            body=error,
        )
    assert rejected.result == {"status_code": 409, "error": error}
    with _store(tmp_path) as reopened:
        assert reopened.load(_TASK_ID, "bid") == rejected


def test_fabricated_definitive_state_without_rejection_evidence_fails_closed(
    tmp_path: Path,
) -> None:
    with _store(tmp_path) as store:
        store.create(_intent())
        path = store.intent_path(_TASK_ID, "bid")
    payload = json.loads(path.read_text())
    payload.update(
        state="definitive_rejection",
        revision=2,
        result=None,
        diagnostics=[{"kind": "definitive_nonapplication", "status_code": 409}],
    )
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    path.chmod(0o600)
    with pytest.raises(RuntimeIntentError) as raised, _store(tmp_path) as reopened:
        reopened.load(_TASK_ID, "bid")
    assert raised.value.reason == "corrupt_record"


@pytest.mark.parametrize("event_id", [0, -1, _SQLITE_MAX + 1])
def test_invalid_event_id_in_envelope_and_result_cannot_complete(
    tmp_path: Path,
    event_id: int,
) -> None:
    intent, base = _prepared("accept_bid")
    body = copy.deepcopy(base)
    operation = body["operation"]
    assert isinstance(operation, dict)
    result = operation["result"]
    assert isinstance(result, dict)
    operation["event_id"] = event_id
    result["event_id"] = event_id
    with _store(tmp_path, owner=_owner_for_action("accept_bid")) as store:
        pending = store.create(intent)
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="acceptance",
            expected_revision=pending.revision,
            status_code=200,
            body=body,
        )
    assert observed.state == "attention_required"


@pytest.mark.parametrize("actual", [7, -1, _SQLITE_MAX + 1])
def test_impossible_version_conflict_cannot_be_definitive(
    tmp_path: Path,
    actual: int,
) -> None:
    body = {
        "error": "task_version_conflict",
        "message": "Task lifecycle version differs from the command precondition",
        "details": {
            "expected_lifecycle_version": 7,
            "actual_lifecycle_version": actual,
            "current_status": "open",
        },
    }
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=409,
            body=body,
        )
    assert observed.state == "pending"
    assert observed.result is None


@pytest.mark.parametrize(
    "body",
    [
        {"error": "task_version_conflict", "message": "conflict", "details": {}},
        {
            "error": "task_version_conflict",
            "message": "conflict",
            "details": {
                "expected_lifecycle_version": 7,
                "actual_lifecycle_version": 8,
                "current_status": "open",
                "extra": True,
            },
        },
    ],
)
def test_malformed_version_conflict_shape_remains_pending(
    tmp_path: Path,
    body: dict[str, object],
) -> None:
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=409,
            body=body,
        )
    assert observed.state == "pending"


def test_same_operation_transition_pending_is_not_foreign_owner(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=409,
            body={
                "error": "transition_pending",
                "message": "Another task transition already owns this task",
                "details": {"winning_operation_id": _OPERATION_ID},
            },
        )
    assert observed.state == "pending"
    assert observed.diagnostics[-1]["kind"] == "remote_pending"


@pytest.mark.parametrize(
    "mutation",
    ["version", "amount", "reward", "selected_bid_amount"],
)
def test_sqlite_integer_bounds_are_enforced_before_store(
    tmp_path: Path,
    mutation: str,
) -> None:
    with pytest.raises(RuntimeIntentError) as raised:
        if mutation == "version":
            intent = _intent(expected_lifecycle_version=_SQLITE_MAX + 1)
        elif mutation == "amount":
            intent = _intent(
                public_arguments={
                    "task_id": _TASK_ID,
                    "amount": _SQLITE_MAX + 1,
                    "operation_id": _OPERATION_ID,
                    "expected_lifecycle_version": 7,
                },
                acknowledgment_context={
                    **dict(_intent().acknowledgment_context),
                    "result_facts": {
                        "bid_id": f"bid-{_OPERATION_ID[3:]}",
                        "bidder_id": "a-worker-alice",
                        "amount": _SQLITE_MAX + 1,
                    },
                },
            )
        else:
            descriptor, _arguments, _ack = _action_case("approve_task")
            context = descriptor["acknowledgment_context"]
            assert isinstance(context, dict)
            frozen = context["frozen_task_facts"]
            assert isinstance(frozen, dict)
            field = "accepted_reward" if mutation == "reward" else "selected_bid_amount"
            frozen[field] = _SQLITE_MAX + 1
            intent = NewTaskIntent(**descriptor)
        with _store(tmp_path, owner=_owner_for_action(intent.action)) as store:
            store.create(intent)
    assert raised.value.reason == "invalid_input"


@pytest.mark.parametrize("field", ["amount", "accepted_reward", "selected_bid_amount"])
def test_bool_is_rejected_for_native_numeric_fact(tmp_path: Path, field: str) -> None:
    with pytest.raises(RuntimeIntentError) as raised:
        if field == "amount":
            base = _intent()
            context = dict(base.acknowledgment_context)
            context["result_facts"] = {
                "bid_id": f"bid-{_OPERATION_ID[3:]}",
                "bidder_id": "a-worker-alice",
                "amount": True,
            }
            intent = _intent(
                public_arguments={
                    "task_id": _TASK_ID,
                    "amount": True,
                    "operation_id": _OPERATION_ID,
                    "expected_lifecycle_version": 7,
                },
                acknowledgment_context=context,
            )
        else:
            descriptor, _arguments, _ack = _action_case("approve_task")
            context = descriptor["acknowledgment_context"]
            assert isinstance(context, dict)
            frozen = context["frozen_task_facts"]
            assert isinstance(frozen, dict)
            frozen[field] = True
            intent = NewTaskIntent(**descriptor)
        with _store(tmp_path, owner=_owner_for_action(intent.action)) as store:
            store.create(intent)
    assert raised.value.reason == "invalid_input"


@pytest.mark.parametrize(
    ("action", "error"),
    [
        (action, error)
        for action in (
            "submit_bid",
            "accept_bid",
            "submit_deliverable",
            "approve_task",
            "dispute_task",
        )
        for error in ("invalid_status", "cutoff_reached")
    ],
)
def test_exact_documented_preclaim_error_is_definitive(
    tmp_path: Path,
    action: str,
    error: str,
) -> None:
    descriptor, _arguments, _ack = _action_case(action)
    intent = NewTaskIntent(**descriptor)
    details = (
        {
            "expected_status": intent.source_status,
            "current_status": "expired" if action == "submit_bid" else intent.target_status,
        }
        if error == "invalid_status"
        else {}
    )
    body = {
        "error": error,
        "message": (
            "Task status differs from the command precondition"
            if error == "invalid_status"
            else "Task operation cutoff has been reached"
        ),
        "details": details,
    }
    with _store(tmp_path, owner=_owner_for_action(action)) as store:
        pending = store.create(intent)
        rejected = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot=intent.slot,
            expected_revision=pending.revision,
            status_code=409,
            body=body,
        )
    assert rejected.state == "definitive_rejection"
    assert rejected.result == {"status_code": 409, "error": body}


@pytest.mark.parametrize(
    ("action", "current_status", "expected_state"),
    [
        ("submit_bid", "expired", "definitive_rejection"),
        ("approve_task", "disputed", "definitive_rejection"),
        ("accept_bid", "cancelled", "definitive_rejection"),
        ("submit_bid", "open", "pending"),
        ("submit_bid", "unknown-status", "pending"),
    ],
)
def test_invalid_status_uses_actual_source_conflict_not_requested_target(
    tmp_path: Path, action: str, current_status: str, expected_state: str
) -> None:
    descriptor, _arguments, _ack = _action_case(action)
    intent = NewTaskIntent(**descriptor)
    with _store(tmp_path, owner=_owner_for_action(action)) as store:
        pending = store.create(intent)
        observed = store.record_acknowledgment(
            task_id=intent.task_id,
            slot=intent.slot,
            expected_revision=pending.revision,
            status_code=409,
            body={
                "error": "invalid_status",
                "message": "Task status differs",
                "details": {
                    "expected_status": intent.source_status,
                    "current_status": current_status,
                },
            },
        )
    assert observed.state == expected_state


def test_boolean_expected_version_cannot_prove_definitive_rejection(tmp_path: Path) -> None:
    original = _intent()
    intent = _intent(
        expected_lifecycle_version=1,
        public_arguments={**original.public_arguments, "expected_lifecycle_version": 1},
        acknowledgment_context={
            **original.acknowledgment_context,
            "source_lifecycle_version": 1,
            "target_lifecycle_version": 1,
        },
    )
    with _store(tmp_path) as store:
        pending = store.create(intent)
        observed = store.record_acknowledgment(
            task_id=intent.task_id,
            slot=intent.slot,
            expected_revision=pending.revision,
            status_code=409,
            body={
                "error": "task_version_conflict",
                "message": "Task lifecycle version differs",
                "details": {
                    "expected_lifecycle_version": True,
                    "actual_lifecycle_version": 2,
                    "current_status": "accepted",
                },
            },
        )
    assert observed.state == "pending"
    assert observed.result is None


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (502, {"error": "task_board_unavailable", "message": "Unavailable", "details": {}}),
        (409, {"error": "invalid_status", "message": "wrong", "details": {}}),
        (
            409,
            {
                "error": "cutoff_reached",
                "message": "cutoff",
                "details": {"extra": True},
            },
        ),
    ],
)
def test_5xx_or_malformed_preclaim_error_remains_pending(
    tmp_path: Path,
    status_code: int,
    body: dict[str, object],
) -> None:
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=status_code,
            body=body,
        )
    assert observed.state == "pending"
    assert observed.result is None
