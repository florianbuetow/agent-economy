"""Unit tests for DisputeStore."""

from datetime import UTC, datetime

import pytest

from court_service.services.dispute_store import DisputeStore


@pytest.mark.unit
def test_insert_and_get_dispute(tmp_path) -> None:
    """insert_dispute() persists and get_dispute() retrieves the record."""
    store = DisputeStore(db_path=str(tmp_path / "court.db"))
    deadline = datetime.now(UTC).isoformat()

    created = store.insert_dispute(
        task_id="task-1",
        claimant_id="a-claimant",
        respondent_id="a-worker",
        claim="Claim text",
        escrow_id="escrow-1",
        rebuttal_deadline=deadline,
    )

    fetched = store.get_dispute(created["dispute_id"])
    assert fetched == created
    assert created["status"] == "rebuttal_pending"
    assert created["votes"] == []
    store.close()


@pytest.mark.unit
def test_update_rebuttal_updates_dispute(tmp_path) -> None:
    """update_rebuttal() stores rebuttal and timestamp."""
    store = DisputeStore(db_path=str(tmp_path / "court.db"))
    dispute = store.insert_dispute(
        task_id="task-1",
        claimant_id="a-claimant",
        respondent_id="a-worker",
        claim="Claim text",
        escrow_id="escrow-1",
        rebuttal_deadline=datetime.now(UTC).isoformat(),
    )

    store.update_rebuttal(dispute["dispute_id"], "Rebuttal text")

    updated = store.get_dispute(dispute["dispute_id"])
    assert updated is not None
    assert updated["rebuttal"] == "Rebuttal text"
    assert updated["rebutted_at"] is not None
    store.close()


@pytest.mark.unit
def test_set_status_updates_dispute_status(tmp_path) -> None:
    """set_status() updates status."""
    store = DisputeStore(db_path=str(tmp_path / "court.db"))
    dispute = store.insert_dispute(
        task_id="task-1",
        claimant_id="a-claimant",
        respondent_id="a-worker",
        claim="Claim text",
        escrow_id="escrow-1",
        rebuttal_deadline=datetime.now(UTC).isoformat(),
    )

    store.set_status(dispute["dispute_id"], "judging")

    updated = store.get_dispute(dispute["dispute_id"])
    assert updated is not None
    assert updated["status"] == "judging"
    store.close()


@pytest.mark.unit
def test_persist_ruling_updates_dispute_and_votes(tmp_path) -> None:
    """persist_ruling() writes ruled state and vote rows atomically."""
    store = DisputeStore(db_path=str(tmp_path / "court.db"))
    dispute = store.insert_dispute(
        task_id="task-1",
        claimant_id="a-claimant",
        respondent_id="a-worker",
        claim="Claim text",
        escrow_id="escrow-1",
        rebuttal_deadline=datetime.now(UTC).isoformat(),
    )

    votes = [
        {
            "judge_id": "judge-1",
            "worker_pct": 70,
            "reasoning": "Reason one",
            "voted_at": datetime.now(UTC).isoformat(),
        },
        {
            "judge_id": "judge-2",
            "worker_pct": 60,
            "reasoning": "Reason two",
            "voted_at": datetime.now(UTC).isoformat(),
        },
    ]
    store.persist_ruling(dispute["dispute_id"], 65, "Combined ruling", votes)

    ruled = store.get_dispute(dispute["dispute_id"])
    assert ruled is not None
    assert ruled["status"] == "ruled"
    assert ruled["worker_pct"] == 65
    assert ruled["ruling_summary"] == "Combined ruling"
    assert len(ruled["votes"]) == 2
    assert {vote["judge_id"] for vote in ruled["votes"]} == {"judge-1", "judge-2"}
    store.close()


@pytest.mark.unit
def test_list_disputes_and_counts(tmp_path) -> None:
    """list_disputes(), count_disputes(), and count_active() return expected values."""
    store = DisputeStore(db_path=str(tmp_path / "court.db"))
    first = store.insert_dispute(
        task_id="task-1",
        claimant_id="a-claimant",
        respondent_id="a-worker",
        claim="Claim text",
        escrow_id="escrow-1",
        rebuttal_deadline=datetime.now(UTC).isoformat(),
    )
    second = store.insert_dispute(
        task_id="task-2",
        claimant_id="a-claimant-2",
        respondent_id="a-worker-2",
        claim="Claim text two",
        escrow_id="escrow-2",
        rebuttal_deadline=datetime.now(UTC).isoformat(),
    )

    store.set_status(second["dispute_id"], "ruled")

    listed_all = store.list_disputes(task_id=None, status=None)
    listed_task = store.list_disputes(task_id="task-1", status=None)
    listed_ruled = store.list_disputes(task_id=None, status="ruled")

    assert len(listed_all) == 2
    assert len(listed_task) == 1
    assert listed_task[0]["dispute_id"] == first["dispute_id"]
    assert len(listed_ruled) == 1
    assert listed_ruled[0]["dispute_id"] == second["dispute_id"]
    assert store.count_disputes() == 2
    assert store.count_active() == 1
    store.close()


@pytest.mark.unit
def test_close_succeeds(tmp_path) -> None:
    """close() can be called without error."""
    store = DisputeStore(db_path=str(tmp_path / "court.db"))
    store.close()
