"""T-181: durable callers preserve the public canonical operation-ID domain."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from service_auth.financial_responses import validate_operation_id

from base_agent.runtime_intents import RuntimeIntentError
from tests.unit.test_runtime_intents import (
    _TASK_ID,
    _bid_ack_for,
    _bid_intent_for,
    _intent,
    _store,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_CANONICAL_IDS = (
    "op-00000000-0000-0000-0000-000000000001",
    "op-00000000-0000-4000-8000-000000000001",
    "op-00000000-0000-7000-8000-000000000001",
    "op-00000000-0000-8000-8000-000000000001",
    "op-ffffffff-ffff-ffff-ffff-ffffffffffff",
)


@pytest.mark.parametrize("operation_id", _CANONICAL_IDS)
def test_public_operation_identity_survives_completion_and_reopen(
    tmp_path: Path, operation_id: str
) -> None:
    assert validate_operation_id(operation_id) == operation_id
    with _store(tmp_path) as store:
        pending = store.create(_bid_intent_for(_TASK_ID, operation_id))
        completed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=201,
            body=_bid_ack_for(_TASK_ID, operation_id),
        )
        assert completed.state == "completed"
        assert completed.operation_id == operation_id
    with _store(tmp_path) as reopened:
        assert reopened.load(_TASK_ID, "bid") == completed


@pytest.mark.parametrize("winning_operation_id", _CANONICAL_IDS)
def test_public_foreign_operation_is_reported_as_blocking_and_reopens(
    tmp_path: Path, winning_operation_id: str
) -> None:
    assert validate_operation_id(winning_operation_id) == winning_operation_id
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        blocked = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=409,
            body={
                "error": "transition_pending",
                "message": "Another operation owns the transition",
                "details": {"winning_operation_id": winning_operation_id},
            },
        )
        assert blocked.state == "blocked"
        assert blocked.diagnostics[-1] == {
            "kind": "foreign_pending_owner",
            "operation_id": pending.operation_id,
            "winning_operation_id": winning_operation_id,
        }
    with _store(tmp_path) as reopened:
        assert reopened.load(_TASK_ID, "bid") == blocked


@pytest.mark.parametrize(
    "operation_id",
    (
        "op-FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
        "op-00000000000040008000000000000001",
        "op-00000000-0000-4000-8000-000000000001\n",
        "op-{00000000-0000-4000-8000-000000000001}",
        "00000000-0000-4000-8000-000000000001",
        "op-not-a-uuid",
    ),
)
def test_noncanonical_operation_identity_is_rejected_before_journal_write(
    tmp_path: Path, operation_id: str
) -> None:
    with pytest.raises(ValueError):
        validate_operation_id(operation_id)
    with _store(tmp_path) as store:
        with pytest.raises(RuntimeIntentError, match="canonical"):
            store.create(_bid_intent_for(_TASK_ID, operation_id))
        assert store.load(_TASK_ID, "bid") is None
