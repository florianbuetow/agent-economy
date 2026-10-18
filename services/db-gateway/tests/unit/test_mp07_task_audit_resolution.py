"""MP-07 task-audit resolution and symmetric lifecycle regressions."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.task_contract_audit import audit_task_contracts
from tests.unit import test_mp07_legacy_effect_migration as legacy
from tests.unit import test_mp07_schema_regressions as regressions

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _observation_immutable(connection: sqlite3.Connection) -> tuple[str, str]:
    row = connection.execute(
        "SELECT observed_fact_json, observed_fact_sha256 "
        "FROM board_legacy_effect_observations WHERE task_id='t-1'"
    ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def _assert_clean(connection: sqlite3.Connection) -> None:
    assert audit_money(connection).financially_clean
    assert audit_receipt_ownership(connection).ownership_clean
    result = audit_task_contracts(connection)
    assert result.contracts_clean, result.findings


@pytest.mark.parametrize(
    "case",
    ["pending_approved_settlement", "succeeded_worker_settlement", "succeeded_court_claim"],
)
def test_unresolved_flag_cannot_clear_without_matching_current_outcome(
    tmp_path: Path,
    case: str,
) -> None:
    database, _inventory = regressions._migrate_case(tmp_path, case)
    connection = sqlite3.connect(database)
    try:
        immutable = _observation_immutable(connection)
        connection.execute(
            "UPDATE board_legacy_effect_observations SET unresolved=0 WHERE task_id='t-1'"
        )
        connection.commit()
        result = audit_task_contracts(connection)
        assert _observation_immutable(connection) == immutable
    finally:
        connection.close()
    assert not result.contracts_clean
    assert "legacy_observation_invalid" in {finding.code for finding in result.findings}


def test_pending_worker_observation_resolves_only_with_real_worker_settlement(
    tmp_path: Path,
) -> None:
    database, _inventory = regressions._migrate_case(tmp_path, "pending_approved_settlement")
    connection = sqlite3.connect(database)
    try:
        immutable = _observation_immutable(connection)
        legacy._add_worker_settlement_receipt(connection)
        connection.execute("UPDATE board_tasks SET escrow_pending=0 WHERE task_id='t-1'")
        connection.execute(
            "UPDATE board_legacy_effect_observations SET unresolved=0 WHERE task_id='t-1'"
        )
        connection.commit()
        _assert_clean(connection)
        assert _observation_immutable(connection) == immutable
    finally:
        connection.close()


def test_succeeded_worker_observation_resolves_when_task_reaches_approved(
    tmp_path: Path,
) -> None:
    database, _inventory = regressions._migrate_case(tmp_path, "succeeded_worker_settlement")
    connection = sqlite3.connect(database)
    try:
        immutable = _observation_immutable(connection)
        connection.execute(
            "UPDATE board_tasks SET status='approved', approved_at='2026-10-17T12:00:04Z' "
            "WHERE task_id='t-1'"
        )
        connection.execute(
            "UPDATE board_legacy_effect_observations SET unresolved=0 WHERE task_id='t-1'"
        )
        connection.commit()
        _assert_clean(connection)
        assert _observation_immutable(connection) == immutable
    finally:
        connection.close()


def test_resolved_observation_immutable_hash_tampering_remains_dirty(tmp_path: Path) -> None:
    database, _inventory = regressions._migrate_case(tmp_path, "pending_approved_settlement")
    connection = sqlite3.connect(database)
    try:
        legacy._add_worker_settlement_receipt(connection)
        connection.execute("UPDATE board_tasks SET escrow_pending=0 WHERE task_id='t-1'")
        connection.execute(
            "UPDATE board_legacy_effect_observations SET unresolved=0, "
            "observed_fact_sha256=? WHERE task_id='t-1'",
            ("0" * 64,),
        )
        connection.commit()
        result = audit_task_contracts(connection)
    finally:
        connection.close()
    assert not result.contracts_clean
    assert "legacy_observation_invalid" in {finding.code for finding in result.findings}


def test_court_observation_allows_linked_claim_and_later_court_progression(tmp_path: Path) -> None:
    database, _inventory = regressions._migrate_case(tmp_path, "succeeded_court_claim")
    connection = sqlite3.connect(database)
    try:
        immutable = _observation_immutable(connection)
        connection.execute(
            "UPDATE board_tasks SET status='disputed', dispute_id='clm-1', "
            "dispute_reason='reason', disputed_at='2026-10-17T12:00:02Z' WHERE task_id='t-1'"
        )
        connection.execute(
            "UPDATE board_legacy_effect_observations SET unresolved=0 WHERE task_id='t-1'"
        )
        for status in ("rebuttal_pending", "rebuttal_submitted", "judging"):
            connection.execute("UPDATE court_claims SET status=? WHERE claim_id='clm-1'", (status,))
            connection.commit()
            _assert_clean(connection)
            assert _observation_immutable(connection) == immutable
        connection.execute("UPDATE court_claims SET status='ruled' WHERE claim_id='clm-1'")
        connection.execute(
            "UPDATE board_tasks SET status='ruled', ruling_id='clm-1', "
            "ruled_at='2026-10-17T12:00:12Z', "
            "worker_pct=100, ruling_summary='worker wins' WHERE task_id='t-1'"
        )
        connection.execute(
            "INSERT INTO court_rulings VALUES "
            "('clm-1','clm-1','t-1',100,'worker wins','[]','2026-10-17T12:00:12Z')"
        )
        connection.commit()
        _assert_clean(connection)
        assert _observation_immutable(connection) == immutable
    finally:
        connection.close()


@pytest.mark.parametrize(
    "fault",
    [
        "open_with_acceptance_phase",
        "accepted_with_submission_phase",
        "unsupported_status",
        "open_with_accepted_terms",
        "cancelled_with_accepted_terms",
    ],
)
def test_task_audit_rejects_symmetric_phase_and_state_faults(
    tmp_path: Path,
    fault: str,
) -> None:
    database = regressions._migrate_clean(tmp_path)
    connection = sqlite3.connect(database)
    try:
        if fault == "open_with_acceptance_phase":
            connection.execute(
                "UPDATE board_tasks SET accepted_at='2026-10-17T12:00:00.500000Z', "
                "execution_deadline='2026-10-17T12:00:01.500000Z' WHERE task_id='t-bank-only'"
            )
            connection.execute(
                "UPDATE board_task_contracts SET execution_cutoff_us=1792238401500000 "
                "WHERE task_id='t-bank-only'"
            )
        elif fault == "accepted_with_submission_phase":
            connection.execute("UPDATE board_tasks SET status='accepted' WHERE task_id='t-1'")
        elif fault == "unsupported_status":
            connection.execute(
                "UPDATE board_tasks SET status='unknown' WHERE task_id='t-bank-only'"
            )
        elif fault == "open_with_accepted_terms":
            connection.execute("UPDATE board_tasks SET status='open' WHERE task_id='t-1'")
        else:
            connection.execute("UPDATE board_tasks SET status='cancelled' WHERE task_id='t-1'")
        connection.commit()
        result = audit_task_contracts(connection)
    finally:
        connection.close()
    assert not result.contracts_clean


@pytest.mark.parametrize("phase", ["preacceptance", "execution"])
def test_expired_task_acceptance_phase_nuance_remains_valid(tmp_path: Path, phase: str) -> None:
    database = regressions._migrate_clean(tmp_path)
    connection = sqlite3.connect(database)
    try:
        task_id = "t-bank-only" if phase == "preacceptance" else "t-1"
        if phase == "execution":
            # Construct a pre-submission phase fixture, not a disputed -> expired transition.
            connection.execute("DELETE FROM court_rebuttals WHERE claim_id='clm-1'")
            connection.execute("DELETE FROM court_claims WHERE claim_id='clm-1'")
            connection.execute(
                "DELETE FROM events WHERE task_id='t-1' "
                "AND event_type IN ('claim.filed', 'rebuttal.submitted')"
            )
            connection.execute(
                "UPDATE board_tasks SET submitted_at=NULL, review_deadline=NULL, "
                "dispute_reason=NULL, dispute_id=NULL, rebuttal_deadline=NULL, "
                "rebuttal_submitted_at=NULL, disputed_at=NULL, ruling_id=NULL, "
                "worker_pct=NULL, ruling_summary=NULL, ruled_at=NULL WHERE task_id='t-1'"
            )
            connection.execute(
                "UPDATE board_task_contracts SET review_cutoff_us=NULL WHERE task_id='t-1'"
            )
        connection.execute("UPDATE board_tasks SET status='expired' WHERE task_id=?", (task_id,))
        connection.commit()
        _assert_clean(connection)
    finally:
        connection.close()
