"""Unit tests for RulingOrchestrator."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from service_commons.exceptions import ServiceError

from court_service.judges.base import MockJudge
from court_service.services.dispute_store import DisputeStore
from court_service.services.ruling_orchestrator import RulingOrchestrator


def _make_mock_platform_agent(agent_id: str = "agent-platform") -> MagicMock:
    """Create a minimal mock PlatformAgent for orchestrator tests."""
    mock = MagicMock()
    mock.agent_id = agent_id
    mock.split_escrow = AsyncMock(return_value={"status": "ok"})
    mock.record_ruling = AsyncMock(return_value={"status": "ok"})
    mock.submit_platform_feedback = AsyncMock(return_value={"status": "ok"})
    return mock


@pytest.mark.unit
async def test_execute_ruling_with_mock_judges(tmp_path) -> None:
    """execute_ruling() rules a dispute and persists votes."""
    store = DisputeStore(db_path=str(tmp_path / "court.db"))
    dispute = store.insert_dispute(
        task_id="task-1",
        claimant_id="agent-claimant",
        respondent_id="agent-worker",
        claim="Claim text",
        escrow_id="escrow-1",
        rebuttal_deadline=datetime.now(UTC).isoformat(),
    )
    store.update_rebuttal(dispute["dispute_id"], "Rebuttal text")
    store.set_status(dispute["dispute_id"], "rebuttal_submitted")

    orchestrator = RulingOrchestrator(store=store)
    judges = [
        MockJudge(judge_id="judge-1", fixed_worker_pct=60, reasoning="Vote one"),
        MockJudge(judge_id="judge-2", fixed_worker_pct=70, reasoning="Vote two"),
        MockJudge(judge_id="judge-3", fixed_worker_pct=80, reasoning="Vote three"),
    ]
    platform_agent = _make_mock_platform_agent()

    ruled = await orchestrator.execute_ruling(
        dispute_id=str(dispute["dispute_id"]),
        judges=judges,
        task_data={
            "spec": "Build feature X",
            "deliverables": ["artifact-a"],
            "title": "Task 1",
            "reward": 100,
        },
        platform_agent=platform_agent,
    )

    assert ruled["status"] == "ruled"
    assert isinstance(ruled["worker_pct"], int)
    assert ruled["ruling_summary"] is not None
    assert len(ruled["votes"]) == 3
    store.close()


@pytest.mark.unit
async def test_validate_ruling_preconditions_dispute_not_found(tmp_path) -> None:
    """execute_ruling() fails when dispute_id does not exist."""
    store = DisputeStore(db_path=str(tmp_path / "court.db"))
    orchestrator = RulingOrchestrator(store=store)
    platform_agent = _make_mock_platform_agent()

    with pytest.raises(ServiceError) as exc:
        await orchestrator.execute_ruling(
            dispute_id="disp-00000000-0000-0000-0000-000000000000",
            judges=[],
            task_data={},
            platform_agent=platform_agent,
        )

    assert exc.value.error == "dispute_not_found"
    store.close()


@pytest.mark.unit
async def test_validate_ruling_preconditions_wrong_status(tmp_path) -> None:
    """execute_ruling() fails when dispute status is not ready for ruling."""
    store = DisputeStore(db_path=str(tmp_path / "court.db"))
    dispute = store.insert_dispute(
        task_id="task-1",
        claimant_id="agent-claimant",
        respondent_id="agent-worker",
        claim="Claim text",
        escrow_id="escrow-1",
        rebuttal_deadline=datetime.now(UTC).isoformat(),
    )
    store.set_status(str(dispute["dispute_id"]), "filed")
    orchestrator = RulingOrchestrator(store=store)
    platform_agent = _make_mock_platform_agent()

    with pytest.raises(ServiceError) as exc:
        await orchestrator.execute_ruling(
            dispute_id=str(dispute["dispute_id"]),
            judges=[],
            task_data={},
            platform_agent=platform_agent,
        )

    assert exc.value.error == "dispute_not_ready"
    store.close()


@pytest.mark.unit
def test_normalize_vote_clamps_worker_pct() -> None:
    """_normalize_vote() clamps worker_pct and applies defaults."""
    high_vote = RulingOrchestrator._normalize_vote({"worker_pct": 200}, 0)
    assert high_vote.worker_pct == 100
    assert high_vote.reasoning == "No reasoning provided."
    assert high_vote.judge_id == "judge-0"
    assert high_vote.voted_at != ""

    low_vote = RulingOrchestrator._normalize_vote({"worker_pct": -10}, 1)
    assert low_vote.worker_pct == 0

    default_vote = RulingOrchestrator._normalize_vote({}, 2)
    assert default_vote.worker_pct == 50
    assert default_vote.reasoning == "No reasoning provided."
    assert default_vote.judge_id == "judge-2"
    assert default_vote.voted_at != ""


@pytest.mark.unit
def test_delivery_rating_mapping() -> None:
    """_delivery_rating() maps worker_pct to expected rating."""
    assert RulingOrchestrator._delivery_rating(80) == "extremely_satisfied"
    assert RulingOrchestrator._delivery_rating(40) == "satisfied"
    assert RulingOrchestrator._delivery_rating(39) == "dissatisfied"


@pytest.mark.unit
def test_spec_rating_mapping() -> None:
    """_spec_rating() maps worker_pct to expected rating."""
    assert RulingOrchestrator._spec_rating(80) == "dissatisfied"
    assert RulingOrchestrator._spec_rating(40) == "satisfied"
    assert RulingOrchestrator._spec_rating(39) == "extremely_satisfied"
