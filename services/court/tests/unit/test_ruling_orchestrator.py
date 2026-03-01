"""Unit tests for RulingOrchestrator."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from service_commons.exceptions import ServiceError

from court_service.judges.base import MockJudge
from court_service.services.dispute_store import DisputeStore
from court_service.services.ruling_orchestrator import RulingOrchestrator


class _TaskBoardClientMock:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def record_ruling(self, task_id: str, ruling_payload: dict[str, object]) -> None:
        self.calls.append((task_id, ruling_payload))


class _CentralBankClientMock:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int]] = []

    async def split_escrow(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
    ) -> dict[str, object]:
        self.calls.append((escrow_id, worker_account_id, poster_account_id, worker_pct))
        return {"status": "ok"}


class _ReputationClientMock:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record_feedback(self, feedback_payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(feedback_payload)
        return {"status": "ok"}


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
    task_board_client = _TaskBoardClientMock()
    central_bank_client = _CentralBankClientMock()
    reputation_client = _ReputationClientMock()

    ruled = await orchestrator.execute_ruling(
        dispute_id=str(dispute["dispute_id"]),
        judges=judges,
        task_data={
            "spec": "Build feature X",
            "deliverables": ["artifact-a"],
            "title": "Task 1",
            "reward": 100,
        },
        task_board_client=task_board_client,
        central_bank_client=central_bank_client,
        reputation_client=reputation_client,
        platform_agent_id="agent-platform",
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

    with pytest.raises(ServiceError) as exc:
        await orchestrator.execute_ruling(
            dispute_id="disp-00000000-0000-0000-0000-000000000000",
            judges=[],
            task_data={},
            task_board_client=None,
            central_bank_client=None,
            reputation_client=None,
            platform_agent_id="agent-platform",
        )

    assert exc.value.error == "DISPUTE_NOT_FOUND"
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

    with pytest.raises(ServiceError) as exc:
        await orchestrator.execute_ruling(
            dispute_id=str(dispute["dispute_id"]),
            judges=[],
            task_data={},
            task_board_client=None,
            central_bank_client=None,
            reputation_client=None,
            platform_agent_id="agent-platform",
        )

    assert exc.value.error == "DISPUTE_NOT_READY"
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
