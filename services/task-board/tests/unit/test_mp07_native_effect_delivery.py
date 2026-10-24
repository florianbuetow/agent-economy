"""MP-07 native Bank-effect delivery resumes persisted Gateway intent."""

from __future__ import annotations

import copy
import importlib
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.task_operation_contract import canonical_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_OPERATION_ID = "op-11111111-1111-4111-8111-111111111111"
_TASK_ID = "t-native-effect"
_ESCROW_ID = "esc-native-effect"
_POSTER_ID = "a-poster"
_WORKER_ID = "a-worker"
_SOURCE_REFERENCE = f"task:{_TASK_ID}:approved"


class _StoreBoundary(Protocol):
    def finalize_task_operation(self, operation_id: str) -> dict[str, Any]: ...


class _EscrowBoundary(Protocol):
    async def release_escrow(self, *args: object) -> object: ...

    async def split_escrow(self, *args: object) -> object: ...


class _CompleteBankOperation(Protocol):
    async def __call__(
        self,
        store: _StoreBoundary,
        escrow_coordinator: _EscrowBoundary,
        envelope: dict[str, Any],
    ) -> dict[str, Any]: ...


def _entry_point() -> _CompleteBankOperation:
    try:
        module = importlib.import_module("task_board_service.services.task_operations")
    except ModuleNotFoundError:
        pytest.fail("Missing native complete_bank_operation capability")
    value = getattr(module, "complete_bank_operation", None)
    if not callable(value):
        pytest.fail("Missing callable complete_bank_operation capability")
    return cast("_CompleteBankOperation", value)


def _release_financial() -> dict[str, object]:
    return {
        "request_version": 1,
        "operation_kind": "escrow_release",
        "escrow_id": _ESCROW_ID,
        "recipient_account_id": _WORKER_ID,
        "source_reference": _SOURCE_REFERENCE,
    }


def _split_financial() -> dict[str, object]:
    return {
        "request_version": 1,
        "operation_kind": "escrow_split",
        "escrow_id": _ESCROW_ID,
        "worker_account_id": _WORKER_ID,
        "poster_account_id": _POSTER_ID,
        "worker_pct": 50,
        "source_reference": f"task:{_TASK_ID}:ruling:clm-1",
    }


def _pending_envelope(*, split: bool = False) -> dict[str, Any]:
    financial = _split_financial() if split else _release_financial()
    return {
        "protocol_version": 1,
        "operation_id": _OPERATION_ID,
        "task_id": _TASK_ID,
        "command": "record_ruling" if split else "approve_task",
        "request_sha256": "0" * 64,
        "operation_state": "claimed",
        "source_status": "disputed" if split else "submitted",
        "source_lifecycle_version": 3,
        "target_status": "ruled" if split else "approved",
        "target_lifecycle_version": 4,
        "effect_kind": "bank_split" if split else "bank_release",
        "effect_state": "pending",
        "effect_operation_id": _OPERATION_ID,
        "effect_result_reference": None,
        "effect_intent": {
            "financial_request": financial,
            "financial_request_sha256": canonical_hash(financial),
            "expected_escrow": {
                "escrow_id": _ESCROW_ID,
                "task_id": _TASK_ID,
                "payer_account_id": _POSTER_ID,
                "amount": 100,
            },
        },
        "event_id": None,
        "result": None,
    }


def _completed_envelope(*, split: bool = False) -> dict[str, Any]:
    pending = _pending_envelope(split=split)
    pending.update(
        {
            "operation_state": "completed",
            "effect_state": "succeeded",
            "effect_result_reference": _OPERATION_ID,
            "event_id": 7,
            "result": {
                "task_id": _TASK_ID,
                "command": pending["command"],
                "status": pending["target_status"],
                "lifecycle_version": 4,
                "event_id": 7,
                "effect_result_reference": _OPERATION_ID,
                "reward": 100,
                "worker_id": _WORKER_ID,
                **({"ruling_id": "clm-1", "worker_pct": 50, "summary": "split"} if split else {}),
            },
        }
    )
    return pending


def _changed_completed(mutation: str) -> dict[str, Any]:
    completed = copy.deepcopy(_completed_envelope())
    result = completed["result"]
    intent = completed["effect_intent"]
    assert isinstance(result, dict) and isinstance(intent, dict)
    if mutation == "task":
        completed["task_id"] = "t-different"
        result["task_id"] = "t-different"
        expected_escrow = intent["expected_escrow"]
        assert isinstance(expected_escrow, dict)
        expected_escrow["task_id"] = "t-different"
    elif mutation == "command":
        completed["command"] = "auto_approve"
        result["command"] = "auto_approve"
    elif mutation == "hash":
        completed["request_sha256"] = "f" * 64
    elif mutation == "versions":
        completed["source_lifecycle_version"] = 4
        completed["target_lifecycle_version"] = 5
        result["lifecycle_version"] = 5
    elif mutation == "intent":
        financial = intent["financial_request"]
        assert isinstance(financial, dict)
        financial["recipient_account_id"] = _POSTER_ID
        intent["financial_request_sha256"] = canonical_hash(financial)
    else:
        raise AssertionError(f"Unknown mutation: {mutation}")
    return completed


@dataclass
class _Store:
    outcomes: list[dict[str, Any] | ServiceError]
    finalize_calls: list[str] = field(default_factory=list)
    update_calls: list[tuple[object, ...]] = field(default_factory=list)

    def finalize_task_operation(self, operation_id: str) -> dict[str, Any]:
        self.finalize_calls.append(operation_id)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, ServiceError):
            raise outcome
        return outcome

    def update_task(self, *args: object, **_kwargs: object) -> int:
        self.update_calls.append(args)
        return 1


