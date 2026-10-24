"""MP-09 native V6 acceptance artifact-binding contract."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.artifact_upload_requests import parse_artifact_upload_request
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import (
    TaskOperationRequest,
    parse_task_operation_request,
)
from db_gateway_service.task_time import epoch_us_to_utc_text
from tests.conftest import make_event
from tests.fakes.mp09_acceptance_binding_fixture import (
    ACCEPT_OPERATION_ID,
    ACCEPTED_US,
    ARTIFACT_TABLES,
    BID_AMOUNT,
    BID_ID,
    SUBMISSION_MANIFEST_ID,
    AcceptanceBindingFixture,
    AcceptanceReadGuard,
    acceptance_payload,
    claim_acceptance,
    coherently_tamper_frozen_manifest,
    failpoint_operation_writer,
    full_snapshot,
    immutable_publication_snapshot,
    install_partial_governance_surface,
    make_artifact_writer,
    prepare_native_acceptance,
)
from tests.fakes.mp09_receipt_adoption_fixture import (
    AUTHORIZED_US,
    BOARD_KID,
    POSTER_ID,
    PUBLICATION_ID,
    TASK_ID,
    WORKER_ID,
    BankReadGuard,
    bank_snapshot,
    fixed_utc_text,
)
from tests.fakes.native_publication_fixture import publish_native_task

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_NATIVE_RESULT_KEYS = {
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
_V5_ACCEPTED_US = 4_070_908_802_000_000


def _assert_exact_native_acceptance(
    fixture: AcceptanceBindingFixture,
    envelope: dict[str, Any],
) -> None:
    admission = fixture.publication.admission_request
    assert admission is not None
    execution_cutoff = ACCEPTED_US + admission.execution_deadline_seconds * 1_000_000
    accepted_at = epoch_us_to_utc_text(ACCEPTED_US)
    task = fixture.connection.execute(
        "SELECT status,worker_id,accepted_bid_id,accepted_at,execution_deadline "
        "FROM board_tasks WHERE task_id=?",
        (TASK_ID,),
    ).fetchone()
    timing = fixture.connection.execute(
        "SELECT lifecycle_version,execution_cutoff_us,accepted_reward,accepted_bid_amount "
        "FROM board_task_contracts WHERE task_id=?",
        (TASK_ID,),
    ).fetchone()
    event = fixture.connection.execute(
        "SELECT event_id,event_source,event_type,timestamp,task_id,agent_id,summary,payload "
        "FROM events WHERE event_type='task.accepted' AND task_id=?",
        (TASK_ID,),
    ).fetchone()
    operation = fixture.connection.execute(
        "SELECT operation_id,operation_contract_version,task_id,command,canonical_request_json,"
        "request_sha256,caller_kid,actor_kind,actor_id,source_status,"
        "source_lifecycle_version,target_status,target_lifecycle_version,operation_state,"
        "effect_kind,effect_state,effect_operation_id,effect_result_reference,"
        "effect_intent_json,claimed_at_us,completed_at_us,event_id,result_json,error_json "
        "FROM board_task_operations WHERE operation_id=?",
        (ACCEPT_OPERATION_ID,),
    ).fetchone()
    assert task is not None and tuple(task) == (
        "accepted",
        WORKER_ID,
        BID_ID,
        accepted_at,
        epoch_us_to_utc_text(execution_cutoff),
    )
    assert timing is not None and tuple(timing) == (1, execution_cutoff, 100, BID_AMOUNT)
    assert event is not None and tuple(event[1:]) == (
        "board",
        "task.accepted",
        accepted_at,
        TASK_ID,
        POSTER_ID,
        "Task operation completed: accept_bid",
        json.dumps(
            {"command": "accept_bid", "operation_id": ACCEPT_OPERATION_ID},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    assert operation is not None
    result = {
        "task_id": TASK_ID,
        "command": "accept_bid",
        "status": "accepted",
        "lifecycle_version": 1,
        "bid_id": BID_ID,
        "worker_id": WORKER_ID,
        "reward": 100,
        "bid_amount": BID_AMOUNT,
        "accepted_binding_kind": "publication_contract",
        "accepted_binding_sha256": fixture.accepted_binding_sha256,
        "event_id": int(event[0]),
    }
    assert tuple(operation) == (
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
        ACCEPTED_US,
        ACCEPTED_US,
        event[0],
        json.dumps(result, sort_keys=True, separators=(",", ":")),
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
        "event_id": event[0],
        "result": result,
    }


def _assert_native_result(fixture: AcceptanceBindingFixture, envelope: dict[str, Any]) -> None:
    result = envelope["result"]
    assert isinstance(result, dict)
    assert set(result) == _NATIVE_RESULT_KEYS
    assert result == {
        "task_id": TASK_ID,
        "command": "accept_bid",
        "status": "accepted",
        "lifecycle_version": 1,
        "bid_id": BID_ID,
        "worker_id": WORKER_ID,
        "reward": 100,
        "bid_amount": BID_AMOUNT,
        "accepted_binding_kind": "publication_contract",
        "accepted_binding_sha256": fixture.accepted_binding_sha256,
        "event_id": result["event_id"],
    }


def test_real_native_acceptance_atomically_copies_publication_binding(tmp_path: Path) -> None:
    fixture = prepare_native_acceptance(tmp_path / "native-success.sqlite3")
    try:
        publication_before = immutable_publication_snapshot(fixture.connection)
        bank_before = bank_snapshot(fixture.connection)
        accepted = claim_acceptance(fixture)
        _assert_exact_native_acceptance(fixture, accepted)
        binding = fixture.connection.execute(
            "SELECT accepted_binding_kind,accepted_publication_contract_sha256,"
            "publication_contract_sha256 FROM board_task_artifact_contracts WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert binding is not None and tuple(binding) == (
            "publication_contract",
            fixture.accepted_binding_sha256,
            fixture.accepted_binding_sha256,
        )
        _assert_native_result(fixture, accepted)
        assert immutable_publication_snapshot(fixture.connection) == publication_before
        assert bank_snapshot(fixture.connection) == bank_before
        assert fixture.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        fixture.close()


def test_exact_acceptance_replay_uses_collision_probe_then_stored_operation_only(
    tmp_path: Path,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / "exact-replay.sqlite3")
    try:
        original = claim_acceptance(fixture)
        before = full_snapshot(fixture.connection)
        with AcceptanceReadGuard(fixture.connection, deny_current_state=True) as guard:
            replay = claim_acceptance(fixture)
        assert replay == original
        assert guard.reads[:2] == ["board_task_publications", "board_task_operations"]
        assert not ({"board_tasks", "board_task_contracts", *ARTIFACT_TABLES} & set(guard.reads))
        assert full_snapshot(fixture.connection) == before
        _assert_native_result(fixture, replay)
    finally:
        fixture.close()


def test_changed_acceptance_replay_conflicts_without_mutation(tmp_path: Path) -> None:
    fixture = prepare_native_acceptance(tmp_path / "changed-replay.sqlite3")
    try:
        claim_acceptance(fixture)
        changed = parse_task_operation_request(acceptance_payload(bid_amount=BID_AMOUNT - 1))
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture, request=changed)
        assert (raised.value.status_code, raised.value.error) == (409, "operation_id_conflict")
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_new_acceptance_after_success_cannot_replace_worker_or_binding(tmp_path: Path) -> None:
    fixture = prepare_native_acceptance(tmp_path / "second-accept.sqlite3")
    try:
        original = claim_acceptance(fixture)
        second_payload = acceptance_payload(operation_id="op-63000000-0000-4000-8000-000000000099")
        second = parse_task_operation_request(second_payload)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture, request=second)
        assert (raised.value.status_code, raised.value.error) == (
            409,
            "task_version_conflict",
        )
        assert full_snapshot(fixture.connection) == before
        assert original["result"]["worker_id"] == WORKER_ID
    finally:
        fixture.close()


def test_publication_operation_identity_wins_before_acceptance_state_reads(tmp_path: Path) -> None:
    fixture = prepare_native_acceptance(tmp_path / "cross-journal.sqlite3")
    try:
        collision = parse_task_operation_request(acceptance_payload(operation_id=PUBLICATION_ID))
        before = full_snapshot(fixture.connection)
        with (
            AcceptanceReadGuard(fixture.connection, deny_current_state=True) as guard,
            pytest.raises(ServiceError) as raised,
        ):
            claim_acceptance(fixture, request=collision)
        assert (raised.value.status_code, raised.value.error, raised.value.details) == (
            409,
            "operation_id_conflict",
            {"winning_operation_id": PUBLICATION_ID},
        )
        assert guard.reads == ["board_task_publications"]
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("missing", "artifact_contract_missing"),
        ("legacy", "legacy_observation_invalid"),
        ("prebound", "artifact_contract_invalid"),
        ("base_hash", "artifact_contract_invalid"),
        ("contract_hash", "artifact_contract_invalid"),
        ("creation_result", "artifact_contract_invalid"),
        ("actor_link", "artifact_contract_invalid"),
    ],
)
def test_invalid_native_contract_fails_before_any_acceptance_write(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / f"invalid-{mutation}.sqlite3")
    try:
        if mutation == "missing":
            fixture.connection.execute(
                "DELETE FROM board_task_artifact_contracts WHERE task_id=?", (TASK_ID,)
            )
        elif mutation == "legacy":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET artifact_contract_version=0,"
                "input_provenance='unknown_pre_v6',accepted_binding_kind=NULL,"
                "base_request_sha256=NULL,input_manifest_id=NULL,input_manifest_sha256=NULL,"
                "publication_contract_sha256=NULL,accepted_publication_contract_sha256=NULL,"
                "legacy_acceptance_observation_sha256=NULL,"
                "legacy_acceptance_observation_status=NULL,"
                "source_publication_request_sha256=NULL,publication_operation_id=NULL,"
                "input_manifest_kind=NULL,input_actor_id=NULL WHERE task_id=?",
                (TASK_ID,),
            )
        elif mutation == "prebound":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET "
                "accepted_binding_kind='publication_contract',"
                "accepted_publication_contract_sha256=publication_contract_sha256 "
                "WHERE task_id=?",
                (TASK_ID,),
            )
        elif mutation == "base_hash":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET base_request_sha256=? WHERE task_id=?",
                ("f" * 64, TASK_ID),
            )
        elif mutation == "contract_hash":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET publication_contract_sha256=? "
                "WHERE task_id=?",
                ("f" * 64, TASK_ID),
            )
        elif mutation == "creation_result":
            fixture.connection.execute(
                "UPDATE board_task_publications SET result_json=? WHERE operation_id=?",
                ('{"tampered":true}', PUBLICATION_ID),
            )
        else:
            # Controlled runtime-corruption proof: preserve the signed acceptance
            # actor while breaking only the persisted native input-owner link.
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA foreign_keys=OFF")
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET input_actor_id=? WHERE task_id=?",
                (WORKER_ID, TASK_ID),
            )
            fixture.connection.commit()
            fixture.connection.execute("PRAGMA foreign_keys=ON")
            assert fixture.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture)
        assert (raised.value.status_code, raised.value.error) == (
            409 if expected_error == "artifact_contract_missing" else 500,
            expected_error,
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_coherent_manifest_and_hash_rewrite_cannot_replace_authoritative_upload_set(
    tmp_path: Path,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / "coherent-tamper.sqlite3")
    try:
        coherently_tamper_frozen_manifest(fixture.connection)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "artifact_contract_invalid",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "domain",
    [
        "admission_canonical",
        "freeze_canonical",
        "publish_canonical",
        "phase_chronology",
        "lock_event",
        "published_task",
        "published_timing",
    ],
)
def test_full_publication_integrity_domain_corruption_blocks_acceptance(
    tmp_path: Path,
    domain: str,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / f"integrity-{domain}.sqlite3")
    try:
        if domain == "admission_canonical":
            fixture.connection.execute(
                "UPDATE board_task_publications SET canonical_request_json=? WHERE operation_id=?",
                ('{"tampered":true}', PUBLICATION_ID),
            )
        elif domain == "freeze_canonical":
            fixture.connection.execute(
                "UPDATE board_task_publications SET canonical_freeze_request_json=? "
                "WHERE operation_id=?",
                ('{"tampered":true}', PUBLICATION_ID),
            )
        elif domain == "publish_canonical":
            fixture.connection.execute(
                "UPDATE board_task_publications SET canonical_publish_request_json=? "
                "WHERE operation_id=?",
                ('{"tampered":true}', PUBLICATION_ID),
            )
        elif domain == "phase_chronology":
            fixture.connection.execute(
                "UPDATE board_task_publications SET publish_authorized_at_us="
                "inputs_frozen_at_us-1 WHERE operation_id=?",
                (PUBLICATION_ID,),
            )
        elif domain == "lock_event":
            fixture.connection.execute(
                "UPDATE events SET timestamp=? WHERE event_id=(SELECT lock_receipt_event_id "
                "FROM board_task_publications WHERE operation_id=?)",
                (fixed_utc_text(AUTHORIZED_US - 1), PUBLICATION_ID),
            )
        elif domain == "published_task":
            fixture.connection.execute(
                "UPDATE board_tasks SET title='tampered title' WHERE task_id=?",
                (TASK_ID,),
            )
        else:
            fixture.connection.execute(
                "UPDATE board_task_contracts SET bidding_cutoff_us=bidding_cutoff_us+1,"
                "acceptance_cutoff_us=acceptance_cutoff_us+1 WHERE task_id=?",
                (TASK_ID,),
            )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "artifact_contract_invalid",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "missing",
    [
        *(frozenset({table}) for table in sorted(ARTIFACT_TABLES)),
        frozenset(sorted(ARTIFACT_TABLES)[:6]),
    ],
    ids=[*(f"missing-{table}" for table in sorted(ARTIFACT_TABLES)), "six-missing"],
)
def test_partial_artifact_governance_surface_fails_closed(
    tmp_path: Path,
    missing: frozenset[str],
) -> None:
    suffix = "-".join(sorted(missing))
    writer, operations, request = _prepare_v5_acceptance(tmp_path / f"partial-{suffix}")
    try:
        install_partial_governance_surface(writer.connection, missing=missing)
        before = tuple(writer.connection.iterdump())
        with pytest.raises(ServiceError) as raised:
            operations.claim_operation(request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "artifact_governance_incomplete",
        )
        assert tuple(writer.connection.iterdump()) == before
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("target", "mode", "expected_status", "expected_error"),
    [
        ("task_update", "zero_row_after", 409, "task_version_conflict"),
        ("timing_update", "zero_row_after", 409, "task_version_conflict"),
        ("artifact_update", "zero_row_after", 409, "task_version_conflict"),
        ("event_insert", "zero_row_after", 500, "task_operation_persistence_failed"),
        ("operation_insert", "zero_row_after", 500, "task_operation_persistence_failed"),
        ("task_update", "operational_before", 500, "task_operation_persistence_failed"),
        ("timing_update", "operational_before", 500, "task_operation_persistence_failed"),
        ("artifact_update", "operational_before", 500, "task_operation_persistence_failed"),
        ("event_insert", "operational_before", 500, "task_operation_persistence_failed"),
        ("operation_insert", "operational_before", 500, "task_operation_persistence_failed"),
    ],
)
def test_each_acceptance_write_failure_rolls_back_every_fact(
    tmp_path: Path,
    target: Any,
    mode: Any,
    expected_status: int,
    expected_error: str,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / f"fail-{target}-{mode}.sqlite3")
    try:
        injected = failpoint_operation_writer(fixture, target=target, mode=mode)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture, writer=injected)
        assert (raised.value.status_code, raised.value.error) == (
            expected_status,
            expected_error,
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize("table", ["events", "board_task_operations"])
def test_ignored_acceptance_insert_is_persistence_failure(
    tmp_path: Path,
    table: str,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / f"ignore-{table}.sqlite3")
    try:
        condition = (
            "NEW.event_type='task.accepted'" if table == "events" else "NEW.command='accept_bid'"
        )
        fixture.connection.execute(
            f"CREATE TRIGGER fixture_ignore BEFORE INSERT ON {table} "  # nosec B608
            f"WHEN {condition} BEGIN SELECT RAISE(IGNORE); END"
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "task_operation_persistence_failed",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


@pytest.mark.parametrize("table", ["events", "board_task_operations"])
def test_trigger_aborted_acceptance_insert_is_persistence_failure(
    tmp_path: Path,
    table: str,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / f"abort-{table}.sqlite3")
    try:
        condition = (
            "NEW.event_type='task.accepted'" if table == "events" else "NEW.command='accept_bid'"
        )
        fixture.connection.execute(
            f"CREATE TRIGGER fixture_abort BEFORE INSERT ON {table} "  # nosec B608
            f"WHEN {condition} BEGIN SELECT RAISE(ABORT,'fixture abort'); END"
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "task_operation_persistence_failed",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_natural_unique_constraint_failure_rolls_back_acceptance(tmp_path: Path) -> None:
    fixture = prepare_native_acceptance(tmp_path / "natural-unique.sqlite3")
    try:
        fixture.connection.execute(
            "CREATE UNIQUE INDEX fixture_unique_event ON events(event_type,task_id)"
        )
        fixture.connection.execute(
            "INSERT INTO events "
            "(event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
            "VALUES ('fixture','task.accepted','2099-01-01T00:00:00Z',?,?, 'fixture','{}')",
            (TASK_ID, POSTER_ID),
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "task_operation_persistence_failed",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_natural_foreign_key_failure_rolls_back_acceptance(tmp_path: Path) -> None:
    fixture = prepare_native_acceptance(tmp_path / "natural-foreign-key.sqlite3")
    try:
        # Controlled corruption removes the selected worker after the real bid,
        # so the first acceptance update reaches SQLite's native FK constraint.
        fixture.connection.commit()
        fixture.connection.execute("PRAGMA foreign_keys=OFF")
        fixture.connection.execute("DELETE FROM identity_agents WHERE agent_id=?", (WORKER_ID,))
        fixture.connection.commit()
        fixture.connection.execute("PRAGMA foreign_keys=ON")
        assert fixture.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            claim_acceptance(fixture)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "task_operation_persistence_failed",
        )
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_real_acceptance_preserves_published_replay_and_immutable_input(tmp_path: Path) -> None:
    fixture = prepare_native_acceptance(tmp_path / "publication-replay.sqlite3")
    try:
        immutable_before = immutable_publication_snapshot(fixture.connection)
        accepted = claim_acceptance(fixture)
        before_replay = full_snapshot(fixture.connection)
        with BankReadGuard(fixture.connection, deny=True) as bank_guard:
            owner = fixture.publication.publication_writer.get(
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
            replay = fixture.publication.publication_writer.reconcile(
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
            )
        assert bank_guard.reads == []
        assert owner == fixture.published_envelope
        assert replay == fixture.published_envelope
        assert full_snapshot(fixture.connection) == before_replay
        assert immutable_publication_snapshot(fixture.connection) == immutable_before
        _assert_native_result(fixture, accepted)
    finally:
        fixture.close()


def test_acceptance_result_bridges_existing_upload_and_submission_kernels(tmp_path: Path) -> None:
    fixture = prepare_native_acceptance(tmp_path / "consumer-bridge.sqlite3")
    try:
        accepted = claim_acceptance(fixture)
        fixture.connection.execute(
            "INSERT INTO board_artifact_manifests (manifest_id,task_id,kind,actor_id,"
            "format_version,state,canonical_manifest_json,manifest_sha256,frozen_at_us,"
            "publication_operation_id,public_task_id) VALUES (?,?,'submission',?,1,'draft',"
            "NULL,NULL,NULL,NULL,?)",
            (SUBMISSION_MANIFEST_ID, TASK_ID, WORKER_ID, TASK_ID),
        )
        fixture.connection.commit()
        result = accepted["result"]
        assert isinstance(result, dict)
        binding_kind = result["accepted_binding_kind"]
        binding_hash = result["accepted_binding_sha256"]
        body = b"accepted artifact\n"
        upload = parse_artifact_upload_request(
            {
                "protocol_version": 1,
                "upload_operation_id": "73000000-0000-4000-8000-000000000001",
                "resource": "POST /board/artifact-uploads/reserve",
                "task_id": TASK_ID,
                "manifest_id": SUBMISSION_MANIFEST_ID,
                "actor_id": WORKER_ID,
                "actor_role": "worker",
                "display_filename": "answer.txt",
                "evidence_format": "text",
                "declared_size_bytes": len(body),
                "content_sha256": hashlib.sha256(body).hexdigest(),
                "expected_lifecycle_version": 1,
                "accepted_binding_kind": binding_kind,
                "accepted_binding_sha256": binding_hash,
            }
        )
        artifacts = make_artifact_writer(fixture)
        fixture.clock.now_us = ACCEPTED_US + 1_000_000
        reserved = artifacts.reserve_upload(upload, verified_caller_kid=BOARD_KID)
        attempt = reserved["attempt"]
        ready = artifacts.finalize_upload(
            upload.upload_operation_id,
            owner_id=attempt["owner_id"],
            generation=attempt["generation"],
            attempt_nonce=attempt["attempt_nonce"],
            observed_size_bytes=len(body),
            observed_sha256=hashlib.sha256(body).hexdigest(),
            verified_caller_kid=BOARD_KID,
        )
        assert ready["state"] == "ready"
        changed = _submission_request(str(binding_kind), "f" * 64)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            fixture.operation_writer.claim_operation(changed, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            409,
            "accepted_binding_mismatch",
        )
        assert full_snapshot(fixture.connection) == before
        submitted = fixture.operation_writer.claim_operation(
            _submission_request(str(binding_kind), str(binding_hash), number=2),
            BOARD_KID,
        )
        assert submitted["result"]["manifest_id"] == SUBMISSION_MANIFEST_ID
        assert submitted["result"]["status"] == "submitted"
    finally:
        fixture.close()


def _submission_request(
    kind: str,
    digest: str,
    *,
    number: int = 1,
) -> TaskOperationRequest:
    return parse_task_operation_request(
        {
            "protocol_version": 2,
            "operation_id": f"op-64000000-0000-4000-8000-{number:012d}",
            "task_id": TASK_ID,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": WORKER_ID,
            "arguments": {
                "worker_id": WORKER_ID,
                "manifest_id": SUBMISSION_MANIFEST_ID,
                "accepted_binding_kind": kind,
                "accepted_binding_sha256": digest,
            },
        }
    )


def _prepare_v5_acceptance(
    database: Path,
) -> tuple[DbWriter, TaskOperationWriter, Any]:
    writer = DbWriter(
        db_path=str(database),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V5),
        current_task_board_caller_kid=BOARD_KID,
    )
    poster = f"poster-v5-{uuid4()}"
    worker = f"worker-v5-{uuid4()}"
    task = f"t-{uuid4()}"
    escrow = f"esc-{uuid4()}"
    try:
        for agent in (poster, worker):
            writer.register_agent(
                {
                    "agent_id": agent,
                    "name": agent,
                    "public_key": f"ed25519:{uuid4()}",
                    "registered_at": "2099-01-01T00:00:00Z",
                    "event": make_event(agent_id=agent),
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
            title="V5 acceptance control",
            spec="Preserve historical acceptance bytes.",
            reward=100,
            bidding_deadline_seconds=60,
            acceptance_window_seconds=60,
            execution_deadline_seconds=3600,
            review_deadline_seconds=60,
            lock_timestamp="2099-01-01T00:00:01Z",
        )

        def clock() -> int:
            return _V5_ACCEPTED_US

        operations = TaskOperationWriter(
            writer.connection,
            clock_us=clock,
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
        return writer, operations, request
    except BaseException:
        writer.close()
        raise


def test_current_v5_acceptance_bytes_and_bank_receipts_remain_unchanged(tmp_path: Path) -> None:
    writer, operations, request = _prepare_v5_acceptance(tmp_path / "v5-control.sqlite3")
    try:
        present_tables = {
            str(row[0])
            for row in writer.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert not (ARTIFACT_TABLES & present_tables)
        bank_before = tuple(
            tuple(row)
            for row in writer.connection.execute(
                "SELECT * FROM bank_operation_receipts ORDER BY operation_id"
            )
        )
        accepted = operations.claim_operation(request, BOARD_KID)
        accepted_at = epoch_us_to_utc_text(_V5_ACCEPTED_US)
        execution_cutoff = _V5_ACCEPTED_US + 3_600_000_000
        task = writer.connection.execute(
            "SELECT status,worker_id,accepted_bid_id,accepted_at,execution_deadline "
            "FROM board_tasks WHERE task_id=?",
            (request.task_id,),
        ).fetchone()
        timing = writer.connection.execute(
            "SELECT lifecycle_version,execution_cutoff_us,accepted_reward,accepted_bid_amount "
            "FROM board_task_contracts WHERE task_id=?",
            (request.task_id,),
        ).fetchone()
        stored = writer.connection.execute(
            "SELECT operation_id,operation_contract_version,task_id,command,"
            "canonical_request_json,request_sha256,caller_kid,actor_kind,actor_id,"
            "source_status,source_lifecycle_version,target_status,target_lifecycle_version,"
            "operation_state,effect_kind,effect_state,effect_operation_id,"
            "effect_result_reference,effect_intent_json,claimed_at_us,completed_at_us,event_id,"
            "result_json,error_json FROM "
            "board_task_operations WHERE operation_id=?",
            (request.operation_id,),
        ).fetchone()
        assert task is not None and tuple(task) == (
            "accepted",
            request.argument("worker_id"),
            request.argument("bid_id"),
            accepted_at,
            epoch_us_to_utc_text(execution_cutoff),
        )
        assert timing is not None and tuple(timing) == (1, execution_cutoff, 100, 90)
        assert stored is not None and type(stored[21]) is int and int(stored[21]) > 0
        event = writer.connection.execute(
            "SELECT event_source,event_type,timestamp,task_id,agent_id,summary,payload "
            "FROM events WHERE event_id=?",
            (stored[21],),
        ).fetchone()
        assert event is not None and tuple(event) == (
            "board",
            "task.accepted",
            accepted_at,
            request.task_id,
            request.actor_id,
            "Task operation completed: accept_bid",
            json.dumps(
                {"command": "accept_bid", "operation_id": request.operation_id},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        expected_result = {
            "task_id": request.task_id,
            "command": "accept_bid",
            "status": "accepted",
            "lifecycle_version": 1,
            "bid_id": request.argument("bid_id"),
            "worker_id": request.argument("worker_id"),
            "reward": 100,
            "bid_amount": 90,
            "event_id": int(stored[21]),
        }
        assert tuple(stored) == (
            request.operation_id,
            1,
            request.task_id,
            "accept_bid",
            request.canonical_json,
            request.request_sha256,
            BOARD_KID,
            "agent",
            request.actor_id,
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
            _V5_ACCEPTED_US,
            _V5_ACCEPTED_US,
            stored[21],
            json.dumps(expected_result, sort_keys=True, separators=(",", ":")),
            None,
        )
        assert accepted == {
            "protocol_version": 1,
            "operation_id": request.operation_id,
            "task_id": request.task_id,
            "command": "accept_bid",
            "request_sha256": request.request_sha256,
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
            "event_id": stored[21],
            "result": expected_result,
        }
        assert operations.claim_operation(request, BOARD_KID) == accepted
        assert bank_before == tuple(
            tuple(row)
            for row in writer.connection.execute(
                "SELECT * FROM bank_operation_receipts ORDER BY operation_id"
            )
        )
    finally:
        writer.close()
