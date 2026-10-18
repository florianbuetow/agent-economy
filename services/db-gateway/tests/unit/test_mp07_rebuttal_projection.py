"""MP-07 Court rebuttals atomically project their timestamp onto the task."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.task_contract_audit import audit_task_contracts
from tests.conftest import make_event
from tests.unit import test_mp07_native_write_boundary as boundary

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit

_SUBMITTED_AT = "2099-01-01T00:00:07Z"


@dataclass(frozen=True)
class _Dispute:
    task_id: str
    poster_id: str
    worker_id: str
    claim_id: str
    lifecycle_version: int


def _snapshot(writer: DbWriter) -> tuple[str, ...]:
    return tuple(writer.connection.iterdump())


def _seed_dispute(writer: DbWriter) -> _Dispute:
    """Create coherent disputed state directly as fixture data, not a public writer path."""
    graph = boundary._seed_open_task(writer)
    bid_id = f"bid-{uuid4()}"
    claim_id = f"clm-{uuid4()}"
    writer.connection.execute(
        "INSERT INTO board_bids (bid_id,task_id,bidder_id,proposal,amount,submitted_at) "
        "VALUES (?,?,?,?,?,?)",
        (bid_id, graph.task_id, graph.worker_id, "Fixture bid", 90, "2099-01-01T00:00:01Z"),
    )
    writer.connection.execute(
        "UPDATE board_tasks SET status='disputed',bid_count=1,worker_id=?,accepted_bid_id=?,"
        "accepted_at='2099-01-01T00:00:02Z',execution_deadline='2099-01-02T00:00:02Z',"
        "submitted_at='2099-01-01T00:00:03Z',review_deadline='2099-01-02T00:00:03Z',"
        "dispute_reason='Fixture dispute',dispute_id=?,"
        "rebuttal_deadline='2099-01-02T00:00:04Z',disputed_at='2099-01-01T00:00:04Z' "
        "WHERE task_id=?",
        (graph.worker_id, bid_id, claim_id, graph.task_id),
    )
    writer.connection.execute(
        "UPDATE board_task_contracts SET lifecycle_version=3,"
        "execution_cutoff_us=4070995202000000,review_cutoff_us=4070995203000000,"
        "accepted_reward=100,accepted_bid_amount=90 WHERE task_id=?",
        (graph.task_id,),
    )
    writer.connection.execute(
        "INSERT INTO court_claims "
        "(claim_id,task_id,claimant_id,respondent_id,reason,status,rebuttal_deadline,filed_at) "
        "VALUES (?,?,?,?,?,'filed','2099-01-02T00:00:04Z','2099-01-01T00:00:04Z')",
        (claim_id, graph.task_id, graph.poster_id, graph.worker_id, "Fixture dispute"),
    )
    writer.connection.commit()
    return _Dispute(graph.task_id, graph.poster_id, graph.worker_id, claim_id, 3)


def _rebuttal(dispute: _Dispute) -> dict[str, object]:
    return {
        "rebuttal_id": f"reb-{uuid4()}",
        "claim_id": dispute.claim_id,
        "agent_id": dispute.worker_id,
        "content": "The delivery follows the task specification.",
        "submitted_at": _SUBMITTED_AT,
        "claim_status_update": "rebuttal",
        "event": make_event(
            source="court",
            event_type="rebuttal.submitted",
            task_id=dispute.task_id,
            agent_id=dispute.worker_id,
        ),
    }


def _submit(writer: DbWriter, data: dict[str, object]) -> dict[str, object]:
    return writer.submit_rebuttal(data, {"status": "filed"})


def _assert_rejected_without_effects(
    writer: DbWriter,
    data: dict[str, object],
    status_code: int,
    error: str,
) -> ServiceError:
    before = _snapshot(writer)
    with pytest.raises(ServiceError) as raised:
        _submit(writer, data)
    assert raised.value.status_code == status_code
    assert raised.value.error == error
    assert _snapshot(writer) == before
    assert writer.connection.in_transaction is False
    return raised.value


def test_rebuttal_projects_exact_timestamp_with_one_event_and_unchanged_version(
    db_writer: DbWriter,
) -> None:
    dispute = _seed_dispute(db_writer)
    data = _rebuttal(dispute)
    event_cursor = int(
        db_writer.connection.execute("SELECT COALESCE(MAX(event_id), 0) FROM events").fetchone()[0]
    )

    result = _submit(db_writer, data)

    rebuttal = db_writer.connection.execute(
        "SELECT rebuttal_id,claim_id,agent_id,content,submitted_at FROM court_rebuttals "
        "WHERE rebuttal_id=?",
        (data["rebuttal_id"],),
    ).fetchone()
    assert rebuttal is not None
    assert tuple(rebuttal) == (
        data["rebuttal_id"],
        dispute.claim_id,
        dispute.worker_id,
        data["content"],
        _SUBMITTED_AT,
    )
    assert (
        db_writer.connection.execute(
            "SELECT status FROM court_claims WHERE claim_id=?", (dispute.claim_id,)
        ).fetchone()[0]
        == "rebuttal"
    )
    assert (
        db_writer.connection.execute(
            "SELECT lifecycle_version FROM board_task_contracts WHERE task_id=?",
            (dispute.task_id,),
        ).fetchone()[0]
        == dispute.lifecycle_version
    )
    events = db_writer.connection.execute(
        "SELECT event_id,event_source,event_type,task_id,agent_id FROM events "
        "WHERE event_id>? ORDER BY event_id",
        (event_cursor,),
    ).fetchall()
    assert len(events) == 1
    assert tuple(events[0])[1:] == (
        "court",
        "rebuttal.submitted",
        dispute.task_id,
        dispute.worker_id,
    )
    assert result == {"rebuttal_id": data["rebuttal_id"], "event_id": int(events[0][0])}
    assert (
        db_writer.connection.execute(
            "SELECT rebuttal_submitted_at FROM board_tasks WHERE task_id=?",
            (dispute.task_id,),
        ).fetchone()[0]
        == _SUBMITTED_AT
    )
    assert db_writer.connection.in_transaction is False


def _insert_active_operation(writer: DbWriter, dispute: _Dispute, operation_state: str) -> str:
    operation_id = f"op-{uuid4()}"
    effect_state = "pending" if operation_state == "claimed" else "conflict"
    error_json = None
    if operation_state == "blocked":
        error_json = '{"details":{},"error":"effect_evidence_conflict","message":"blocked"}'
    writer.connection.execute(
        "INSERT INTO board_task_operations "
        "(operation_id,operation_contract_version,task_id,command,canonical_request_json,"
        "request_sha256,caller_kid,actor_kind,actor_id,source_status,"
        "source_lifecycle_version,target_status,target_lifecycle_version,operation_state,"
        "effect_kind,effect_state,effect_operation_id,effect_result_reference,"
        "effect_intent_json,claimed_at_us,completed_at_us,event_id,result_json,error_json) "
        "VALUES (?,1,?,'record_ruling','{}',?,'gateway-task-board','platform','platform',"
        "'disputed',3,'ruled',4,?,'bank_release',?,?,NULL,'{}',1,NULL,NULL,NULL,?)",
        (
            operation_id,
            dispute.task_id,
            "0" * 64,
            operation_state,
            effect_state,
            operation_id,
            error_json,
        ),
    )
    writer.connection.commit()
    return operation_id


def test_rebuttal_event_uses_persisted_claim_context(db_writer: DbWriter) -> None:
    dispute = _seed_dispute(db_writer)
    data = _rebuttal(dispute)
    event = data["event"]
    assert isinstance(event, dict)
    event.update(
        {
            "event_source": "bank",
            "event_type": "account.created",
            "task_id": "t-unrelated",
            "agent_id": dispute.poster_id,
            "timestamp": "2098-12-01T00:00:00Z",
        }
    )
    result = _submit(db_writer, data)
    recorded = db_writer.connection.execute(
        "SELECT event_source,event_type,task_id,agent_id,timestamp FROM events WHERE event_id=?",
        (result["event_id"],),
    ).fetchone()
    assert recorded is not None
    assert tuple(recorded) == (
        "court",
        "rebuttal.submitted",
        dispute.task_id,
        dispute.worker_id,
        _SUBMITTED_AT,
    )


@pytest.mark.parametrize("operation_state", ["claimed", "blocked"])
def test_active_or_blocked_task_operation_rejects_rebuttal_before_any_write(
    db_writer: DbWriter,
    operation_state: str,
) -> None:
    dispute = _seed_dispute(db_writer)
    operation_id = _insert_active_operation(db_writer, dispute, operation_state)

    raised = _assert_rejected_without_effects(
        db_writer, _rebuttal(dispute), 409, "transition_pending"
    )
    assert raised.details == {"winning_operation_id": operation_id}


def test_unresolved_legacy_observation_rejects_rebuttal_before_any_write(
    db_writer: DbWriter,
) -> None:
    dispute = _seed_dispute(db_writer)
    observation_id = f"obs-{uuid4()}"
    task_row = db_writer.connection.execute(
        "SELECT escrow_id,accepted_bid_id FROM board_tasks WHERE task_id=?",
        (dispute.task_id,),
    ).fetchone()
    assert task_row is not None
    facts = {
        "task_id": dispute.task_id,
        "task_status": "disputed",
        "poster_id": dispute.poster_id,
        "worker_id": dispute.worker_id,
        "escrow_id": str(task_row[0]),
        "reward": 100,
        "accepted_bid_id": str(task_row[1]),
        "accepted_bid_amount": 90,
        "external_reference": dispute.claim_id,
        "claimant_id": dispute.poster_id,
        "respondent_id": dispute.worker_id,
        "reason": "Fixture dispute",
        "claim_status": "filed",
        "filed_at": "2099-01-01T00:00:04Z",
        "rebuttal_deadline": "2099-01-02T00:00:04Z",
    }
    observed_json = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    observed_hash = hashlib.sha256(observed_json.encode()).hexdigest()
    db_writer.connection.execute(
        "INSERT INTO board_legacy_effect_observations "
        "(observation_id,task_id,observation_kind,observed_fact_json,"
        "observed_fact_sha256,external_reference,effect_state,target_outcome_hint,unresolved) "
        "VALUES (?,?,'court_claim_succeeded',?,?,?, 'succeeded','disputed',1)",
        (observation_id, dispute.task_id, observed_json, observed_hash, dispute.claim_id),
    )
    db_writer.connection.commit()
    audit = audit_task_contracts(db_writer.connection)
    assert audit.contracts_clean, audit.findings

    raised = _assert_rejected_without_effects(
        db_writer, _rebuttal(dispute), 409, "legacy_reconciliation_pending"
    )
    assert raised.details == {"observation_id": observation_id}


def test_rebuttal_requires_disputed_task_status(db_writer: DbWriter) -> None:
    dispute = _seed_dispute(db_writer)
    db_writer.connection.execute(
        "UPDATE board_tasks SET status='submitted' WHERE task_id=?", (dispute.task_id,)
    )
    db_writer.connection.commit()

    _assert_rejected_without_effects(db_writer, _rebuttal(dispute), 409, "invalid_status")


def test_rebuttal_requires_task_claim_linkage(db_writer: DbWriter) -> None:
    dispute = _seed_dispute(db_writer)
    db_writer.connection.execute(
        "UPDATE board_tasks SET dispute_id=? WHERE task_id=?",
        (f"clm-{uuid4()}", dispute.task_id),
    )
    db_writer.connection.commit()

    _assert_rejected_without_effects(db_writer, _rebuttal(dispute), 409, "payload_mismatch")


@pytest.mark.parametrize("party", ["claimant", "respondent"])
def test_rebuttal_requires_claim_parties_to_match_task(
    db_writer: DbWriter,
    party: str,
) -> None:
    dispute = _seed_dispute(db_writer)
    if party == "claimant":
        db_writer.connection.execute(
            "UPDATE court_claims SET claimant_id=? WHERE claim_id=?",
            (dispute.worker_id, dispute.claim_id),
        )
    else:
        db_writer.connection.execute(
            "UPDATE court_claims SET respondent_id=? WHERE claim_id=?",
            (dispute.poster_id, dispute.claim_id),
        )
    db_writer.connection.commit()

    _assert_rejected_without_effects(db_writer, _rebuttal(dispute), 409, "payload_mismatch")


def test_rebuttal_requires_request_agent_to_be_task_worker(db_writer: DbWriter) -> None:
    dispute = _seed_dispute(db_writer)
    data = _rebuttal(dispute)
    data["agent_id"] = dispute.poster_id

    _assert_rejected_without_effects(db_writer, data, 403, "forbidden")


def test_rebuttal_requires_persisted_claim(db_writer: DbWriter) -> None:
    dispute = _seed_dispute(db_writer)
    data = _rebuttal(dispute)
    data["claim_id"] = f"clm-{uuid4()}"

    _assert_rejected_without_effects(db_writer, data, 404, "claim_not_found")


def test_second_rebuttal_for_same_claim_is_rejected_even_with_new_id(
    db_writer: DbWriter,
) -> None:
    dispute = _seed_dispute(db_writer)
    db_writer.connection.execute(
        "INSERT INTO court_rebuttals (rebuttal_id,claim_id,agent_id,content,submitted_at) "
        "VALUES (?,?,?,?,?)",
        (f"reb-{uuid4()}", dispute.claim_id, dispute.worker_id, "Existing", _SUBMITTED_AT),
    )
    db_writer.connection.commit()

    _assert_rejected_without_effects(db_writer, _rebuttal(dispute), 409, "rebuttal_exists")


def test_conflicting_board_rebuttal_marker_fails_closed(db_writer: DbWriter) -> None:
    dispute = _seed_dispute(db_writer)
    db_writer.connection.execute(
        "UPDATE board_tasks SET rebuttal_submitted_at='2099-01-01T00:00:06Z' WHERE task_id=?",
        (dispute.task_id,),
    )
    db_writer.connection.commit()

    _assert_rejected_without_effects(
        db_writer, _rebuttal(dispute), 409, "rebuttal_projection_conflict"
    )


@pytest.mark.parametrize("fault_point", ["court_insert", "board_projection"])
def test_rebuttal_fault_rolls_back_court_projection_and_event(
    db_writer: DbWriter,
    fault_point: str,
) -> None:
    dispute = _seed_dispute(db_writer)
    if fault_point == "court_insert":
        db_writer.connection.execute(
            "CREATE TRIGGER inject_rebuttal_failure AFTER INSERT ON court_rebuttals "
            "BEGIN SELECT RAISE(ABORT, 'injected after court insert'); END"
        )
    else:
        db_writer.connection.execute(
            "CREATE TRIGGER inject_projection_failure "
            "AFTER UPDATE OF rebuttal_submitted_at ON board_tasks "
            "BEGIN SELECT RAISE(ABORT, 'injected after board projection'); END"
        )
    db_writer.connection.commit()
    before = _snapshot(db_writer)

    with pytest.raises((ServiceError, sqlite3.IntegrityError)):
        _submit(db_writer, _rebuttal(dispute))

    assert _snapshot(db_writer) == before
    assert db_writer.connection.in_transaction is False
