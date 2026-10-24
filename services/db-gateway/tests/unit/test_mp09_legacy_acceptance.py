"""MP-09 L1 open-V0 legacy acceptance observation integration contract."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import parse_task_operation_request
from db_gateway_service.task_time import epoch_us_to_utc_text
from tests.conftest import make_event
from tests.fakes.mp09_acceptance_binding_fixture import prepare_native_acceptance
from tests.fakes.mp09_legacy_continuation_fixture import (
    ACCEPT_OPERATION_ID,
    ACCEPTANCE_CUTOFF_US,
    ACCEPTED_US,
    BID_AMOUNT,
    BID_ID,
    BID_US,
    BIDDING_CUTOFF_US,
    BOARD_KID,
    POSTER_ID,
    TASK_ID,
    WORKER_ID,
    LegacyContinuationFixture,
    LegacyReplayReadGuard,
    canonical_observation,
    claim_acceptance,
    expected_verified_observation,
    failpoint_writer,
    full_snapshot,
    open_legacy_cutover_fixture,
    open_preserved_v1_cutover_fixture,
    prepare_accept_request,
    prepare_legacy_acceptance,
    submit_real_bid,
)
from tests.fakes.native_publication_fixture import publish_native_task

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_RESULT_KEYS = {
    "task_id",
    "command",
    "status",
    "lifecycle_version",
    "bid_id",
    "worker_id",
    "reward",
    "bid_amount",
    "accepted_binding_kind",
    "accepted_binding_sha256",
    "event_id",
}


def _assert_exact_acceptance(
    fixture: LegacyContinuationFixture,
    envelope: dict[str, Any],
    expected: dict[str, object],
    canonical: str,
    digest: str,
    *,
    accepted_us: int = ACCEPTED_US,
) -> None:
    task = fixture.connection.execute(
        "SELECT status,worker_id,accepted_bid_id,accepted_at,execution_deadline FROM board_tasks "
        "WHERE task_id=?",
        (TASK_ID,),
    ).fetchone()
    timing = fixture.connection.execute(
        "SELECT lifecycle_version,execution_cutoff_us,accepted_reward,accepted_bid_amount "
        "FROM board_task_contracts WHERE task_id=?",
        (TASK_ID,),
    ).fetchone()
    observation = fixture.connection.execute(
        "SELECT observation_status,canonical_observation_json,observation_sha256,observed_at_us "
        "FROM board_legacy_artifact_observations WHERE task_id=?",
        (TASK_ID,),
    ).fetchone()
    contract = fixture.connection.execute(
        "SELECT artifact_contract_version,input_provenance,accepted_binding_kind,"
        "legacy_acceptance_observation_sha256,legacy_acceptance_observation_status,"
        "source_publication_request_sha256 FROM board_task_artifact_contracts WHERE task_id=?",
        (TASK_ID,),
    ).fetchone()
    assert task is not None and tuple(task) == (
        "accepted",
        WORKER_ID,
        BID_ID,
        expected["accepted_at"],
        expected["execution_cutoff_at"],
    )
    assert timing is not None and tuple(timing) == (
        1,
        expected["execution_cutoff_us"],
        100,
        BID_AMOUNT,
    )
    assert observation is not None and tuple(observation) == (
        "verified",
        canonical,
        digest,
        accepted_us,
    )
    assert contract is not None and tuple(contract) == (
        0,
        "unknown_pre_v6",
        "legacy_accepted_continuation",
        digest,
        "verified",
        fixture.source_publication_request_sha256,
    )
    events = fixture.connection.execute(
        "SELECT event_id,event_source,event_type,timestamp,task_id,agent_id,summary,payload "
        "FROM events WHERE event_type='task.accepted' AND task_id=?",
        (TASK_ID,),
    ).fetchall()
    assert len(events) == 1 and type(events[0][0]) is int and int(events[0][0]) > 0
    event_id = int(events[0][0])
    assert tuple(events[0][1:]) == (
        "board",
        "task.accepted",
        epoch_us_to_utc_text(accepted_us),
        TASK_ID,
        POSTER_ID,
        "Task operation completed: accept_bid",
        json.dumps(
            {"command": "accept_bid", "operation_id": ACCEPT_OPERATION_ID},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    expected_result = {
        "task_id": TASK_ID,
        "command": "accept_bid",
        "status": "accepted",
        "lifecycle_version": 1,
        "bid_id": BID_ID,
        "worker_id": WORKER_ID,
        "reward": 100,
        "bid_amount": BID_AMOUNT,
        "accepted_binding_kind": "legacy_accepted_continuation",
        "accepted_binding_sha256": digest,
        "event_id": event_id,
    }
    result = envelope["result"]
    assert isinstance(result, dict) and set(result) == _RESULT_KEYS
    assert result == expected_result
    assert envelope["protocol_version"] == 1
    stored = fixture.connection.execute(
        "SELECT operation_id,operation_contract_version,task_id,command,canonical_request_json,"
        "request_sha256,caller_kid,actor_kind,actor_id,source_status,"
        "source_lifecycle_version,target_status,target_lifecycle_version,operation_state,"
        "effect_kind,effect_state,effect_operation_id,effect_result_reference,"
        "effect_intent_json,claimed_at_us,completed_at_us,event_id,result_json,error_json "
        "FROM board_task_operations WHERE operation_id=?",
        (ACCEPT_OPERATION_ID,),
    ).fetchone()
    assert fixture.accept_request is not None and stored is not None
    assert tuple(stored) == (
        ACCEPT_OPERATION_ID,
        1,
        TASK_ID,
        "accept_bid",
        fixture.accept_request.canonical_json,
        fixture.accept_request.request_sha256,
        BOARD_KID,
        "agent",
        POSTER_ID,
        "open",
        0,
        "accepted",
        1,
        "completed",
        "none",
        "none",
        None,
        None,
        None,
        accepted_us,
        accepted_us,
        event_id,
        json.dumps(expected_result, sort_keys=True, separators=(",", ":")),
        None,
    )
    assert envelope == {
        "protocol_version": 1,
        "operation_id": ACCEPT_OPERATION_ID,
        "task_id": TASK_ID,
        "command": "accept_bid",
        "request_sha256": fixture.accept_request.request_sha256,
        "operation_state": "completed",
        "source_status": "open",
        "source_lifecycle_version": 0,
        "target_status": "accepted",
        "target_lifecycle_version": 1,
        "effect_kind": "none",
        "effect_state": "none",
        "effect_operation_id": None,
        "effect_result_reference": None,
        "effect_intent": None,
        "event_id": event_id,
        "result": expected_result,
    }
    assert (
        fixture.connection.execute("SELECT COUNT(*) FROM board_artifact_manifests").fetchone()[0]
        == 0
    )
    assert fixture.connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("provenance", ["none", "preserved_v1"])
def test_real_open_v0_acceptance_persists_exact_verified_observation(
    tmp_path: Path,
    provenance: str,
) -> None:
    if provenance == "preserved_v1":
        fixture = open_preserved_v1_cutover_fixture(tmp_path / "success-v1.sqlite3")
        submit_real_bid(fixture)
        prepare_accept_request(fixture)
    else:
        fixture = prepare_legacy_acceptance(tmp_path / "success-none.sqlite3")
    try:
        expected = expected_verified_observation(fixture)
        if provenance == "preserved_v1":
            persisted_deadline = fixture.connection.execute(
                "SELECT bidding_deadline FROM board_tasks WHERE task_id=?", (TASK_ID,)
            ).fetchone()
            assert persisted_deadline is not None
            assert persisted_deadline[0] == "2099-01-02T00:01:00.000000Z"
            expected["bidding_deadline"] = "2099-01-02T00:01:00.000000Z"
        assert len(expected) == 32
        canonical, digest = canonical_observation(expected)
        accepted = claim_acceptance(fixture)
        _assert_exact_acceptance(fixture, accepted, expected, canonical, digest)
        if fixture.preserved_v1_publication is not None:
            current = fixture.connection.execute(
                "SELECT operation_id,publication_contract_version,task_id,"
                "canonical_request_json,request_sha256,caller_kid,actor_id,lock_operation_id,"
                "state,admitted_at_us,escrow_id,lock_request_sha256,lock_receipt_event_id,"
                "published_at_us,creation_event_id,result_json,error_json "
                "FROM board_task_publications WHERE task_id=?",
                (TASK_ID,),
            ).fetchone()
            assert current is not None and tuple(current) == fixture.preserved_v1_publication
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "hash_without_row",
        "v1_missing_hash",
        "v1_hash_mismatch",
        "v1_not_published",
        "v1_actor_mismatch",
    ],
)
def test_publication_provenance_partition_rejects_contradictions(
    tmp_path: Path,
    mutation: str,
) -> None:
    if mutation == "hash_without_row":
        fixture = prepare_legacy_acceptance(
            tmp_path / "hash-without-row.sqlite3",
            source_publication_request_sha256="f" * 64,
        )
    else:
        fixture = open_preserved_v1_cutover_fixture(tmp_path / f"{mutation}.sqlite3")
        submit_real_bid(fixture)
        prepare_accept_request(fixture)
        if mutation == "v1_missing_hash":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET "
                "source_publication_request_sha256=NULL WHERE task_id=?",
                (TASK_ID,),
            )
        elif mutation == "v1_hash_mismatch":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET "
                "source_publication_request_sha256=? WHERE task_id=?",
                ("f" * 64, TASK_ID),
            )
        elif mutation == "v1_not_published":
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=ON")
            fixture.connection.execute(
                "UPDATE board_task_publications SET state='awaiting_lock' WHERE task_id=?",
                (TASK_ID,),
            )
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
            assert fixture.connection.execute("PRAGMA ignore_check_constraints").fetchone()[0] == 0
        else:
            assert mutation == "v1_actor_mismatch"
            fixture.connection.execute(
                "UPDATE board_task_publications SET actor_id=? WHERE task_id=?",
                (WORKER_ID, TASK_ID),
            )
        fixture.connection.commit()
    try:
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "legacy_observation_invalid",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_published_v2_history_cannot_be_reclassified_as_version_zero(tmp_path: Path) -> None:
    native = prepare_native_acceptance(tmp_path / "v2-downgrade.sqlite3")
    try:
        native.connection.execute(
            "UPDATE board_task_artifact_contracts SET artifact_contract_version=0,"
            "input_provenance='unknown_pre_v6',accepted_binding_kind=NULL,"
            "base_request_sha256=NULL,input_manifest_id=NULL,input_manifest_sha256=NULL,"
            "publication_contract_sha256=NULL,accepted_publication_contract_sha256=NULL,"
            "legacy_acceptance_observation_sha256=NULL,"
            "legacy_acceptance_observation_status=NULL,"
            "source_publication_request_sha256=NULL,publication_operation_id=NULL,"
            "input_manifest_kind=NULL,input_actor_id=NULL WHERE task_id=?",
            (TASK_ID.replace("65000000", "60000000"),),
        )
        native.connection.commit()
        before = full_snapshot(native.connection)
        with pytest.raises(ServiceError) as raised:
            native.operation_writer.claim_operation(native.accept_request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "legacy_observation_invalid",
        )
        assert full_snapshot(native.connection) == before
    finally:
        native.close()


def test_exact_replay_returns_one_stored_observation_event_and_operation(tmp_path: Path) -> None:
    fixture = prepare_legacy_acceptance(tmp_path / "replay.sqlite3")
    try:
        original = claim_acceptance(fixture)
        fixture.connection.execute(
            "UPDATE board_tasks SET status='expired' WHERE task_id=?",
            (TASK_ID,),
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with LegacyReplayReadGuard(fixture.connection) as guard:
            replay = claim_acceptance(fixture)
        assert replay == original
        assert guard.reads[:2] == ["board_task_publications", "board_task_operations"]
        assert full_snapshot(fixture.connection) == before
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM board_legacy_artifact_observations"
            ).fetchone()[0]
            == 1
        )
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='task.accepted'"
            ).fetchone()[0]
            == 1
        )
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM board_task_operations WHERE operation_id=?",
                (ACCEPT_OPERATION_ID,),
            ).fetchone()[0]
            == 1
        )
    finally:
        fixture.close()


def test_changed_replay_conflicts_without_mutation(tmp_path: Path) -> None:
    fixture = prepare_legacy_acceptance(tmp_path / "changed-replay.sqlite3")
    try:
        claim_acceptance(fixture)
        changed = prepare_accept_request(
            fixture,
            operation_id=ACCEPT_OPERATION_ID,
            overrides={"bid_amount": BID_AMOUNT - 1},
        )
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture, request=changed)
        assert (raised.value.status_code, raised.value.error) == (409, "operation_id_conflict")
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_configured_caller_failure_precedes_legacy_evidence_reads(tmp_path: Path) -> None:
    fixture = prepare_legacy_acceptance(tmp_path / "caller.sqlite3")
    try:
        assert fixture.accept_request is not None
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            fixture.operation_writer.claim_operation(fixture.accept_request, "other-caller")
        assert (raised.value.status_code, raised.value.error) == (
            403,
            "operation_caller_mismatch",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("mutation", "expected_status", "expected_error"),
    [
        ("missing_task", 404, "task_not_found"),
        ("missing_contract", 500, "task_contract_missing"),
        ("missing_bid", 404, "bid_not_found"),
        ("wrong_actor", 403, "forbidden"),
        ("wrong_status", 409, "invalid_status"),
        ("wrong_version", 409, "task_version_conflict"),
        ("selected_bid", 409, "payload_mismatch"),
    ],
)
def test_existing_acceptance_preconditions_remain_first(
    tmp_path: Path,
    mutation: str,
    expected_status: int,
    expected_error: str,
) -> None:
    fixture = prepare_legacy_acceptance(tmp_path / f"precedence-{mutation}.sqlite3")
    try:
        assert fixture.accept_request is not None
        request = fixture.accept_request
        if mutation == "missing_task":
            payload = {
                "protocol_version": 1,
                "operation_id": ACCEPT_OPERATION_ID,
                "task_id": "t-65000000-0000-4000-8000-000000000099",
                "command": "accept_bid",
                "expected_status": "open",
                "expected_lifecycle_version": 0,
                "actor_kind": "agent",
                "actor_id": POSTER_ID,
                "arguments": dict(request.arguments),
            }
            request = parse_task_operation_request(payload)
        elif mutation == "missing_contract":
            fixture.connection.execute(
                "DELETE FROM board_task_contracts WHERE task_id=?", (TASK_ID,)
            )
            fixture.connection.commit()
        elif mutation == "missing_bid":
            fixture.connection.execute("DELETE FROM board_bids WHERE bid_id=?", (BID_ID,))
            fixture.connection.commit()
        elif mutation == "wrong_actor":
            payload = json.loads(request.canonical_json)
            assert isinstance(payload, dict)
            payload["operation_id"] = request.operation_id
            payload["actor_id"] = WORKER_ID
            request = parse_task_operation_request(payload)
        elif mutation == "wrong_status":
            fixture.connection.execute(
                "UPDATE board_tasks SET status='accepted' WHERE task_id=?", (TASK_ID,)
            )
            fixture.connection.commit()
        elif mutation == "wrong_version":
            fixture.connection.execute(
                "UPDATE board_task_contracts SET lifecycle_version=1 WHERE task_id=?", (TASK_ID,)
            )
            fixture.connection.commit()
        else:
            request = prepare_accept_request(fixture, overrides={"bid_amount": BID_AMOUNT - 1})
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture, request=request)
        assert (raised.value.status_code, raised.value.error) == (
            expected_status,
            expected_error,
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_real_bid_half_open_bidding_cutoff(tmp_path: Path, offset: int) -> None:
    fixture = open_legacy_cutover_fixture(tmp_path / f"bid-cutoff-{offset}.sqlite3")
    try:
        at_us = BIDDING_CUTOFF_US + offset
        if offset < 0:
            submitted = submit_real_bid(fixture, at_us=at_us)
            assert submitted["operation_state"] == "completed"
        else:
            before = full_snapshot(fixture.connection)
            with pytest.raises(ServiceError) as raised:
                submit_real_bid(fixture, at_us=at_us)
            assert (raised.value.status_code, raised.value.error) == (409, "cutoff_reached")
            assert full_snapshot(fixture.connection) == before
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM board_legacy_artifact_observations"
            ).fetchone()[0]
            == 0
        )
    finally:
        fixture.close()


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_real_acceptance_half_open_acceptance_cutoff(tmp_path: Path, offset: int) -> None:
    fixture = open_legacy_cutover_fixture(tmp_path / f"accept-cutoff-{offset}.sqlite3")
    try:
        submit_real_bid(fixture)
        request = prepare_accept_request(fixture, at_us=ACCEPTANCE_CUTOFF_US + offset)
        if offset < 0:
            result = claim_acceptance(fixture, request=request)
            expected = expected_verified_observation(
                fixture,
                accepted_us=ACCEPTANCE_CUTOFF_US + offset,
            )
            canonical, digest = canonical_observation(expected)
            _assert_exact_acceptance(
                fixture,
                result,
                expected,
                canonical,
                digest,
                accepted_us=ACCEPTANCE_CUTOFF_US + offset,
            )
        else:
            before = full_snapshot(fixture.connection)
            with pytest.raises(ServiceError) as raised:
                claim_acceptance(fixture, request=request)
            assert (raised.value.status_code, raised.value.error) == (409, "cutoff_reached")
            assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("bid_us", "accepted_us", "should_succeed"),
    [(BID_US, BID_US, True), (BID_US, BID_US - 1, False)],
)
def test_bid_and_acceptance_chronology(
    tmp_path: Path,
    bid_us: int,
    accepted_us: int,
    should_succeed: bool,
) -> None:
    fixture = prepare_legacy_acceptance(
        tmp_path / f"chronology-{should_succeed}.sqlite3",
        bid_us=bid_us,
        accepted_us=accepted_us,
    )
    try:
        if should_succeed:
            result = claim_acceptance(fixture)
            expected = expected_verified_observation(
                fixture,
                bid_us=bid_us,
                accepted_us=accepted_us,
            )
            canonical, digest = canonical_observation(expected)
            _assert_exact_acceptance(
                fixture,
                result,
                expected,
                canonical,
                digest,
                accepted_us=accepted_us,
            )
        else:
            before = full_snapshot(fixture.connection)
            with pytest.raises(ServiceError) as raised:
                claim_acceptance(fixture)
            assert (raised.value.status_code, raised.value.error) == (
                500,
                "legacy_observation_invalid",
            )
            assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def _mutate_source_contradiction(
    fixture: LegacyContinuationFixture,
    mutation: str,
) -> None:
    if mutation == "escrow_payer":
        fixture.connection.execute(
            "UPDATE bank_escrow SET payer_account_id=? WHERE escrow_id='escrow-mp09-legacy'",
            (WORKER_ID,),
        )
    elif mutation == "escrow_amount":
        fixture.connection.execute(
            "UPDATE bank_escrow SET amount=101 WHERE escrow_id='escrow-mp09-legacy'"
        )
    elif mutation == "escrow_status":
        fixture.connection.execute(
            "UPDATE bank_escrow SET status='released' WHERE escrow_id='escrow-mp09-legacy'"
        )
    elif mutation == "created_after_bid":
        fixture.connection.execute(
            "UPDATE board_tasks SET created_at=? WHERE task_id=?",
            (epoch_us_to_utc_text(BID_US + 1), TASK_ID),
        )
    elif mutation == "bid_at_cutoff":
        fixture.connection.execute(
            "UPDATE board_bids SET submitted_at=? WHERE bid_id=?",
            (epoch_us_to_utc_text(BIDDING_CUTOFF_US), BID_ID),
        )
    else:
        assert mutation == "bidding_timing"
        fixture.connection.execute(
            "UPDATE board_tasks SET bidding_deadline=? WHERE task_id=?",
            (epoch_us_to_utc_text(BIDDING_CUTOFF_US + 1), TASK_ID),
        )


@pytest.mark.parametrize(
    ("mutation", "expected_status", "expected_error"),
    [
        ("missing_escrow", 409, "legacy_continuation_unavailable"),
        ("bad_bidding_deadline", 500, "legacy_observation_invalid"),
        ("wrong_reward", 409, "payload_mismatch"),
        ("escrow_payer", 500, "legacy_observation_invalid"),
        ("escrow_amount", 500, "legacy_observation_invalid"),
        ("escrow_status", 500, "legacy_observation_invalid"),
        ("created_after_bid", 500, "legacy_observation_invalid"),
        ("bid_at_cutoff", 500, "legacy_observation_invalid"),
        ("bidding_timing", 500, "legacy_observation_invalid"),
        ("prebound", 500, "legacy_observation_invalid"),
        ("stale_observation", 500, "legacy_observation_conflict"),
        ("wrong_provenance", 500, "legacy_observation_invalid"),
        ("wrong_contract_version", 500, "artifact_contract_invalid"),
        ("native_cross_kind", 500, "legacy_observation_invalid"),
    ],
)
def test_legacy_evidence_failures_precede_all_acceptance_writes(
    tmp_path: Path,
    mutation: str,
    expected_status: int,
    expected_error: str,
) -> None:
    fixture = prepare_legacy_acceptance(tmp_path / f"failure-{mutation}.sqlite3")
    try:
        if mutation == "missing_escrow":
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA foreign_keys=OFF")
            fixture.connection.execute(
                "DELETE FROM bank_escrow WHERE escrow_id='escrow-mp09-legacy'"
            )
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA foreign_keys=ON")
            assert fixture.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        elif mutation == "bad_bidding_deadline":
            fixture.connection.execute(
                "UPDATE board_tasks SET bidding_deadline='not-a-time' WHERE task_id=?", (TASK_ID,)
            )
        elif mutation == "wrong_reward":
            fixture.connection.execute(
                "UPDATE board_tasks SET reward=101 WHERE task_id=?", (TASK_ID,)
            )
        elif mutation in {
            "escrow_payer",
            "escrow_amount",
            "escrow_status",
            "created_after_bid",
            "bid_at_cutoff",
            "bidding_timing",
        }:
            _mutate_source_contradiction(fixture, mutation)
        elif mutation == "prebound":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET "
                "accepted_binding_kind='legacy_accepted_continuation',"
                "legacy_acceptance_observation_sha256=?,"
                "legacy_acceptance_observation_status='verified' WHERE task_id=?",
                ("f" * 64, TASK_ID),
            )
        elif mutation == "stale_observation":
            fixture.connection.execute(
                "INSERT INTO board_legacy_artifact_observations VALUES (?,'verified','{}',?,?)",
                (TASK_ID, "f" * 64, ACCEPTED_US),
            )
        else:
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=ON")
            if mutation == "wrong_provenance":
                fixture.connection.execute(
                    "UPDATE board_task_artifact_contracts SET input_provenance='native_v6' "
                    "WHERE task_id=?",
                    (TASK_ID,),
                )
            elif mutation == "wrong_contract_version":
                fixture.connection.execute(
                    "UPDATE board_task_artifact_contracts SET artifact_contract_version=1 "
                    "WHERE task_id=?",
                    (TASK_ID,),
                )
            else:
                fixture.connection.execute(
                    "UPDATE board_task_artifact_contracts SET "
                    "accepted_binding_kind='publication_contract',"
                    "accepted_publication_contract_sha256=? WHERE task_id=?",
                    ("f" * 64, TASK_ID),
                )
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
            assert fixture.connection.execute("PRAGMA ignore_check_constraints").fetchone()[0] == 0
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture)
        assert (raised.value.status_code, raised.value.error) == (expected_status, expected_error)
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("target", "mode", "expected_status", "expected_error"),
    [
        ("task_update", "zero_row_after", 409, "task_version_conflict"),
        ("timing_update", "zero_row_after", 409, "task_version_conflict"),
        ("observation_insert", "zero_row_after", 500, "task_operation_persistence_failed"),
        ("contract_update", "zero_row_after", 409, "task_version_conflict"),
        ("event_insert", "zero_row_after", 500, "task_operation_persistence_failed"),
        ("operation_insert", "zero_row_after", 500, "task_operation_persistence_failed"),
        ("task_update", "operational_before", 500, "task_operation_persistence_failed"),
        ("timing_update", "operational_before", 500, "task_operation_persistence_failed"),
        ("observation_insert", "operational_before", 500, "task_operation_persistence_failed"),
        ("contract_update", "operational_before", 500, "task_operation_persistence_failed"),
        ("event_insert", "operational_before", 500, "task_operation_persistence_failed"),
        ("operation_insert", "operational_before", 500, "task_operation_persistence_failed"),
        ("task_update", "runtime_before", 500, "task_operation_persistence_failed"),
        ("timing_update", "runtime_before", 500, "task_operation_persistence_failed"),
        ("observation_insert", "runtime_before", 500, "task_operation_persistence_failed"),
        ("contract_update", "runtime_before", 500, "task_operation_persistence_failed"),
        ("event_insert", "runtime_before", 500, "task_operation_persistence_failed"),
        ("operation_insert", "runtime_before", 500, "task_operation_persistence_failed"),
    ],
)
def test_six_acceptance_write_failures_roll_back_all_facts(
    tmp_path: Path,
    target: Any,
    mode: Any,
    expected_status: int,
    expected_error: str,
) -> None:
    fixture = prepare_legacy_acceptance(tmp_path / f"atomic-{target}-{mode}.sqlite3")
    try:
        writer = failpoint_writer(fixture, target=target, mode=mode)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture, writer=writer)
        assert (raised.value.status_code, raised.value.error) == (expected_status, expected_error)
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_native_acceptance_control_creates_no_legacy_observation(tmp_path: Path) -> None:
    native = prepare_native_acceptance(tmp_path / "native-control.sqlite3")
    try:
        accepted = native.operation_writer.claim_operation(native.accept_request, BOARD_KID)
        assert accepted["result"]["accepted_binding_kind"] == "publication_contract"
        assert (
            native.connection.execute(
                "SELECT COUNT(*) FROM board_legacy_artifact_observations"
            ).fetchone()[0]
            == 0
        )
    finally:
        native.close()


def test_current_v5_acceptance_preserves_historical_result_and_bank_receipts(
    tmp_path: Path,
) -> None:
    writer = DbWriter(
        db_path=str(tmp_path / "v5-control.sqlite3"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V5),
        current_task_board_caller_kid=BOARD_KID,
    )
    poster = f"poster-{uuid4()}"
    worker = f"worker-{uuid4()}"
    task = f"t-{uuid4()}"
    escrow = f"esc-{uuid4()}"
    try:
        for actor in (poster, worker):
            writer.register_agent(
                {
                    "agent_id": actor,
                    "name": actor,
                    "public_key": f"ed25519:{uuid4()}",
                    "registered_at": "2099-01-01T00:00:00Z",
                    "event": make_event(agent_id=actor),
                }
            )
        writer.create_account(
            {
                "operation_id": f"op-{uuid4()}",
                "account_id": poster,
                "balance": 100,
                "created_at": "2099-01-01T00:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": 100,
                    "reference": "initial_balance",
                    "timestamp": "2099-01-01T00:00:00Z",
                },
                "event": make_event(source="central-bank", event_type="account.created"),
            }
        )
        publish_native_task(
            writer,
            caller_kid=BOARD_KID,
            clock_us=4_070_908_802_000_000,
            task_id=task,
            poster_id=poster,
            escrow_id=escrow,
            title="V5 legacy control",
            spec="Preserve acceptance bytes.",
            reward=100,
            bidding_deadline_seconds=60,
            acceptance_window_seconds=60,
            execution_deadline_seconds=3600,
            review_deadline_seconds=60,
            lock_timestamp="2099-01-01T00:00:01Z",
        )

        def v5_clock() -> int:
            return 4_070_908_803_000_000

        operations = TaskOperationWriter(
            writer.connection,
            clock_us=v5_clock,
            deadline_actor_id="system-task-board-deadline",
            platform_public_key="ed25519:test-platform-authority",
            current_task_board_caller_kid=BOARD_KID,
        )
        bid_id = f"bid-{uuid4()}"
        operations.claim_operation(
            parse_task_operation_request(
                {
                    "protocol_version": 1,
                    "operation_id": f"op-{uuid4()}",
                    "task_id": task,
                    "command": "submit_bid",
                    "expected_status": "open",
                    "expected_lifecycle_version": 0,
                    "actor_kind": "agent",
                    "actor_id": worker,
                    "arguments": {"bid_id": bid_id, "bidder_id": worker, "amount": 90},
                }
            ),
            BOARD_KID,
        )
        request = parse_task_operation_request(
            {
                "protocol_version": 1,
                "operation_id": f"op-{uuid4()}",
                "task_id": task,
                "command": "accept_bid",
                "expected_status": "open",
                "expected_lifecycle_version": 0,
                "actor_kind": "agent",
                "actor_id": poster,
                "arguments": {
                    "bid_id": bid_id,
                    "worker_id": worker,
                    "escrow_id": escrow,
                    "reward": 100,
                    "bid_amount": 90,
                },
            }
        )
        bank_before = tuple(
            tuple(row)
            for row in writer.connection.execute(
                "SELECT * FROM bank_operation_receipts ORDER BY operation_id"
            )
        )
        accepted = operations.claim_operation(request, BOARD_KID)
        result = accepted["result"]
        assert isinstance(result, dict)
        assert "accepted_binding_kind" not in result
        assert "accepted_binding_sha256" not in result
        stored = writer.connection.execute(
            "SELECT canonical_request_json,request_sha256,result_json,error_json "
            "FROM board_task_operations WHERE operation_id=?",
            (request.operation_id,),
        ).fetchone()
        assert stored is not None and tuple(stored) == (
            request.canonical_json,
            request.request_sha256,
            json.dumps(result, sort_keys=True, separators=(",", ":")),
            None,
        )
        assert bank_before == tuple(
            tuple(row)
            for row in writer.connection.execute(
                "SELECT * FROM bank_operation_receipts ORDER BY operation_id"
            )
        )
    finally:
        writer.close()
