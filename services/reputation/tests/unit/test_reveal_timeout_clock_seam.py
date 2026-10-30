"""G11 — durable timed reveal (plan item 3.4), driven by the injectable clock seam.

Before G11, timeout visibility was computed at read time, so the Reputation API
showed timed-out feedback while the stored ``visible`` flag (read by the UI)
stayed 0. Now reads follow the stored flag only, and a sweep durably reveals
sealed feedback once ``reveal_timeout_seconds`` have passed.

This suite submits sealed feedback through the real ``submit_feedback`` path,
moves the frozen clock across the timeout, and checks that the API view and the
stored flag agree before and after the sweep.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from reputation_service.core.reveal_sweep import reveal_sweep_loop
from reputation_service.core.state import AppState
from reputation_service.services import feedback as feedback_module
from reputation_service.services.feedback import (
    get_feedback_by_id,
    get_feedback_for_task,
    reveal_timed_out_feedback,
    submit_feedback,
)
from reputation_service.types import FeedbackRecord
from tests.fakes.sqlite_feedback_store import SqliteFeedbackStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

MAX_COMMENT_LENGTH = 256
REVEAL_TIMEOUT_SECONDS = 86400


@pytest.fixture
def reset_clock() -> Iterator[None]:
    """Restore the feedback module's clock seam after a test overrides it."""
    original = feedback_module._clock
    try:
        yield
    finally:
        feedback_module._clock = original


def _make_store(tmp_path: Path) -> SqliteFeedbackStore:
    return SqliteFeedbackStore(db_path=str(tmp_path / "test.db"))


def _valid_body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "task_id": "task-vis09",
        "from_agent_id": "agent-a",
        "to_agent_id": "agent-b",
        "category": "delivery_quality",
        "rating": "satisfied",
        "comment": "Good work",
    }
    base.update(overrides)
    return base


def _submit_sealed(store: SqliteFeedbackStore, task_id: str) -> FeedbackRecord:
    result = submit_feedback(
        store,
        _valid_body(task_id=task_id),
        MAX_COMMENT_LENGTH,
        force_visible=False,
        source="peer",
    )
    assert isinstance(result, FeedbackRecord)
    assert result.visible is False
    return result


def _stored_flag(store: SqliteFeedbackStore, feedback_id: str) -> bool:
    record = store.get_by_id(feedback_id)
    assert record is not None
    return record.visible


@pytest.mark.unit
@pytest.mark.usefixtures("reset_clock")
class TestDurableTimedReveal:
    """The API view and the stored flag agree before and after the timeout sweep."""

    def test_sweep_before_timeout_reveals_nothing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        record = _submit_sealed(store, "task-g11-before")
        submitted_at = datetime.fromisoformat(record.submitted_at)

        feedback_module._clock = lambda: (
            submitted_at + timedelta(seconds=REVEAL_TIMEOUT_SECONDS - 1)
        )
        assert reveal_timed_out_feedback(store, REVEAL_TIMEOUT_SECONDS) == 0
        assert get_feedback_by_id(store, record.feedback_id) is None
        assert _stored_flag(store, record.feedback_id) is False

    def test_reads_do_not_reveal_without_the_sweep(self, tmp_path: Path) -> None:
        """Past the timeout but before the sweep, the API agrees with the stored 0."""
        store = _make_store(tmp_path)
        record = _submit_sealed(store, "task-g11-unswept")
        submitted_at = datetime.fromisoformat(record.submitted_at)

        feedback_module._clock = lambda: (
            submitted_at + timedelta(seconds=REVEAL_TIMEOUT_SECONDS + 1)
        )
        assert get_feedback_by_id(store, record.feedback_id) is None
        assert get_feedback_for_task(store, "task-g11-unswept") == []
        assert _stored_flag(store, record.feedback_id) is False

    def test_sweep_after_timeout_reveals_durably_once(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        record = _submit_sealed(store, "task-g11-after")
        submitted_at = datetime.fromisoformat(record.submitted_at)

        feedback_module._clock = lambda: (
            submitted_at + timedelta(seconds=REVEAL_TIMEOUT_SECONDS + 1)
        )
        assert reveal_timed_out_feedback(store, REVEAL_TIMEOUT_SECONDS) == 1
        assert _stored_flag(store, record.feedback_id) is True
        revealed = get_feedback_by_id(store, record.feedback_id)
        assert revealed is not None
        assert [r.feedback_id for r in get_feedback_for_task(store, "task-g11-after")] == [
            record.feedback_id
        ]

        # A second sweep finds nothing left to reveal.
        assert reveal_timed_out_feedback(store, REVEAL_TIMEOUT_SECONDS) == 0


@pytest.mark.unit
@pytest.mark.usefixtures("reset_clock")
async def test_background_loop_reveals_timed_out_feedback(tmp_path: Path) -> None:
    """The lifespan's sweep loop performs the durable reveal on its own."""
    store = _make_store(tmp_path)
    record = _submit_sealed(store, "task-g11-loop")
    submitted_at = datetime.fromisoformat(record.submitted_at)
    feedback_module._clock = lambda: submitted_at + timedelta(seconds=REVEAL_TIMEOUT_SECONDS + 1)

    state = AppState(feedback_store=store, feedback_reveal_timeout_seconds=REVEAL_TIMEOUT_SECONDS)
    loop = asyncio.create_task(reveal_sweep_loop(state, 0.01))
    try:
        for _ in range(200):
            if _stored_flag(store, record.feedback_id):
                break
            await asyncio.sleep(0.01)
    finally:
        loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop
    assert _stored_flag(store, record.feedback_id) is True
