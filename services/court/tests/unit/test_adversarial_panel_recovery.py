"""Adversarial Court panel failure and median-selection sequences."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from service_commons.exceptions import ServiceError

from court_service.judges import DisputeContext, Judge, JudgeVote, MockJudge
from court_service.services.ruling_orchestrator import RulingOrchestrator
from tests.fakes.in_memory_dispute_store import InMemoryDisputeStore
from tests.helpers import make_ruling_acknowledgment

if TYPE_CHECKING:
    from pathlib import Path


class _CountingJudge(Judge):
    def __init__(self, judge_id: str, worker_pct: int) -> None:
        self.judge_id = judge_id
        self.worker_pct = worker_pct
        self.calls = 0

    async def evaluate(self, _context: DisputeContext) -> JudgeVote:
        self.calls += 1
        return _vote(self.judge_id, self.worker_pct)


class _FailingJudge(Judge):
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, _context: DisputeContext) -> JudgeVote:
        self.calls += 1
        raise TimeoutError("deterministic provider timeout")


class _ForbiddenJudge(Judge):
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, _context: DisputeContext) -> JudgeVote:
        self.calls += 1
        raise AssertionError("later judge must not be accepted after panel failure")


def _vote(judge_id: str, worker_pct: int) -> JudgeVote:
    return JudgeVote(
        judge_id=judge_id,
        worker_pct=worker_pct,
        spec_quality="satisfied",
        spec_quality_explanation="Deterministic campaign finding.",
        delivery_quality="satisfied",
        delivery_quality_explanation="Deterministic campaign finding.",
        reasoning=f"{judge_id} awards {worker_pct}%.",
        voted_at="2026-11-04T12:00:00Z",
    )


def _platform() -> MagicMock:
    platform = MagicMock()
    platform.agent_id = "agent-platform"

    async def record_ruling(_task_id: str, payload: dict[str, object]) -> dict[str, object]:
        return make_ruling_acknowledgment(
            payload,
            poster_id="agent-poster",
            worker_id="agent-worker",
            escrow_id="escrow-1",
            reward=100,
            event_id=1,
        )

    platform.record_ruling = AsyncMock(side_effect=record_ruling)
    platform.submit_platform_feedback = AsyncMock(return_value={"status": "ok"})
    return platform


def _orchestrator(store: InMemoryDisputeStore) -> RulingOrchestrator:
    return RulingOrchestrator(
        store=store,
        feedback_extremely_satisfied_cutoff=80,
        feedback_satisfied_cutoff=40,
        feedback_comment_max_length=256,
        judging_lease_seconds=60,
    )


def _dispute(store: InMemoryDisputeStore) -> str:
    dispute = store.insert_dispute(
        task_id="task-1",
        claimant_id="agent-poster",
        respondent_id="agent-worker",
        claim="The delivery is wrong.",
        escrow_id="escrow-1",
        rebuttal_deadline=datetime.now(UTC).isoformat(),
    )
    dispute_id = str(dispute["dispute_id"])
    store.update_rebuttal(dispute_id, "The worker responds.")
    return dispute_id


@pytest.mark.unit
async def test_partial_panel_failure_releases_cleanly_and_retry_rejudges_all(
    tmp_path: Path,
) -> None:
    """A failed second judge persists no partial decision or vote set."""
    store = InMemoryDisputeStore(db_path=str(tmp_path / "court.db"))
    dispute_id = _dispute(store)
    orchestrator = _orchestrator(store)
    first = _CountingJudge("judge-1", 10)
    second = _FailingJudge()
    third = _ForbiddenJudge()
    task = {
        "title": "Task",
        "spec": "Return exact work.",
        "deliverables": [],
        "reward": 100,
        "lifecycle_version": 3,
    }

    with pytest.raises(ServiceError, match="Judge 1 failed"):
        await orchestrator.execute_ruling(
            dispute_id,
            [first, second, third],
            task,
            _platform(),
        )

    failed = store.get_dispute(dispute_id)
    assert failed is not None
    assert failed["status"] == "rebuttal_pending"
    assert failed["votes"] == []
    assert first.calls == 1
    assert second.calls == 1
    assert third.calls == 0

    retried = await orchestrator.execute_ruling(
        dispute_id,
        [
            MockJudge("judge-1", 0, "satisfied", "dissatisfied", "Vote one"),
            MockJudge("judge-2", 33, "satisfied", "dissatisfied", "Vote two"),
            MockJudge("judge-3", 100, "satisfied", "dissatisfied", "Vote three"),
        ],
        task,
        _platform(),
    )

    assert retried["status"] == "ruled"
    assert retried["worker_pct"] == 33
    assert [vote["worker_pct"] for vote in retried["votes"]] == [0, 33, 100]
    store.close()


@pytest.mark.unit
def test_panel_median_for_zero_thirty_three_and_one_hundred_is_thirty_three() -> None:
    """The configured three-judge policy selects the middle percentage."""
    worker_pct, summary = RulingOrchestrator._compute_ruling(
        [_vote("judge-1", 0), _vote("judge-2", 33), _vote("judge-3", 100)]
    )

    assert worker_pct == 33
    assert summary == "judge-1 awards 0%.\n\njudge-2 awards 33%.\n\njudge-3 awards 100%."
