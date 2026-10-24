"""MP-09 L2 verified legacy continuation upload and submission contract."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.task_time import epoch_us_to_utc_text
from tests.fakes.mp09_legacy_continuation_fixture import (
    ACCEPTED_US,
    BID_ID,
    BOARD_KID,
    POSTER_ID,
    TASK_ID,
    WORKER_ID,
)
from tests.fakes.mp09_legacy_submission_fixture import (
    BODY_A,
    BODY_B,
    SUBMISSION_MANIFEST_ID,
    EvidenceReadGuard,
    canonical_json,
    failpoint_submission_writer,
    finalize_upload,
    full_snapshot,
    prepare_legacy_submission,
    ready_upload,
    reserve_upload,
    submission_request,
    upload_request,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit
_WRITE_TARGETS = (
    "manifest_update",
    "task_update",
    "timing_update",
    "event_insert",
    "operation_insert",
)
_FAILURE_CASES = [
    ("manifest_update", "zero_row_after", 409, "manifest_mismatch"),
    ("task_update", "zero_row_after", 409, "task_version_conflict"),
    ("timing_update", "zero_row_after", 409, "task_version_conflict"),
    ("event_insert", "zero_row_after", 500, "task_operation_persistence_failed"),
    ("operation_insert", "zero_row_after", 500, "task_operation_persistence_failed"),
    *(
        (target, "operational_before", 500, "task_operation_persistence_failed")
        for target in _WRITE_TARGETS
    ),
    *(
        (target, "runtime_before", 500, "task_operation_persistence_failed")
        for target in _WRITE_TARGETS
    ),
]
_ERROR_STATUS = {
    "accepted_binding_mismatch": 409,
    "artifact_contract_missing": 409,
    "forbidden": 403,
    "legacy_continuation_unavailable": 409,
    "legacy_observation_invalid": 500,
    "stale_upload_attempt": 409,
    "task_version_conflict": 409,
    "upload_operation_conflict": 409,
    "upload_pending": 409,
}


def _assert_error(raised: pytest.ExceptionInfo[ServiceError], error: str) -> None:
    assert error in _ERROR_STATUS
    assert (raised.value.status_code, raised.value.error) == (_ERROR_STATUS[error], error)


def _upload_row(fixture: Any, operation_id: str) -> tuple[Any, ...]:
    row = fixture.connection.execute(
        "SELECT upload_operation_id,request_sha256,canonical_request_json,caller_kid,"
        "resource,task_id,manifest_id,actor_id,actor_role,display_filename,evidence_format,"
        "media_type,declared_size_bytes,content_sha256,expected_lifecycle_version,"
        "accepted_binding_kind,accepted_binding_sha256,state,artifact_id,object_key,"
        "current_attempt_id,created_at_us,finalized_at_us,manifest_kind,"
        "publication_operation_id FROM board_artifact_uploads WHERE upload_operation_id=?",
        (operation_id,),
    ).fetchone()
    assert row is not None
    return tuple(row)


def _attempt_row(fixture: Any, attempt_id: str) -> tuple[Any, ...]:
    row = fixture.connection.execute(
        "SELECT attempt_id,upload_operation_id,owner_id,generation,attempt_nonce,staging_key,"
        "intended_object_key,lease_expires_at_us,absolute_upload_deadline_us,state,"
        "observed_size_bytes,observed_sha256,failure_code,created_at_us,updated_at_us "
        "FROM board_artifact_upload_attempts WHERE attempt_id=?",
        (attempt_id,),
    ).fetchone()
    assert row is not None
    return tuple(row)


def _attempt_count(fixture: Any, operation_id: str) -> tuple[int, int]:
    row = fixture.connection.execute(
        "SELECT COUNT(*),MAX(generation) FROM board_artifact_upload_attempts "
        "WHERE upload_operation_id=?",
        (operation_id,),
    ).fetchone()
    assert row is not None and type(row[0]) is int and type(row[1]) is int
    return int(row[0]), int(row[1])


def _assert_orphan_terminal_replays(
    fixture: Any,
    request: Any,
    reserved: dict[str, Any],
) -> None:
    expected_attempt_count = _attempt_count(fixture, request.upload_operation_id)
    terminal_before = full_snapshot(fixture.connection)
    terminal = reserve_upload(fixture, request)
    assert terminal == {
        "upload_operation_id": request.upload_operation_id,
        "state": "orphaned",
        "artifact_id": reserved["artifact_id"],
        "object_key": reserved["object_key"],
        "accepted_binding_kind": fixture.binding_kind,
        "accepted_binding_sha256": fixture.binding_sha256,
        "declared_size_bytes": request.declared_size_bytes,
        "content_sha256": request.content_sha256,
        "manifest_kind": "submission",
        "publication_operation_id": None,
    }
    assert "attempt" not in terminal
    assert full_snapshot(fixture.connection) == terminal_before
    assert _attempt_count(fixture, request.upload_operation_id) == expected_attempt_count

    changed = upload_request(fixture, 1, body=BODY_B)
    changed_before = full_snapshot(fixture.connection)
    with pytest.raises(ServiceError) as conflict:
        reserve_upload(fixture, changed)
    _assert_error(conflict, "upload_operation_conflict")
    assert full_snapshot(fixture.connection) == changed_before
    assert _attempt_count(fixture, request.upload_operation_id) == expected_attempt_count


@pytest.mark.parametrize("provenance", ["none", "preserved_v1"])
def test_real_l1_binding_permits_reserve_and_finalize(
    tmp_path: Path,
    provenance: str,
) -> None:
    fixture = prepare_legacy_submission(
        tmp_path / f"upload-{provenance}.sqlite3",
        provenance=provenance,
    )
    try:
        fixture.clock.now_us = ACCEPTED_US + 1_000_000
        operation_time = fixture.clock.now_us
        request = upload_request(fixture, 1, body=BODY_A)
        reserved = reserve_upload(fixture, request)
        attempt = reserved["attempt"]
        lease_expires_at_us = operation_time + 300_000_000
        absolute_deadline_us = operation_time + 900_000_000
        expected_attempt = {
            "attempt_id": attempt["attempt_id"],
            "owner_id": attempt["owner_id"],
            "generation": 1,
            "attempt_nonce": attempt["attempt_nonce"],
            "staging_key": (
                f"staging/{request.upload_operation_id}.1.{attempt['attempt_nonce']}.part"
            ),
            "lease_expires_at_us": lease_expires_at_us,
            "absolute_upload_deadline_us": absolute_deadline_us,
            "state": "active",
        }
        expected_reserved = {
            "upload_operation_id": request.upload_operation_id,
            "state": "reserved",
            "artifact_id": reserved["artifact_id"],
            "object_key": reserved["object_key"],
            "accepted_binding_kind": fixture.binding_kind,
            "accepted_binding_sha256": fixture.binding_sha256,
            "declared_size_bytes": len(BODY_A),
            "content_sha256": hashlib.sha256(BODY_A).hexdigest(),
            "manifest_kind": "submission",
            "publication_operation_id": None,
            "attempt": expected_attempt,
        }
        assert reserved == expected_reserved
        assert _upload_row(fixture, request.upload_operation_id) == (
            request.upload_operation_id,
            request.request_sha256,
            request.canonical_json,
            BOARD_KID,
            "POST /board/artifact-uploads/reserve",
            TASK_ID,
            SUBMISSION_MANIFEST_ID,
            WORKER_ID,
            "worker",
            "artifact-1.txt",
            "text",
            "text/plain; charset=utf-8",
            len(BODY_A),
            hashlib.sha256(BODY_A).hexdigest(),
            1,
            fixture.binding_kind,
            fixture.binding_sha256,
            "reserved",
            reserved["artifact_id"],
            reserved["object_key"],
            attempt["attempt_id"],
            operation_time,
            None,
            "submission",
            None,
        )
        assert _attempt_row(fixture, attempt["attempt_id"]) == (
            attempt["attempt_id"],
            request.upload_operation_id,
            attempt["owner_id"],
            1,
            attempt["attempt_nonce"],
            expected_attempt["staging_key"],
            reserved["object_key"],
            lease_expires_at_us,
            absolute_deadline_us,
            "active",
            None,
            None,
            None,
            operation_time,
            operation_time,
        )
        ready = finalize_upload(fixture, request, reserved)
        expected_ready = dict(expected_reserved)
        del expected_ready["attempt"]
        expected_ready["state"] = "ready"
        assert ready == expected_ready
        expected_upload = list(_upload_row(fixture, request.upload_operation_id))
        assert tuple(expected_upload[:20]) == (
            request.upload_operation_id,
            request.request_sha256,
            request.canonical_json,
            BOARD_KID,
            "POST /board/artifact-uploads/reserve",
            TASK_ID,
            SUBMISSION_MANIFEST_ID,
            WORKER_ID,
            "worker",
            "artifact-1.txt",
            "text",
            "text/plain; charset=utf-8",
            len(BODY_A),
            hashlib.sha256(BODY_A).hexdigest(),
            1,
            fixture.binding_kind,
            fixture.binding_sha256,
            "ready",
            reserved["artifact_id"],
            reserved["object_key"],
        )
        assert tuple(expected_upload[20:]) == (
            None,
            operation_time,
            operation_time,
            "submission",
            None,
        )
        assert _attempt_row(fixture, attempt["attempt_id"]) == (
            attempt["attempt_id"],
            request.upload_operation_id,
            attempt["owner_id"],
            1,
            attempt["attempt_nonce"],
            expected_attempt["staging_key"],
            reserved["object_key"],
            lease_expires_at_us,
            absolute_deadline_us,
            "finalized",
            len(BODY_A),
            hashlib.sha256(BODY_A).hexdigest(),
            None,
            operation_time,
            operation_time,
        )
        assert fixture.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("kind", "digest"),
    [("legacy_accepted_continuation", "f" * 64), ("publication_contract", "f" * 64)],
)
def test_changed_or_cross_kind_binding_is_rejected_before_reservation(
    tmp_path: Path,
    kind: str,
    digest: str,
) -> None:
    fixture = prepare_legacy_submission(tmp_path / f"binding-{kind}.sqlite3", provenance="none")
    try:
        request = upload_request(
            fixture,
            1,
            body=BODY_A,
            binding_kind=kind,
            binding_sha256=digest,
        )
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reserve_upload(fixture, request)
        assert (raised.value.status_code, raised.value.error) == (
            409,
            "accepted_binding_mismatch",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_active_replay_precedes_validation_and_expired_takeover_revalidates(tmp_path: Path) -> None:
    fixture = prepare_legacy_submission(tmp_path / "takeover.sqlite3", provenance="none")
    try:
        request = upload_request(fixture, 1, body=BODY_A)
        reserved = reserve_upload(fixture, request)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reserve_upload(fixture, request)
        assert (raised.value.status_code, raised.value.error) == (409, "upload_pending")
        assert full_snapshot(fixture.connection) == before
        fixture.clock.now_us = int(reserved["attempt"]["lease_expires_at_us"])
        takeover = reserve_upload(fixture, request)
        assert takeover["attempt"]["generation"] == 2
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM board_artifact_uploads WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        fixture.close()


def test_corrupt_evidence_active_replay_is_pending_but_expired_takeover_revalidates(
    tmp_path: Path,
) -> None:
    fixture = prepare_legacy_submission(tmp_path / "corrupt-takeover.sqlite3", provenance="none")
    try:
        request = upload_request(fixture, 1, body=BODY_A)
        reserved = reserve_upload(fixture, request)
        fixture.connection.execute(
            "UPDATE board_legacy_artifact_observations SET canonical_observation_json=? "
            "WHERE task_id=?",
            ('{"tampered":true}', TASK_ID),
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with (
            EvidenceReadGuard(fixture.connection) as guard,
            pytest.raises(ServiceError) as pending,
        ):
            reserve_upload(fixture, request)
        _assert_error(pending, "upload_pending")
        assert guard.reads == []
        assert full_snapshot(fixture.connection) == before
        fixture.clock.now_us = int(reserved["attempt"]["lease_expires_at_us"])
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as invalid:
            reserve_upload(fixture, request)
        _assert_error(invalid, "legacy_observation_invalid")
        assert full_snapshot(fixture.connection) == before
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM board_artifact_upload_attempts WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        fixture.close()


def test_ready_terminal_replay_precedes_corrupt_evidence_and_changed_intent_conflicts(
    tmp_path: Path,
) -> None:
    fixture = prepare_legacy_submission(tmp_path / "ready-replay.sqlite3", provenance="none")
    try:
        request, ready = ready_upload(fixture, 1, body=BODY_A)
        fixture.connection.execute(
            "UPDATE board_legacy_artifact_observations SET canonical_observation_json=? "
            "WHERE task_id=?",
            ('{"tampered":true}', TASK_ID),
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with EvidenceReadGuard(fixture.connection) as guard:
            replay = reserve_upload(fixture, request)
        assert replay == ready and "attempt" not in replay
        assert guard.reads == []
        assert full_snapshot(fixture.connection) == before
        changed = upload_request(fixture, 1, body=BODY_B)
        before_changed = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as conflict:
            reserve_upload(fixture, changed)
        _assert_error(conflict, "upload_operation_conflict")
        assert full_snapshot(fixture.connection) == before_changed
        attempt_count = fixture.connection.execute(
            "SELECT COUNT(*),MAX(generation) FROM board_artifact_upload_attempts "
            "WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        assert attempt_count is not None and tuple(attempt_count) == (1, 1)
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "tamper",
    ["missing", "row_incomplete", "contract_incomplete", "canonical", "hash", "task_title"],
)
def test_stored_legacy_evidence_is_revalidated_before_new_reservation(
    tmp_path: Path,
    tamper: str,
) -> None:
    fixture = prepare_legacy_submission(tmp_path / f"reserve-{tamper}.sqlite3", provenance="none")
    try:
        if tamper == "missing":
            fixture.connection.execute(
                "DELETE FROM board_legacy_artifact_observations WHERE task_id=?", (TASK_ID,)
            )
            expected = "legacy_continuation_unavailable"
        elif tamper == "row_incomplete":
            fixture.connection.execute(
                "UPDATE board_legacy_artifact_observations SET "
                "observation_status='incomplete_corrupt' WHERE task_id=?",
                (TASK_ID,),
            )
            expected = "legacy_continuation_unavailable"
        elif tamper == "contract_incomplete":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET "
                "legacy_acceptance_observation_status='incomplete_corrupt' WHERE task_id=?",
                (TASK_ID,),
            )
            expected = "legacy_continuation_unavailable"
        elif tamper == "canonical":
            fixture.connection.execute(
                "UPDATE board_legacy_artifact_observations SET canonical_observation_json=? "
                "WHERE task_id=?",
                ('{"tampered":true}', TASK_ID),
            )
            expected = "legacy_observation_invalid"
        elif tamper == "hash":
            fixture.connection.execute(
                "UPDATE board_legacy_artifact_observations SET observation_sha256=? "
                "WHERE task_id=?",
                ("f" * 64, TASK_ID),
            )
            expected = "legacy_observation_invalid"
        else:
            fixture.connection.execute(
                "UPDATE board_tasks SET title='tampered' WHERE task_id=?", (TASK_ID,)
            )
            expected = "legacy_observation_invalid"
        fixture.connection.commit()
        request = upload_request(fixture, 1, body=BODY_A)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reserve_upload(fixture, request)
        _assert_error(raised, expected)
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "tamper",
    ["delete", "protocol", "state", "actor", "source_hash"],
)
def test_current_publication_partition_is_revalidated_before_reservation(
    tmp_path: Path,
    tamper: str,
) -> None:
    fixture = prepare_legacy_submission(
        tmp_path / f"publication-{tamper}.sqlite3",
        provenance="preserved_v1",
    )
    try:
        if tamper == "delete":
            fixture.connection.execute(
                "DELETE FROM board_task_publications WHERE task_id=?", (TASK_ID,)
            )
        elif tamper in {"protocol", "state"}:
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=ON")
            column = (
                "request_protocol_version=2" if tamper == "protocol" else "state='awaiting_lock'"
            )
            fixture.connection.execute(
                f"UPDATE board_task_publications SET {column} WHERE task_id=?",  # nosec B608
                (TASK_ID,),
            )
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
        elif tamper == "actor":
            fixture.connection.execute(
                "UPDATE board_task_publications SET actor_id=? WHERE task_id=?",
                (WORKER_ID, TASK_ID),
            )
        else:
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET source_publication_request_sha256=? "
                "WHERE task_id=?",
                ("f" * 64, TASK_ID),
            )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reserve_upload(fixture, upload_request(fixture, 1, body=BODY_A))
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "legacy_observation_invalid",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_actual_publication_insertion_into_null_provenance_history_is_invalid(
    tmp_path: Path,
) -> None:
    fixture = prepare_legacy_submission(tmp_path / "publication-insert.sqlite3", provenance="none")
    donor = prepare_legacy_submission(
        tmp_path / "publication-donor.sqlite3", provenance="preserved_v1"
    )
    try:
        row = donor.connection.execute(
            "SELECT * FROM board_task_publications WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert row is not None and len(row) == 30
        placeholders = ",".join("?" for _ in row)
        fixture.connection.execute(
            f"INSERT INTO board_task_publications VALUES ({placeholders})",  # nosec B608
            tuple(row),
        )
        fixture.connection.commit()
        assert fixture.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reserve_upload(fixture, upload_request(fixture, 1, body=BODY_A))
        _assert_error(raised, "legacy_observation_invalid")
        assert full_snapshot(fixture.connection) == before
    finally:
        donor.close()
        fixture.close()


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("poster_id", "legacy_observation_invalid"),
        ("spec", "legacy_observation_invalid"),
        ("reward", "legacy_observation_invalid"),
        ("bidding_duration", "legacy_observation_invalid"),
        ("execution_duration", "legacy_observation_invalid"),
        ("review_duration", "legacy_observation_invalid"),
        ("bidding_deadline", "legacy_observation_invalid"),
        ("escrow_id", "legacy_observation_invalid"),
        ("worker", "forbidden"),
        ("accepted_bid", "legacy_observation_invalid"),
        ("accepted_at", "legacy_observation_invalid"),
        ("execution", "legacy_observation_invalid"),
        ("bidder", "legacy_observation_invalid"),
        ("bid_amount", "legacy_observation_invalid"),
        ("bid_proposal", "legacy_observation_invalid"),
        ("bid_time", "legacy_observation_invalid"),
        ("escrow_payer", "legacy_observation_invalid"),
        ("escrow_amount", "legacy_observation_invalid"),
        ("escrow_status", "legacy_observation_invalid"),
        ("timing_version", "legacy_observation_invalid"),
        ("lifecycle", "task_version_conflict"),
        ("cutoff", "legacy_observation_invalid"),
        ("accepted_reward", "legacy_observation_invalid"),
        ("accepted_bid_amount", "legacy_observation_invalid"),
        ("input_provenance", "legacy_observation_invalid"),
        ("observed_backdate", "legacy_observation_invalid"),
    ],
)
def test_every_authoritative_current_fact_is_revalidated_before_reservation(
    tmp_path: Path,
    domain: str,
    expected: str,
) -> None:
    fixture = prepare_legacy_submission(tmp_path / f"current-{domain}.sqlite3", provenance="none")
    try:
        statements: dict[str, tuple[str, tuple[object, ...]]] = {
            "poster_id": (
                "UPDATE board_tasks SET poster_id=? WHERE task_id=?",
                (WORKER_ID, TASK_ID),
            ),
            "spec": (
                "UPDATE board_tasks SET spec='changed specification' WHERE task_id=?",
                (TASK_ID,),
            ),
            "reward": ("UPDATE board_tasks SET reward=101 WHERE task_id=?", (TASK_ID,)),
            "bidding_duration": (
                "UPDATE board_tasks SET bidding_deadline_seconds=61 WHERE task_id=?",
                (TASK_ID,),
            ),
            "execution_duration": (
                "UPDATE board_tasks SET deadline_seconds=3601 WHERE task_id=?",
                (TASK_ID,),
            ),
            "review_duration": (
                "UPDATE board_tasks SET review_deadline_seconds=61 WHERE task_id=?",
                (TASK_ID,),
            ),
            "bidding_deadline": (
                "UPDATE board_tasks SET bidding_deadline='2099-01-02T00:01:01Z' WHERE task_id=?",
                (TASK_ID,),
            ),
            "worker": ("UPDATE board_tasks SET worker_id=? WHERE task_id=?", (POSTER_ID, TASK_ID)),
            "accepted_bid": (
                "UPDATE board_tasks SET accepted_bid_id='other' WHERE task_id=?",
                (TASK_ID,),
            ),
            "accepted_at": (
                "UPDATE board_tasks SET accepted_at='2099-01-02T00:00:41Z' WHERE task_id=?",
                (TASK_ID,),
            ),
            "execution": (
                "UPDATE board_tasks SET execution_deadline='2099-01-02T01:00:41Z' WHERE task_id=?",
                (TASK_ID,),
            ),
            "bidder": ("UPDATE board_bids SET bidder_id=? WHERE bid_id=?", (POSTER_ID, BID_ID)),
            "bid_amount": ("UPDATE board_bids SET amount=89 WHERE bid_id=?", (BID_ID,)),
            "bid_proposal": ("UPDATE board_bids SET proposal='changed' WHERE bid_id=?", (BID_ID,)),
            "bid_time": (
                "UPDATE board_bids SET submitted_at='2099-01-02T00:00:31Z' WHERE bid_id=?",
                (BID_ID,),
            ),
            "escrow_payer": (
                "UPDATE bank_escrow SET payer_account_id=? WHERE task_id=?",
                (WORKER_ID, TASK_ID),
            ),
            "escrow_amount": ("UPDATE bank_escrow SET amount=101 WHERE task_id=?", (TASK_ID,)),
            "escrow_status": (
                "UPDATE bank_escrow SET status='released' WHERE task_id=?",
                (TASK_ID,),
            ),
            "timing_version": (
                "UPDATE board_task_contracts SET timing_contract_version=1,"
                "acceptance_window_seconds=NULL,acceptance_cutoff_us=bidding_cutoff_us "
                "WHERE task_id=?",
                (TASK_ID,),
            ),
            "lifecycle": (
                "UPDATE board_task_contracts SET lifecycle_version=2 WHERE task_id=?",
                (TASK_ID,),
            ),
            "cutoff": (
                "UPDATE board_task_contracts SET execution_cutoff_us=execution_cutoff_us+1 "
                "WHERE task_id=?",
                (TASK_ID,),
            ),
            "accepted_reward": (
                "UPDATE board_task_contracts SET accepted_reward=101 WHERE task_id=?",
                (TASK_ID,),
            ),
            "accepted_bid_amount": (
                "UPDATE board_task_contracts SET accepted_bid_amount=89 WHERE task_id=?",
                (TASK_ID,),
            ),
            "observed_backdate": (
                "UPDATE board_legacy_artifact_observations SET observed_at_us=observed_at_us-1 "
                "WHERE task_id=?",
                (TASK_ID,),
            ),
        }
        if domain == "escrow_id":
            fixture.connection.execute(
                "INSERT INTO bank_escrow (escrow_id,payer_account_id,amount,task_id,status,"
                "created_at,resolved_at,event_id) VALUES "
                "('escrow-mp09-legacy-other',?,100,'other-task','locked',"
                "'2099-01-01T23:59:59Z',NULL,NULL)",
                (POSTER_ID,),
            )
            fixture.connection.execute(
                "UPDATE board_tasks SET escrow_id='escrow-mp09-legacy-other' WHERE task_id=?",
                (TASK_ID,),
            )
        elif domain == "input_provenance":
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=ON")
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET input_provenance='native_v6' "
                "WHERE task_id=?",
                (TASK_ID,),
            )
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
        elif domain == "worker":
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA foreign_keys=OFF")
            fixture.connection.execute(
                "UPDATE board_tasks SET worker_id=? WHERE task_id=?",
                (POSTER_ID, TASK_ID),
            )
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA foreign_keys=ON")
            assert fixture.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        else:
            sql, parameters = statements[domain]
            fixture.connection.execute(sql, parameters)
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reserve_upload(fixture, upload_request(fixture, 1, body=BODY_A))
        _assert_error(raised, expected)
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "tamper",
    ["missing", "row_incomplete", "contract_incomplete", "canonical", "publication_deleted"],
)
def test_finalize_evidence_failure_durably_orphans_upload_and_preserves_terminal_replay(
    tmp_path: Path,
    tamper: str,
) -> None:
    provenance = "preserved_v1" if tamper == "publication_deleted" else "none"
    fixture = prepare_legacy_submission(
        tmp_path / f"orphan-{tamper}.sqlite3",
        provenance=provenance,
    )
    try:
        request = upload_request(fixture, 1, body=BODY_A)
        reserved = reserve_upload(fixture, request)
        finalize_time = ACCEPTED_US + 10_000_000
        fixture.clock.now_us = finalize_time
        if tamper == "missing":
            fixture.connection.execute(
                "DELETE FROM board_legacy_artifact_observations WHERE task_id=?", (TASK_ID,)
            )
            expected = "legacy_continuation_unavailable"
        elif tamper == "row_incomplete":
            fixture.connection.execute(
                "UPDATE board_legacy_artifact_observations SET "
                "observation_status='incomplete_corrupt' WHERE task_id=?",
                (TASK_ID,),
            )
            expected = "legacy_continuation_unavailable"
        elif tamper == "contract_incomplete":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET "
                "legacy_acceptance_observation_status='incomplete_corrupt' WHERE task_id=?",
                (TASK_ID,),
            )
            expected = "legacy_continuation_unavailable"
        elif tamper == "canonical":
            fixture.connection.execute(
                "UPDATE board_legacy_artifact_observations SET canonical_observation_json=? "
                "WHERE task_id=?",
                ('{"tampered":true}', TASK_ID),
            )
            expected = "legacy_observation_invalid"
        else:
            fixture.connection.execute(
                "DELETE FROM board_task_publications WHERE task_id=?", (TASK_ID,)
            )
            expected = "legacy_observation_invalid"
        fixture.connection.commit()
        with pytest.raises(ServiceError) as raised:
            finalize_upload(fixture, request, reserved)
        _assert_error(raised, expected)
        upload = fixture.connection.execute(
            "SELECT state,current_attempt_id,finalized_at_us FROM board_artifact_uploads "
            "WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        assert upload is not None and tuple(upload[:2]) == ("orphaned", None)
        assert upload[2] == finalize_time
        attempt_exact = fixture.connection.execute(
            "SELECT state,failure_code,updated_at_us FROM board_artifact_upload_attempts "
            "WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        assert attempt_exact is not None and tuple(attempt_exact) == (
            "orphaned",
            expected,
            finalize_time,
        )
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as stale:
            finalize_upload(fixture, request, reserved)
        _assert_error(stale, "stale_upload_attempt")
        assert full_snapshot(fixture.connection) == before
        _assert_orphan_terminal_replays(fixture, request, reserved)
    finally:
        fixture.close()


def test_active_upload_blocks_legacy_submission_without_mutation(tmp_path: Path) -> None:
    fixture = prepare_legacy_submission(tmp_path / "active-submit.sqlite3", provenance="none")
    try:
        reserve_upload(fixture, upload_request(fixture, 1, body=BODY_A))
        request = submission_request(fixture, 1)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            fixture.operations.claim_operation(request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (409, "upload_pending")
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize("historical_asset", [False, True])
def test_no_ready_v6_uploads_returns_no_assets_and_keeps_draft_manifest(
    tmp_path: Path,
    historical_asset: bool,
) -> None:
    fixture = prepare_legacy_submission(
        tmp_path / f"no-assets-{historical_asset}.sqlite3",
        provenance="none",
    )
    try:
        if historical_asset:
            fixture.connection.execute(
                "INSERT INTO board_assets (asset_id,task_id,uploader_id,filename,content_type,"
                "size_bytes,storage_path,content_hash,uploaded_at) VALUES "
                "('legacy-asset',?,?, 'old.txt','text/plain',3,'old/path',?,"
                "'2099-01-02T00:00:41Z')",
                (TASK_ID, WORKER_ID, hashlib.sha256(b"old").hexdigest()),
            )
            fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            fixture.operations.claim_operation(submission_request(fixture, 1), BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (400, "no_assets")
        assert full_snapshot(fixture.connection) == before
        assert (
            fixture.connection.execute(
                "SELECT state FROM board_artifact_manifests WHERE manifest_id=?",
                (SUBMISSION_MANIFEST_ID,),
            ).fetchone()[0]
            == "draft"
        )
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("tamper", "expected"),
    [
        ("missing_contract", "artifact_contract_missing"),
        ("wrong_version", "accepted_binding_mismatch"),
        ("changed_request", "accepted_binding_mismatch"),
        ("cross_kind", "accepted_binding_mismatch"),
        ("missing_observation", "legacy_continuation_unavailable"),
        ("row_incomplete", "legacy_continuation_unavailable"),
        ("contract_incomplete", "legacy_continuation_unavailable"),
        ("canonical", "legacy_observation_invalid"),
        ("hash", "legacy_observation_invalid"),
        ("task_title", "legacy_observation_invalid"),
        ("publication", "legacy_observation_invalid"),
    ],
)
def test_ready_submission_revalidates_binding_observation_and_current_facts(
    tmp_path: Path,
    tamper: str,
    expected: str,
) -> None:
    provenance = "preserved_v1" if tamper == "publication" else "none"
    fixture = prepare_legacy_submission(
        tmp_path / f"submit-{tamper}.sqlite3", provenance=provenance
    )
    try:
        ready_upload(fixture, 1, body=BODY_A)
        request = submission_request(fixture, 1)
        if tamper == "missing_contract":
            fixture.connection.execute(
                "DELETE FROM board_task_artifact_contracts WHERE task_id=?", (TASK_ID,)
            )
        elif tamper == "wrong_version":
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=ON")
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET artifact_contract_version=1 "
                "WHERE task_id=?",
                (TASK_ID,),
            )
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
        elif tamper in {"changed_request", "cross_kind"}:
            request = submission_request(
                fixture,
                1,
                binding_kind=("publication_contract" if tamper == "cross_kind" else None),
                binding_sha256="f" * 64,
            )
        elif tamper == "missing_observation":
            fixture.connection.execute(
                "DELETE FROM board_legacy_artifact_observations WHERE task_id=?", (TASK_ID,)
            )
        elif tamper == "row_incomplete":
            fixture.connection.execute(
                "UPDATE board_legacy_artifact_observations SET "
                "observation_status='incomplete_corrupt' WHERE task_id=?",
                (TASK_ID,),
            )
        elif tamper == "contract_incomplete":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET "
                "legacy_acceptance_observation_status='incomplete_corrupt' WHERE task_id=?",
                (TASK_ID,),
            )
        elif tamper == "canonical":
            fixture.connection.execute(
                "UPDATE board_legacy_artifact_observations SET "
                "canonical_observation_json='{}' WHERE task_id=?",
                (TASK_ID,),
            )
        elif tamper == "hash":
            fixture.connection.execute(
                "UPDATE board_legacy_artifact_observations SET observation_sha256=? "
                "WHERE task_id=?",
                ("f" * 64, TASK_ID),
            )
        elif tamper == "task_title":
            fixture.connection.execute(
                "UPDATE board_tasks SET title='changed' WHERE task_id=?", (TASK_ID,)
            )
        else:
            fixture.connection.execute(
                "DELETE FROM board_task_publications WHERE task_id=?", (TASK_ID,)
            )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            fixture.operations.claim_operation(request, BOARD_KID)
        _assert_error(raised, expected)
        assert full_snapshot(fixture.connection) == before
        assert (
            fixture.connection.execute(
                "SELECT state FROM board_artifact_manifests WHERE manifest_id=?",
                (SUBMISSION_MANIFEST_ID,),
            ).fetchone()[0]
            == "draft"
        )
    finally:
        fixture.close()


def _expected_item(
    request: Any,
    ready: dict[str, Any],
    *,
    body: bytes,
    finalized_at_us: int,
) -> dict[str, object]:
    return {
        "artifact_id": ready["artifact_id"],
        "display_filename": request.display_filename,
        "evidence_format": "text",
        "media_type": "text/plain; charset=utf-8",
        "byte_size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "provenance": "v6_upload",
        "actor_id": WORKER_ID,
        "uploaded_at": epoch_us_to_utc_text(finalized_at_us),
    }


def test_two_ready_uploads_freeze_exact_manifest_and_submit_atomically(tmp_path: Path) -> None:
    fixture = prepare_legacy_submission(tmp_path / "submit-success.sqlite3", provenance="none")
    try:
        fixture.clock.now_us = ACCEPTED_US + 1_000_000
        first_time = fixture.clock.now_us
        first_request, first = ready_upload(fixture, 1, body=BODY_A)
        fixture.clock.now_us += 1_000_000
        second_time = fixture.clock.now_us
        second_request, second = ready_upload(fixture, 2, body=BODY_B)
        fixture.clock.now_us += 1_000_000
        submitted_at = fixture.clock.now_us
        ready_rows_before = tuple(
            tuple(row)
            for row in fixture.connection.execute(
                "SELECT * FROM board_artifact_uploads ORDER BY upload_operation_id"
            )
        )
        result = fixture.operations.claim_operation(submission_request(fixture, 1), BOARD_KID)
        items = sorted(
            [
                _expected_item(first_request, first, body=BODY_A, finalized_at_us=first_time),
                _expected_item(second_request, second, body=BODY_B, finalized_at_us=second_time),
            ],
            key=lambda item: str(item["artifact_id"]),
        )
        expected_json = canonical_json(
            {
                "format_version": 1,
                "manifest_id": SUBMISSION_MANIFEST_ID,
                "task_id": TASK_ID,
                "kind": "submission",
                "actor_id": WORKER_ID,
                "artifacts": items,
            }
        )
        expected_hash = hashlib.sha256(expected_json.encode()).hexdigest()
        manifest = fixture.connection.execute(
            "SELECT state,canonical_manifest_json,manifest_sha256,frozen_at_us "
            "FROM board_artifact_manifests WHERE manifest_id=?",
            (SUBMISSION_MANIFEST_ID,),
        ).fetchone()
        assert manifest is not None and tuple(manifest) == (
            "frozen",
            expected_json,
            expected_hash,
            submitted_at,
        )
        assert result["protocol_version"] == 2
        assert result["result"]["manifest_sha256"] == expected_hash
        assert result["result"]["status"] == "submitted"
        task = fixture.connection.execute(
            "SELECT status,submitted_at,review_deadline FROM board_tasks WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        timing = fixture.connection.execute(
            "SELECT lifecycle_version,review_cutoff_us FROM board_task_contracts WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert task is not None and tuple(task) == (
            "submitted",
            epoch_us_to_utc_text(submitted_at),
            epoch_us_to_utc_text(submitted_at + 60_000_000),
        )
        assert timing is not None and tuple(timing) == (2, submitted_at + 60_000_000)
        operation_id = "op-77000000-0000-4000-8000-000000000001"
        operation = fixture.connection.execute(
            "SELECT * FROM board_task_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        assert operation is not None and type(operation[21]) is int and int(operation[21]) > 0
        event_id = int(operation[21])
        expected_result = {
            "task_id": TASK_ID,
            "command": "submit_deliverable",
            "status": "submitted",
            "lifecycle_version": 2,
            "worker_id": WORKER_ID,
            "manifest_id": SUBMISSION_MANIFEST_ID,
            "manifest_sha256": expected_hash,
            "event_id": event_id,
        }
        request = submission_request(fixture, 1)
        assert tuple(operation) == (
            operation_id,
            1,
            TASK_ID,
            "submit_deliverable",
            request.canonical_json,
            request.request_sha256,
            BOARD_KID,
            "agent",
            WORKER_ID,
            "accepted",
            1,
            "submitted",
            2,
            "completed",
            "none",
            "none",
            None,
            None,
            None,
            submitted_at,
            submitted_at,
            event_id,
            canonical_json(expected_result),
            None,
        )
        event = fixture.connection.execute(
            "SELECT event_source,event_type,timestamp,task_id,agent_id,summary,payload "
            "FROM events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        assert event is not None and tuple(event) == (
            "board",
            "task.submitted",
            epoch_us_to_utc_text(submitted_at),
            TASK_ID,
            WORKER_ID,
            "Task operation completed: submit_deliverable",
            canonical_json({"command": "submit_deliverable", "operation_id": operation_id}),
        )
        assert result == {
            "protocol_version": 2,
            "operation_id": operation_id,
            "task_id": TASK_ID,
            "command": "submit_deliverable",
            "request_sha256": request.request_sha256,
            "operation_state": "completed",
            "source_status": "accepted",
            "source_lifecycle_version": 1,
            "target_status": "submitted",
            "target_lifecycle_version": 2,
            "effect_kind": "none",
            "effect_state": "none",
            "effect_operation_id": None,
            "effect_result_reference": None,
            "effect_intent": None,
            "event_id": event_id,
            "result": expected_result,
        }
        assert ready_rows_before == tuple(
            tuple(row)
            for row in fixture.connection.execute(
                "SELECT * FROM board_artifact_uploads ORDER BY upload_operation_id"
            )
        )
        assert fixture.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("target", "mode", "status", "error"),
    _FAILURE_CASES,
)
def test_each_submission_write_failure_rolls_back_full_snapshot(
    tmp_path: Path,
    target: Any,
    mode: Any,
    status: int,
    error: str,
) -> None:
    fixture = prepare_legacy_submission(
        tmp_path / f"failure-{target}-{mode}.sqlite3",
        provenance="none",
    )
    try:
        ready_upload(fixture, 1, body=BODY_A)
        writer = failpoint_submission_writer(fixture, target=target, mode=mode)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            writer.claim_operation(submission_request(fixture, 1), BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (status, error)
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_validation_runtime_error_bubbles_without_persistence_translation(tmp_path: Path) -> None:
    fixture = prepare_legacy_submission(tmp_path / "validation-runtime.sqlite3", provenance="none")
    try:
        ready_upload(fixture, 1, body=BODY_A)
        writer = failpoint_submission_writer(
            fixture,
            target="validation_select",
            mode="runtime_before",
        )
        before = full_snapshot(fixture.connection)
        with pytest.raises(RuntimeError, match="injected validation_select"):
            writer.claim_operation(submission_request(fixture, 1), BOARD_KID)
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()
