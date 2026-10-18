"""MP-07 public task projections expose frozen v3 terms without private evidence."""

from __future__ import annotations

from typing import Any

import pytest

from task_board_service.schemas import TaskResponse, TaskSummary
from task_board_service.services.task_helpers import task_to_response, task_to_summary
from task_board_service.task_contract_projection import (
    LegacyReconciliation,
    PendingTransition,
    TaskContractProjection,
)

pytestmark = pytest.mark.unit

_BIDDING_CUTOFF_US = 4_070_908_800_123_456
_ACCEPTANCE_CUTOFF_US = 4_070_908_830_123_456
_EXECUTION_CUTOFF_US = 4_070_908_921_654_321
_REVIEW_CUTOFF_US = 4_070_908_982_000_007

_CONTRACT_FIELDS = tuple(TaskContractProjection.model_fields)


def _task_row(*, legacy: bool) -> dict[str, Any]:
    pending_transition: dict[str, object] | None = {
        "operation_id": "op-11111111-1111-4111-8111-111111111111",
        "command": "approve_task",
        "target_status": "approved",
        "target_lifecycle_version": 3,
        "operation_state": "claimed",
        "effect_state": "pending",
    }
    legacy_reconciliations: list[dict[str, object]] = []
    timing_contract_version = 2
    acceptance_window_seconds: int | None = 30
    acceptance_cutoff_us = _ACCEPTANCE_CUTOFF_US
    if legacy:
        pending_transition = None
        timing_contract_version = 1
        acceptance_window_seconds = None
        acceptance_cutoff_us = _BIDDING_CUTOFF_US
        legacy_reconciliations = [
            {
                "observation_id": "obs-11111111-1111-4111-8111-111111111111",
                "observation_kind": "worker_settlement_pending",
                "effect_state": "pending",
                "target_outcome_hint": "approved",
            }
        ]
    return {
        "task_id": "t-11111111-1111-4111-8111-111111111111",
        "poster_id": "a-poster",
        "title": "Frozen public task projection",
        "spec": "Use the authoritative persisted cutoff fields.",
        "reward": 100,
        "bidding_deadline_seconds": 1,
        "deadline_seconds": 1,
        "review_deadline_seconds": 1,
        "status": "submitted",
        "escrow_id": "esc-11111111-1111-4111-8111-111111111111",
        "bid_count": 1,
        "worker_id": "a-worker",
        "accepted_bid_id": "bid-11111111-1111-4111-8111-111111111111",
        "created_at": "1999-01-01T00:00:00Z",
        "accepted_at": "1999-01-01T00:00:01Z",
        "submitted_at": "1999-01-01T00:00:02Z",
        "approved_at": None,
        "cancelled_at": None,
        "disputed_at": None,
        "dispute_reason": None,
        "dispute_id": None,
        "rebuttal_deadline": None,
        "rebuttal_submitted_at": None,
        "ruling_id": None,
        "ruled_at": None,
        "worker_pct": None,
        "ruling_summary": None,
        "expired_at": None,
        "escrow_pending": 0,
        "lifecycle_version": 2,
        "timing_contract_version": timing_contract_version,
        "acceptance_window_seconds": acceptance_window_seconds,
        "bidding_cutoff_us": _BIDDING_CUTOFF_US,
        "acceptance_cutoff_us": acceptance_cutoff_us,
        "execution_cutoff_us": _EXECUTION_CUTOFF_US,
        "review_cutoff_us": _REVIEW_CUTOFF_US,
        "accepted_reward": 100,
        "accepted_bid_amount": 90,
        "pending_transition": pending_transition,
        "legacy_reconciliations": legacy_reconciliations,
        "observed_fact_json": "private historical evidence",
        "caller_kid": "private-service-identity",
        "request_sha256": "f" * 64,
        "_pending_transition_json": "private Gateway transport field",
    }


def _assert_common_projection(projected: dict[str, Any]) -> None:
    assert {field: projected[field] for field in _CONTRACT_FIELDS} == {
        field: _task_row(legacy=False)[field] for field in _CONTRACT_FIELDS
    }
    assert projected["bidding_deadline"] == "2099-01-01T00:00:00.123456Z"
    assert projected["acceptance_deadline"] == "2099-01-01T00:00:30.123456Z"
    assert projected["execution_deadline"] == "2099-01-01T00:02:01.654321Z"
    assert projected["review_deadline"] == "2099-01-01T00:03:02.000007Z"
    assert {
        "observed_fact_json",
        "caller_kid",
        "request_sha256",
        "_pending_transition_json",
    }.isdisjoint(projected)


def test_native_full_and_summary_projections_use_exact_persisted_cutoffs() -> None:
    row = _task_row(legacy=False)
    response = task_to_response(row)
    summary = task_to_summary(row)

    _assert_common_projection(response)
    _assert_common_projection(summary)
    parsed_response = TaskResponse.model_validate(response)
    parsed_summary = TaskSummary.model_validate(summary)
    assert isinstance(parsed_response.pending_transition, PendingTransition)
    assert isinstance(parsed_summary.pending_transition, PendingTransition)
    assert parsed_response.legacy_reconciliations == []
    assert parsed_summary.legacy_reconciliations == []
    assert issubclass(TaskResponse, TaskContractProjection)
    assert issubclass(TaskSummary, TaskContractProjection)


def test_migrated_timing_v1_preserves_null_window_and_separate_reconciliation() -> None:
    row = _task_row(legacy=True)
    for projected, model in (
        (task_to_response(row), TaskResponse),
        (task_to_summary(row), TaskSummary),
    ):
        assert projected["timing_contract_version"] == 1
        assert projected["acceptance_window_seconds"] is None
        assert projected["acceptance_cutoff_us"] == projected["bidding_cutoff_us"]
        assert projected["acceptance_deadline"] == projected["bidding_deadline"]
        assert projected["pending_transition"] is None
        parsed = model.model_validate(projected)
        assert len(parsed.legacy_reconciliations) == 1
        assert isinstance(parsed.legacy_reconciliations[0], LegacyReconciliation)
        assert "operation_id" not in parsed.legacy_reconciliations[0].model_dump()
        assert "command" not in parsed.legacy_reconciliations[0].model_dump()


@pytest.mark.parametrize("helper", [task_to_response, task_to_summary])
@pytest.mark.parametrize("field", _CONTRACT_FIELDS)
def test_public_projection_requires_every_contract_field_without_fallback(
    helper: object,
    field: str,
) -> None:
    row = _task_row(legacy=False)
    del row[field]
    assert callable(helper)
    with pytest.raises(KeyError, match=field):
        helper(row)
