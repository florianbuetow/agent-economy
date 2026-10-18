"""T-181: journals retain exact Task Board identities without filesystem aliases."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from base_agent.runtime_intents import RuntimeIntentError
from tests.unit.test_runtime_intents import (
    _OPERATION_ID,
    _bid_ack_for,
    _bid_intent_for,
    _store,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "task_id",
    (
        "t-00000000-0000-0000-0000-000000000001",
        "t-00000000-0000-7000-8000-000000000001",
        "t-ABCDEFAB-ABCD-4ABC-8ABC-ABCDEFABCDEF",
        "T-ABCDEFAB-ABCD-4ABC-8ABC-ABCDEFABCDEF",
    ),
)
def test_public_task_identity_is_preserved_through_completion_and_reopen(
    tmp_path: Path, task_id: str
) -> None:
    with _store(tmp_path) as store:
        pending = store.create(_bid_intent_for(task_id, _OPERATION_ID))
        completed = store.record_acknowledgment(
            task_id=task_id,
            slot="bid",
            expected_revision=pending.revision,
            status_code=201,
            body=_bid_ack_for(task_id, _OPERATION_ID),
        )
        assert completed.state == "completed"
        assert completed.task_id == task_id
        assert completed.public_arguments["task_id"] == task_id
    with _store(tmp_path) as reopened:
        assert reopened.load(task_id, "bid") == completed


def test_distinct_case_sensitive_remote_ids_have_distinct_journal_paths(tmp_path: Path) -> None:
    lower = "t-abcdefab-abcd-4abc-8abc-abcdefabcdef"
    upper = lower.upper()
    other_operation = "op-00000000-0000-4000-8000-000000000182"
    with _store(tmp_path) as store:
        first = store.create(_bid_intent_for(lower, _OPERATION_ID))
        second = store.create(_bid_intent_for(upper, other_operation))
        first_path = store.intent_path(lower, "bid")
        second_path = store.intent_path(upper, "bid")
        assert first_path.name.casefold() != second_path.name.casefold()
        assert first_path.read_bytes() != second_path.read_bytes()
    with _store(tmp_path) as reopened:
        assert reopened.load(lower, "bid") == first
        assert reopened.load(upper, "bid") == second
        assert len(reopened.unfinished()) == 2


@pytest.mark.parametrize(
    "task_id",
    (
        "../t-abcdefab-abcd-4abc-8abc-abcdefabcdef",
        "/t-abcdefab-abcd-4abc-8abc-abcdefabcdef",
        "t-abcdefab-abcd-4abc-8abc-abcdefabcdef\n",
        "t-abcdefab-abcd-4abc-8abc-abcdefabcdef/extra",
        "t-abcdefababcd4abc8abcabcdefabcdef",
        "t-1",
    ),
)
def test_invalid_task_identity_cannot_publish_a_record(tmp_path: Path, task_id: str) -> None:
    with _store(tmp_path) as store:
        with pytest.raises(RuntimeIntentError):
            store.create(_bid_intent_for(task_id, _OPERATION_ID))
        assert store.unfinished() == ()
