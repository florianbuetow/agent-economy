"""Core MP-09 V2 receipt-adoption contract."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_receipt_adoption_fixture import (
    ADMITTED_US,
    AUTHORIZED_US,
    ESCROW_ID,
    FROZEN_US,
    LOCK_COMMITTED_US,
    POSTER_ID,
    PUBLICATION_ID,
    PUBLISHED_US,
    TASK_ID,
    V2_ENVELOPE_KEYS,
    BankReadGuard,
    ReceiptAdoptionFixture,
    admit_publication,
    authorize_publication,
    bank_snapshot,
    expected_creation_result,
    fixed_utc_text,
    freeze_publication,
    full_snapshot,
    fund_poster,
    lock_publication,
    open_receipt_fixture,
    prepare_authorized_publication,
    reconcile_publication,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _assert_published_envelope(
    fixture: ReceiptAdoptionFixture,
    envelope: dict[str, Any],
    request: Any,
) -> None:
    assert fixture.freeze_request is not None
    assert fixture.frozen is not None
    assert fixture.publish_request is not None
    receipt = fixture.connection.execute(
        "SELECT request_sha256,event_id,escrow_id,committed_at FROM "
        "bank_operation_receipts WHERE operation_id=?",
        (request.lock_operation_id,),
    ).fetchone()
    assert receipt is not None
    creation_event_id = envelope["creation_event_id"]
    assert type(creation_event_id) is int and creation_event_id > 0
    expected_result = expected_creation_result(fixture, event_id=creation_event_id)
    assert envelope["input_manifest_sha256"] == fixture.frozen["input_manifest_sha256"]
    assert envelope["publication_contract_sha256"] == fixture.frozen["publication_contract_sha256"]
    assert envelope["inputs_frozen_at_us"] == FROZEN_US
    assert envelope["freeze_operation_id"] == fixture.freeze_request.freeze_operation_id
    assert envelope["publish_operation_id"] == fixture.publish_request.publish_operation_id
    assert envelope["publish_authorized_at_us"] == AUTHORIZED_US
    assert envelope["lock_operation_id"] == request.lock_operation_id
    assert envelope["escrow_id"] == receipt[2] == ESCROW_ID
    assert envelope["lock_request_sha256"] == receipt[0]
    assert envelope["lock_receipt_event_id"] == receipt[1]
    assert str(receipt[3]) == fixed_utc_text(LOCK_COMMITTED_US)
    assert envelope["admitted_at"] == fixed_utc_text(ADMITTED_US)
    assert envelope["published_at"] == fixed_utc_text(PUBLISHED_US)
    assert envelope["creation_event_id"] == expected_result["event_id"]
    assert envelope["result"] == expected_result
    assert envelope["error"] is None
    stored = fixture.connection.execute(
        "SELECT escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
        "creation_event_id,result_json FROM board_task_publications WHERE operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    assert stored is not None
    assert tuple(stored[:5]) == (
        ESCROW_ID,
        receipt[0],
        receipt[1],
        PUBLISHED_US,
        creation_event_id,
    )
    assert str(stored[5]) == json.dumps(
        expected_result,
        sort_keys=True,
        separators=(",", ":"),
    )


def _assert_phase_envelope(
    fixture: ReceiptAdoptionFixture,
    envelope: dict[str, Any],
    *,
    state: str,
) -> None:
    assert set(envelope) == V2_ENVELOPE_KEYS
    assert fixture.admission_request is not None
    request = fixture.admission_request
    assert envelope["protocol_version"] == 2
    assert envelope["publication_operation_id"] == PUBLICATION_ID
    assert envelope["task_id"] == TASK_ID
    assert envelope["actor_id"] == POSTER_ID
    assert envelope["publication_state"] == state
    assert envelope["request_sha256"] == request.request_sha256
    assert envelope["base_request_sha256"] == request.request_sha256
    assert envelope["expected_input_count"] == 0
    assert envelope["input_manifest_id"] == fixture.manifest_id
    assert envelope["lock_operation_id"] == request.lock_operation_id
    assert envelope["admitted_at"] == fixed_utc_text(ADMITTED_US)
    assert envelope["error"] is None
    if state == "awaiting_inputs":
        for field in (
            "input_manifest_sha256",
            "publication_contract_sha256",
            "inputs_frozen_at_us",
            "freeze_operation_id",
            "publish_operation_id",
            "publish_authorized_at_us",
            "escrow_id",
            "lock_request_sha256",
            "lock_receipt_event_id",
            "published_at",
            "creation_event_id",
            "result",
        ):
            assert envelope[field] is None
    elif state == "awaiting_publish":
        assert envelope["input_manifest_sha256"] is not None
        assert envelope["publication_contract_sha256"] is not None
        assert envelope["inputs_frozen_at_us"] == FROZEN_US
        assert envelope["freeze_operation_id"] == fixture.freeze_request.freeze_operation_id
        for field in (
            "publish_operation_id",
            "publish_authorized_at_us",
            "escrow_id",
            "lock_request_sha256",
            "lock_receipt_event_id",
            "published_at",
            "creation_event_id",
            "result",
        ):
            assert envelope[field] is None
    elif state == "awaiting_lock":
        assert envelope["input_manifest_sha256"] is not None
        assert envelope["publication_contract_sha256"] is not None
        assert envelope["inputs_frozen_at_us"] == FROZEN_US
        assert envelope["freeze_operation_id"] == fixture.freeze_request.freeze_operation_id
        assert envelope["publish_operation_id"] == fixture.publish_request.publish_operation_id
        assert envelope["publish_authorized_at_us"] == AUTHORIZED_US
        for field in (
            "escrow_id",
            "lock_request_sha256",
            "lock_receipt_event_id",
            "published_at",
            "creation_event_id",
            "result",
        ):
            assert envelope[field] is None
    else:
        assert state == "published"
        _assert_published_envelope(fixture, envelope, request)


@pytest.mark.parametrize("phase", ["admitted", "frozen", "authorized"])
def test_every_prepublication_phase_returns_the_exact_v2_envelope(
    tmp_path: Path,
    phase: str,
) -> None:
    fixture = open_receipt_fixture(tmp_path / f"envelope-{phase}.sqlite3")
    try:
        fund_poster(fixture)
        admitted = admit_publication(fixture)
        if phase == "admitted":
            _assert_phase_envelope(fixture, admitted, state="awaiting_inputs")
            return
        frozen = freeze_publication(fixture)
        if phase == "frozen":
            _assert_phase_envelope(fixture, frozen, state="awaiting_publish")
            return
        authorized = authorize_publication(fixture)
        _assert_phase_envelope(fixture, authorized, state="awaiting_lock")
    finally:
        fixture.close()


def test_real_bank_lock_materializes_exact_public_graph_without_mutating_bank(
    tmp_path: Path,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / "real-lock.sqlite3")
    try:
        lock_publication(fixture)
        bank_before = bank_snapshot(fixture.connection)
        published = reconcile_publication(fixture)
        assert bank_snapshot(fixture.connection) == bank_before
        _assert_phase_envelope(fixture, published, state="published")
        event_id = published["creation_event_id"]
        assert type(event_id) is int and event_id > 0
        expected_result = expected_creation_result(fixture, event_id=event_id)
        assert published["result"] == expected_result

        request = fixture.admission_request
        assert request is not None
        task = fixture.connection.execute(
            "SELECT poster_id,title,spec,reward,status,bidding_deadline_seconds,"
            "deadline_seconds,review_deadline_seconds,bidding_deadline,execution_deadline,"
            "review_deadline,bid_count,escrow_pending,escrow_id,worker_id,accepted_bid_id,"
            "created_at FROM board_tasks WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert task is not None
        assert tuple(task) == (
            POSTER_ID,
            request.title,
            request.spec,
            request.reward,
            "open",
            request.bidding_deadline_seconds,
            request.execution_deadline_seconds,
            request.review_deadline_seconds,
            expected_result["bidding_deadline"],
            None,
            None,
            0,
            0,
            ESCROW_ID,
            None,
            None,
            expected_result["created_at"],
        )
        timing = fixture.connection.execute(
            "SELECT lifecycle_version,timing_contract_version,acceptance_window_seconds,"
            "bidding_cutoff_us,acceptance_cutoff_us,execution_cutoff_us,review_cutoff_us,"
            "accepted_reward,accepted_bid_amount FROM board_task_contracts WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert timing is not None
        assert tuple(timing) == (
            0,
            2,
            request.acceptance_window_seconds,
            expected_result["bidding_cutoff_us"],
            expected_result["acceptance_cutoff_us"],
            None,
            None,
            None,
            None,
        )
        event = fixture.connection.execute(
            "SELECT event_source,event_type,timestamp,task_id,agent_id,summary,payload "
            "FROM events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        assert event is not None
        assert tuple(event) == (
            "task-board",
            "task.created",
            fixed_utc_text(PUBLISHED_US),
            TASK_ID,
            POSTER_ID,
            f"Task created: {request.title}",
            json.dumps(
                {
                    "bidding_deadline": expected_result["bidding_deadline"],
                    "reward": request.reward,
                    "title": request.title,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        artifact = fixture.connection.execute(
            "SELECT artifact_contract_version,input_provenance,accepted_binding_kind,"
            "base_request_sha256,input_manifest_id,input_manifest_sha256,"
            "publication_contract_sha256,accepted_publication_contract_sha256,"
            "legacy_acceptance_observation_sha256,legacy_acceptance_observation_status,"
            "source_publication_request_sha256,publication_operation_id,input_manifest_kind,"
            "input_actor_id FROM board_task_artifact_contracts WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert artifact is not None
        assert tuple(artifact) == (
            1,
            "native_v6",
            None,
            request.request_sha256,
            fixture.manifest_id,
            published["input_manifest_sha256"],
            published["publication_contract_sha256"],
            None,
            None,
            None,
            None,
            PUBLICATION_ID,
            "input",
            POSTER_ID,
        )
        manifest = fixture.connection.execute(
            "SELECT publication_operation_id,public_task_id FROM board_artifact_manifests "
            "WHERE manifest_id=?",
            (fixture.manifest_id,),
        ).fetchone()
        assert manifest is not None and tuple(manifest) == (PUBLICATION_ID, None)
        assert fixture.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        fixture.close()


@pytest.mark.parametrize("ownership", ["configured", "persisted"])
def test_caller_failure_precedes_bank_reads_and_leaves_every_fact_unchanged(
    tmp_path: Path,
    ownership: str,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / f"caller-{ownership}.sqlite3")
    try:
        lock_publication(fixture)
        other_kid = "gateway-task-board-replacement"
        writer = fixture.publication_writer
        caller = other_kid
        if ownership == "configured":
            caller = other_kid
        else:
            writer = fixture.api.writer_type(
                fixture.connection,
                clock_us=fixture.clock,
                artifact_policy=fixture.artifact_policy,
                current_task_board_caller_kid=other_kid,
            )
        before = full_snapshot(fixture.connection)
        with (
            BankReadGuard(fixture.connection, deny=True) as guard,
            pytest.raises(ServiceError) as raised,
        ):
            writer.reconcile(PUBLICATION_ID, verified_caller_kid=caller)
        assert (raised.value.status_code, raised.value.error) == (
            403,
            "operation_caller_mismatch",
        )
        assert guard.reads == []
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()
