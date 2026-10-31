"""Unit tests for RulingOrchestrator."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from service_commons.exceptions import ServiceError

from court_service.judges.base import MockJudge
from court_service.services.ruling_orchestrator import RulingOrchestrator
from tests.fakes.in_memory_dispute_store import InMemoryDisputeStore as DisputeStore
from tests.helpers import make_ruling_acknowledgment


def _make_mock_platform_agent(agent_id: str = "agent-platform") -> MagicMock:
    """Create a minimal mock PlatformAgent for orchestrator tests."""
    mock = MagicMock()
    mock.agent_id = agent_id
    mock.split_escrow = AsyncMock(return_value={"status": "ok"})

    async def _record_ruling(
        _task_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return make_ruling_acknowledgment(
            payload,
            poster_id="agent-claimant",
            worker_id="agent-worker",
            escrow_id="escrow-1",
            reward=100,
            event_id=1,
        )

    mock.record_ruling = AsyncMock(side_effect=_record_ruling)
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

    orchestrator = RulingOrchestrator(
        store=store,
        feedback_extremely_satisfied_cutoff=80,
        feedback_satisfied_cutoff=40,
        feedback_comment_max_length=256,
        judging_lease_seconds=60,
    )
    judges = [
        MockJudge(
            judge_id="judge-1",
            fixed_worker_pct=60,
            spec_quality="satisfied",
            delivery_quality="satisfied",
            reasoning="Vote one",
        ),
        MockJudge(
            judge_id="judge-2",
            fixed_worker_pct=70,
            spec_quality="satisfied",
            delivery_quality="satisfied",
            reasoning="Vote two",
        ),
        MockJudge(
            judge_id="judge-3",
            fixed_worker_pct=80,
            spec_quality="satisfied",
            delivery_quality="satisfied",
            reasoning="Vote three",
        ),
    ]
    platform_agent = _make_mock_platform_agent()

    ruled = await orchestrator.execute_ruling(
        dispute_id=str(dispute["dispute_id"]),
        judges=judges,
        task_data={
            "spec": "Build feature X",
            "deliverables": [],
            "title": "Task 1",
            "reward": 100,
            "lifecycle_version": 3,
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
    orchestrator = RulingOrchestrator(
        store=store,
        feedback_extremely_satisfied_cutoff=80,
        feedback_satisfied_cutoff=40,
        feedback_comment_max_length=256,
        judging_lease_seconds=60,
    )
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
    orchestrator = RulingOrchestrator(
        store=store,
        feedback_extremely_satisfied_cutoff=80,
        feedback_satisfied_cutoff=40,
        feedback_comment_max_length=256,
        judging_lease_seconds=60,
    )
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
@pytest.mark.parametrize("raw", [{"worker_pct": 200}, {"worker_pct": -10}, {}])
def test_normalize_vote_rejects_malformed_votes(raw: dict[str, object]) -> None:
    """_normalize_vote() never clamps or defaults (plan 3.2): malformed raises."""
    with pytest.raises(ValueError):
        RulingOrchestrator._normalize_vote(raw, 0)
