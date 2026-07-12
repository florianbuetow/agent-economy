"""VIS-09 — reveal-timeout clock seam (WP-07).

``is_visible()`` compares "now" against ``submitted_at + reveal_timeout_seconds``.
Before this seam, "now" was ``datetime.now(UTC)`` inline, so no test could drive the
24h lazy-reveal path deterministically. The pre-existing ``TestIsVisible`` tests in
``test_feedback_service.py`` work around this by hand-constructing a
``FeedbackRecord`` with a pre-computed *past* ``submitted_at`` -- they never move
"now" itself, so they do not exercise a submission made through the real
``submit_feedback`` path with time advancing after the fact.

This suite drives the real path: submit sealed feedback (no counterpart) through
``submit_feedback``, capture the store's own ``submitted_at``, then move the frozen
clock across the ``reveal_timeout_seconds`` boundary and re-read through
``get_feedback_by_id``/``get_feedback_for_task``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from reputation_service.services import feedback as feedback_module
from reputation_service.services.feedback import (
    get_feedback_by_id,
    get_feedback_for_task,
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


@pytest.mark.unit
class TestRevealTimeoutClockSeam:
    """The 24h lazy reveal is driven by an injectable clock, not the wall clock."""

    @pytest.mark.usefixtures("reset_clock")
    def test_sealed_feedback_becomes_visible_once_the_frozen_clock_crosses_the_timeout(
        self,
        tmp_path: Path,
    ) -> None:
        store = _make_store(tmp_path)
        result = submit_feedback(store, _valid_body(), MAX_COMMENT_LENGTH, force_visible=False)
        assert isinstance(result, FeedbackRecord)
        assert result.visible is False

        submitted_at = datetime.fromisoformat(result.submitted_at)

        # Before the timeout: still sealed on read.
        feedback_module._clock = lambda: (
            submitted_at + timedelta(seconds=REVEAL_TIMEOUT_SECONDS - 1)
        )
        before = get_feedback_by_id(store, result.feedback_id, REVEAL_TIMEOUT_SECONDS)
        assert before is None, "sealed record must stay hidden before the timeout elapses"

        # After the timeout: revealed on read, even though no counterpart ever arrived.
        feedback_module._clock = lambda: (
            submitted_at + timedelta(seconds=REVEAL_TIMEOUT_SECONDS + 1)
        )
        after = get_feedback_by_id(store, result.feedback_id, REVEAL_TIMEOUT_SECONDS)
        assert after is not None, "sealed record must become visible once the timeout elapses"
        assert after.feedback_id == result.feedback_id

    @pytest.mark.usefixtures("reset_clock")
    def test_task_listing_honors_the_frozen_clock_too(
        self,
        tmp_path: Path,
    ) -> None:
        """get_feedback_for_task filters through the same seam-driven is_visible."""
        store = _make_store(tmp_path)
        result = submit_feedback(
            store,
            _valid_body(task_id="task-vis09-list"),
            MAX_COMMENT_LENGTH,
            force_visible=False,
        )
        assert isinstance(result, FeedbackRecord)
        submitted_at = datetime.fromisoformat(result.submitted_at)

        feedback_module._clock = lambda: (
            submitted_at + timedelta(seconds=REVEAL_TIMEOUT_SECONDS - 1)
        )
        assert get_feedback_for_task(store, "task-vis09-list", REVEAL_TIMEOUT_SECONDS) == []

        feedback_module._clock = lambda: (
            submitted_at + timedelta(seconds=REVEAL_TIMEOUT_SECONDS + 1)
        )
        visible = get_feedback_for_task(store, "task-vis09-list", REVEAL_TIMEOUT_SECONDS)
        assert [r.feedback_id for r in visible] == [result.feedback_id]