@dataclass
class _Escrow:
    release_outcomes: list[object] = field(default_factory=list)
    split_outcomes: list[object] = field(default_factory=list)
    release_calls: list[tuple[object, ...]] = field(default_factory=list)
    split_calls: list[tuple[object, ...]] = field(default_factory=list)

    async def release_escrow(self, *args: object) -> object:
        self.release_calls.append(args)
        outcome = self.release_outcomes.pop(0) if self.release_outcomes else None
        if isinstance(outcome, ServiceError):
            raise outcome
        return outcome

    async def split_escrow(self, *args: object) -> object:
        self.split_calls.append(args)
        outcome = self.split_outcomes.pop(0) if self.split_outcomes else None
        if isinstance(outcome, ServiceError):
            raise outcome
        return outcome


async def test_completed_input_returns_without_bank_or_finalizer() -> None:
    complete = _entry_point()
    completed = _completed_envelope()
    store = _Store([])
    escrow = _Escrow()

    result = await complete(store, escrow, completed)

    assert result is completed
    assert store.finalize_calls == []
    assert escrow.release_calls == []
    assert escrow.split_calls == []


async def test_existing_gateway_evidence_completes_before_bank_delivery() -> None:
    complete = _entry_point()
    pending = _pending_envelope()
    completed = _completed_envelope()
    store = _Store([completed])
    escrow = _Escrow()

    result = await complete(store, escrow, pending)

    assert result is completed
    assert store.finalize_calls == [_OPERATION_ID]
    assert escrow.release_calls == []


@pytest.mark.parametrize("mutation", ["task", "command", "hash", "versions", "intent"])
async def test_finalize_cannot_change_immutable_claim_context(mutation: str) -> None:
    complete = _entry_point()
    pending = _pending_envelope()
    changed = _changed_completed(mutation)
    store = _Store([changed])
    escrow = _Escrow()

    with pytest.raises(ServiceError) as raised:
        await complete(store, escrow, pending)

    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_gateway_response"
    assert store.finalize_calls == [_OPERATION_ID]
    assert escrow.release_calls == []
    assert escrow.split_calls == []
    assert store.update_calls == []


async def test_pending_release_uses_exact_persisted_financial_identity() -> None:
    complete = _entry_point()
    pending = _pending_envelope()
    store = _Store([pending, pending])
    escrow = _Escrow([{"untrusted_success_body": True}])

    result = await complete(store, escrow, pending)

    assert result is pending
    assert escrow.release_calls == [(_ESCROW_ID, _WORKER_ID, _OPERATION_ID, _SOURCE_REFERENCE)]
    assert store.finalize_calls == [_OPERATION_ID, _OPERATION_ID]
    assert store.update_calls == []


async def test_pending_split_uses_exact_persisted_financial_identity() -> None:
    complete = _entry_point()
    pending = _pending_envelope(split=True)
    completed = _completed_envelope(split=True)
    store = _Store([pending, completed])
    escrow = _Escrow(split_outcomes=[None])

    result = await complete(store, escrow, pending)

    assert result is completed
    assert escrow.split_calls == [
        (
            _ESCROW_ID,
            _WORKER_ID,
            _POSTER_ID,
            50,
            _OPERATION_ID,
            f"task:{_TASK_ID}:ruling:clm-1",
        )
    ]
    assert store.update_calls == []


async def test_bank_exception_after_effect_converges_from_gateway_evidence() -> None:
    complete = _entry_point()
    pending = _pending_envelope()
    completed = _completed_envelope()
    bank_error = ServiceError("central_bank_unavailable", "response lost", 502, {})
    store = _Store([pending, completed])
    escrow = _Escrow([bank_error])

    result = await complete(store, escrow, pending)

    assert result is completed
    assert store.finalize_calls == [_OPERATION_ID, _OPERATION_ID]
    assert len(escrow.release_calls) == 1


async def test_bank_error_with_still_pending_evidence_reraises_original() -> None:
    complete = _entry_point()
    pending = _pending_envelope()
    bank_error = ServiceError(
        "central_bank_unavailable", "still unavailable", 502, {"marker": "original"}
    )
    store = _Store([pending, pending])
    escrow = _Escrow([bank_error])

    with pytest.raises(ServiceError) as raised:
        await complete(store, escrow, pending)

    assert raised.value is bank_error
    assert store.finalize_calls == [_OPERATION_ID, _OPERATION_ID]
    assert len(escrow.release_calls) == 1


async def test_finalizer_conflict_after_bank_propagates_without_bank_retry() -> None:
    complete = _entry_point()
    pending = _pending_envelope()
    conflict = ServiceError(
        "effect_evidence_conflict",
        "persisted evidence conflicts",
        409,
        {"operation_id": _OPERATION_ID},
    )
    store = _Store([pending, conflict])
    escrow = _Escrow([None])

    with pytest.raises(ServiceError) as raised:
        await complete(store, escrow, pending)

    assert raised.value is conflict
    assert store.finalize_calls == [_OPERATION_ID, _OPERATION_ID]
    assert len(escrow.release_calls) == 1


async def test_repeated_pending_calls_reuse_identity_without_legacy_updates() -> None:
    complete = _entry_point()
    pending = _pending_envelope()
    store = _Store([pending, pending, pending, pending])
    escrow = _Escrow([None, None])
    first = await complete(store, escrow, pending)
    second = await complete(store, escrow, first)

    assert first is pending and second is pending
    assert escrow.release_calls == [
        (_ESCROW_ID, _WORKER_ID, _OPERATION_ID, _SOURCE_REFERENCE),
        (_ESCROW_ID, _WORKER_ID, _OPERATION_ID, _SOURCE_REFERENCE),
    ]
    assert store.finalize_calls == [_OPERATION_ID] * 4
    assert store.update_calls == []
