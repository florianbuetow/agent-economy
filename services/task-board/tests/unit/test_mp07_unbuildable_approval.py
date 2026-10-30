"""MP-07 approval classification when frozen command terms do not exist."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.task_review import TaskReviewCoordinator

if TYPE_CHECKING:
    from task_board_service.services.task_manager import TaskManager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_TASK_ID = "t-unbuildable-approval"
_POSTER_ID = "a-poster"
_WINNER_ID = "op-22222222-2222-4222-8222-222222222222"
_REQUEST_ID = "op-11111111-1111-4111-8111-111111111111"


@dataclass
class _Store:
    task: dict[str, Any]
    claim_error: ServiceError | None
    claim_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[tuple[object, ...]] = field(default_factory=list)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return dict(self.task) if task_id == _TASK_ID else None

    def claim_task_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.claim_calls.append(payload)
        if self.claim_error is not None:
            raise self.claim_error
        raise AssertionError("Approval test must install an explicit claim outcome")

    def update_task(self, *args: object, **_kwargs: object) -> int:
        self.update_calls.append(args)
        return 1


@dataclass
class _Escrow:
    calls: list[tuple[object, ...]] = field(default_factory=list)

    async def release_escrow(self, *args: object) -> None:
        self.calls.append(args)
        raise AssertionError("Classification must not reach Bank delivery")


def _task(
    *,
    status: str,
    lifecycle_version: int,
    accepted_terms: bool,
    pending: dict[str, object] | None = None,
    legacy: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": _TASK_ID,
        "poster_id": _POSTER_ID,
        "worker_id": "a-worker" if accepted_terms else None,
        "escrow_id": "esc-1",
        "accepted_reward": 100 if accepted_terms else None,
        "accepted_bid_amount": 90 if accepted_terms else None,
        "worker_payment": None,
        "poster_refund": None,
        "status": status,
        "lifecycle_version": lifecycle_version,
        "pending_transition": pending,
        "legacy_reconciliations": [] if legacy is None else legacy,
    }


def _manager(store: _Store, escrow: _Escrow) -> TaskManager:
    validator = type(
        "ValidatorDouble",
        (),
        {"validate_jws_token": staticmethod(_validated_payload)},
    )()
    value = type(
        "ManagerDouble",
        (),
        {"store": store, "escrow_coordinator": escrow, "token_validator": validator},
    )()
    return cast("TaskManager", value)


async def _validated_payload(
    _token: str,
    _action: str,
    *,
    expected_resource: str,
) -> dict[str, object]:
    assert expected_resource == f"POST /tasks/{_TASK_ID}/approve"
    return {
        "_signer_id": _POSTER_ID,
        "task_id": _TASK_ID,
        "poster_id": _POSTER_ID,
        "operation_id": _REQUEST_ID,
        "expected_lifecycle_version": 0,
    }


async def _approve(store: _Store, escrow: _Escrow) -> None:
    coordinator = TaskReviewCoordinator(_manager(store, escrow))
    await coordinator.approve_task(_TASK_ID, "signed-token")


@pytest.mark.parametrize("recovery", ["native", "legacy"])
async def test_recovery_owner_precedes_unbuildable_approval_state(recovery: str) -> None:
    pending: dict[str, object] | None = None
    legacy: list[dict[str, object]] = []
    expected_error: str
    expected_details: dict[str, object]
    if recovery == "native":
        pending = {
            "operation_id": _WINNER_ID,
            "command": "cancel_task",
            "target_status": "cancelled",
            "target_lifecycle_version": 1,
            "operation_state": "claimed",
            "effect_state": "pending",
        }
        expected_error = "transition_pending"
        expected_details = {"winning_operation_id": _WINNER_ID}
    else:
        legacy = [
            {
                "observation_id": "obs-legacy-approval",
                "observation_kind": "worker_settlement_pending",
                "effect_state": "pending",
                "target_outcome_hint": "approved",
            }
        ]
        expected_error = "legacy_reconciliation_pending"
        expected_details = {"observation_id": "obs-legacy-approval"}
    store = _Store(
        _task(
            status="open",
            lifecycle_version=0,
            accepted_terms=False,
            pending=pending,
            legacy=legacy,
        ),
        None,
    )
    escrow = _Escrow()

    with pytest.raises(ServiceError) as raised:
        await _approve(store, escrow)

    assert raised.value.status_code == 409
    assert raised.value.error == expected_error
    assert raised.value.details == expected_details
    assert store.claim_calls == []
    assert store.update_calls == []
    assert escrow.calls == []


async def test_stale_version_precedes_open_status_classification() -> None:
    store = _Store(_task(status="open", lifecycle_version=1, accepted_terms=False), None)
    escrow = _Escrow()

    with pytest.raises(ServiceError) as raised:
        await _approve(store, escrow)

    assert raised.value.status_code == 409
    assert raised.value.error == "task_version_conflict"
    assert raised.value.details == {
        "expected_lifecycle_version": 0,
        "actual_lifecycle_version": 1,
        "current_status": "open",
    }
    assert store.claim_calls == []
    assert store.update_calls == []
    assert escrow.calls == []


async def test_matching_version_open_task_is_invalid_status_without_effects() -> None:
    store = _Store(_task(status="open", lifecycle_version=0, accepted_terms=False), None)
    escrow = _Escrow()

    with pytest.raises(ServiceError) as raised:
        await _approve(store, escrow)

    assert raised.value.status_code == 409
    assert raised.value.error == "invalid_status"
    assert raised.value.details == {"expected_status": "submitted", "current_status": "open"}
    assert store.claim_calls == []
    assert store.update_calls == []
    assert escrow.calls == []


async def test_accepted_and_approved_histories_still_reach_gateway_replay() -> None:
    sentinel = ServiceError("claim_reached", "Gateway replay reached", 418, {})
    for status, version in (("accepted", 1), ("approved", 3)):
        store = _Store(
            _task(status=status, lifecycle_version=version, accepted_terms=True), sentinel
        )
        escrow = _Escrow()
        with pytest.raises(ServiceError) as raised:
            await _approve(store, escrow)
        assert raised.value is sentinel
        assert len(store.claim_calls) == 1
        assert store.update_calls == []
        assert escrow.calls == []


async def test_submitted_task_missing_frozen_terms_remains_dependency_corruption() -> None:
    store = _Store(_task(status="submitted", lifecycle_version=2, accepted_terms=False), None)
    escrow = _Escrow()

    with pytest.raises(ServiceError) as raised:
        await _approve(store, escrow)

    assert raised.value.status_code == 502
    assert raised.value.error == "invalid_gateway_response"
    assert store.claim_calls == []
    assert store.update_calls == []
    assert escrow.calls == []
