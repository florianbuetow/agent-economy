"""Failure-path acceptance tests for MP-09 V2 receipt adoption."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_receipt_adoption_fixture import (
    AUTHORIZED_US,
    BOARD_KID,
    ESCROW_ID,
    LOCK_COMMITTED_US,
    POSTER_ID,
    PUBLICATION_ID,
    PUBLISHED_US,
    TASK_ID,
    V2_ENVELOPE_KEYS,
    BankReadGuard,
    FailMode,
    WriteTarget,
    bank_event,
    bank_snapshot,
    failpoint_publication_writer,
    fixed_utc_text,
    full_snapshot,
    lock_publication,
    prepare_authorized_publication,
    reconcile_publication,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

    from tests.fakes.mp09_receipt_adoption_fixture import ReceiptAdoptionFixture

pytestmark = pytest.mark.unit

type TriggerMode = Literal["abort", "ignore"]

_MATERIALIZATION_TARGETS: tuple[WriteTarget, ...] = (
    "task_insert",
    "timing_insert",
    "creation_event_insert",
    "artifact_contract_insert",
    "completion_update",
)


def _service_error(call: Callable[[], object]) -> ServiceError:
    with pytest.raises(ServiceError) as raised:
        call()
    return raised.value


def _assert_no_materialized_graph(connection: sqlite3.Connection) -> None:
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM board_tasks WHERE task_id=?", (TASK_ID,)
        ).fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM board_task_contracts WHERE task_id=?", (TASK_ID,)
        ).fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND event_source='task-board' "
            "AND event_type='task.created'",
            (TASK_ID,),
        ).fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM board_task_artifact_contracts WHERE task_id=?", (TASK_ID,)
        ).fetchone()[0]
        == 0
    )


def _assert_awaiting_lock(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT state,escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
        "creation_event_id,result_json,error_json FROM board_task_publications "
        "WHERE operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    assert row is not None
    assert tuple(row) == ("awaiting_lock", None, None, None, None, None, None, None)


def _assert_persistence_failure(
    fixture: ReceiptAdoptionFixture,
    call: Callable[[], object],
) -> None:
    before = full_snapshot(fixture.connection)
    bank_before = bank_snapshot(fixture.connection)
    error = _service_error(call)
    assert (error.status_code, error.error) == (500, "publication_persistence_failed")
    assert full_snapshot(fixture.connection) == before
    assert bank_snapshot(fixture.connection) == bank_before
    _assert_awaiting_lock(fixture.connection)
    _assert_no_materialized_graph(fixture.connection)


def _assert_durable_block(
    fixture: ReceiptAdoptionFixture,
    reason: str,
    *,
    expect_no_graph: bool = True,
) -> dict[str, Any]:
    bank_before = bank_snapshot(fixture.connection)
    error = _service_error(lambda: reconcile_publication(fixture))
    assert (
        error.status_code,
        error.error,
        error.message,
    ) == (
        409,
        "publication_integrity_blocked",
        "Publication evidence requires reconciliation",
    )
    assert set(error.details) == {"publication"}
    publication = error.details["publication"]
    assert isinstance(publication, dict)
    assert set(publication) == V2_ENVELOPE_KEYS
    assert publication["publication_state"] == "blocked_integrity"
    assert publication["escrow_id"] is None
    assert publication["result"] is None
    expected_error = {
        "error": "publication_integrity_blocked",
        "message": "Publication evidence requires reconciliation",
        "details": {"reason": reason},
    }
    assert publication["error"] == expected_error
    stored = fixture.connection.execute(
        "SELECT state,error_json FROM board_task_publications WHERE operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    assert stored is not None
    assert tuple(stored) == (
        "blocked_integrity",
        json.dumps(
            expected_error,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )
    assert bank_snapshot(fixture.connection) == bank_before
    if expect_no_graph:
        _assert_no_materialized_graph(fixture.connection)
    return publication


def _lock_with_unrelated_operation(fixture: ReceiptAdoptionFixture) -> None:
    assert fixture.admission_request is not None
    request = fixture.admission_request
    fixture.bank_writer.escrow_lock(
        {
            "operation_id": "op-62000000-0000-4000-8000-000000000001",
            "escrow_id": "escrow-mp09-unrelated-lock",
            "payer_account_id": request.actor_id,
            "amount": request.reward,
            "task_id": request.task_id,
            "created_at": fixed_utc_text(LOCK_COMMITTED_US),
            "tx_id": f"tx-unrelated-{uuid4()}",
            "event": bank_event(
                event_type="escrow.locked",
                timestamp_us=LOCK_COMMITTED_US,
                task_id=request.task_id,
                agent_id=request.actor_id,
            ),
        }
    )


def _settle_lock(fixture: ReceiptAdoptionFixture) -> None:
    resolved_us = LOCK_COMMITTED_US + 1_000_000
    fixture.bank_writer.escrow_release(
        {
            "operation_id": "op-62000000-0000-4000-8000-000000000002",
            "escrow_id": ESCROW_ID,
            "recipient_account_id": POSTER_ID,
            "source_reference": TASK_ID,
            "resolved_at": fixed_utc_text(resolved_us),
            "tx_id": f"tx-release-{uuid4()}",
            "event": bank_event(
                event_type="escrow.released",
                timestamp_us=resolved_us,
                task_id=TASK_ID,
                agent_id=POSTER_ID,
            ),
        }
    )


def _trigger_sql(target: WriteTarget, mode: TriggerMode) -> str:
    target_sql = {
        "task_insert": "BEFORE INSERT ON board_tasks",
        "timing_insert": "BEFORE INSERT ON board_task_contracts",
        "creation_event_insert": (
            "BEFORE INSERT ON events WHEN NEW.event_source='task-board' "
            "AND NEW.event_type='task.created'"
        ),
        "artifact_contract_insert": "BEFORE INSERT ON board_task_artifact_contracts",
        "completion_update": (
            "BEFORE UPDATE ON board_task_publications WHEN NEW.state='published'"
        ),
        "block_update": (
            "BEFORE UPDATE ON board_task_publications WHEN NEW.state='blocked_integrity'"
        ),
    }[target]
    action = (
        "RAISE(ABORT,'injected receipt adoption write failure')"
        if mode == "abort"
        else "RAISE(IGNORE)"
    )
    return f"CREATE TRIGGER mp09_receipt_{target}_{mode} {target_sql} BEGIN SELECT {action}; END"


def test_missing_exact_receipt_remains_retryable_without_any_mutation(tmp_path: Path) -> None:
    fixture = prepare_authorized_publication(tmp_path / "missing-receipt.sqlite3")
    try:
        before = full_snapshot(fixture.connection)
        bank_before = bank_snapshot(fixture.connection)
        with BankReadGuard(fixture.connection, deny=False) as bank_reads:
            result = reconcile_publication(fixture)
        assert result == fixture.authorized
        assert result["publication_state"] == "awaiting_lock"
        assert any(table == "bank_operation_receipts" for table, _column in bank_reads.reads)
        assert full_snapshot(fixture.connection) == before
        assert bank_snapshot(fixture.connection) == bank_before
        _assert_no_materialized_graph(fixture.connection)
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("malformed_receipt", "receipt_invalid"),
        ("mismatched_escrow", "escrow_invalid"),
        ("settled", "settlement_conflict"),
        ("contradictory_history", "receipt_invalid"),
    ],
)
def test_invalid_financial_evidence_durably_blocks_without_partial_projection(
    tmp_path: Path,
    case: str,
    reason: str,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / f"{case}.sqlite3")
    try:
        if case == "contradictory_history":
            _lock_with_unrelated_operation(fixture)
        else:
            lock_publication(fixture)
            if case == "malformed_receipt":
                assert fixture.admission_request is not None
                fixture.connection.execute(
                    "UPDATE bank_operation_receipts SET result_json='{}' WHERE operation_id=?",
                    (fixture.admission_request.lock_operation_id,),
                )
                fixture.connection.commit()
            elif case == "mismatched_escrow":
                fixture.connection.execute(
                    "UPDATE bank_escrow SET amount=amount+1 WHERE escrow_id=?",
                    (ESCROW_ID,),
                )
                fixture.connection.commit()
            else:
                _settle_lock(fixture)
        blocked = _assert_durable_block(fixture, reason)
        replay = _service_error(lambda: reconcile_publication(fixture))
        assert (replay.status_code, replay.error, replay.details) == (
            409,
            "publication_integrity_blocked",
            {"publication": blocked},
        )
    finally:
        fixture.close()


def test_real_task_identity_collision_rolls_back_provisional_graph_and_durably_blocks(
    tmp_path: Path,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / "constraint-conflict.sqlite3")
    try:
        lock_publication(fixture)
        assert fixture.admission_request is not None
        request = fixture.admission_request
        fixture.connection.execute(
            "INSERT INTO board_tasks (task_id,poster_id,title,spec,reward,status,"
            "bidding_deadline_seconds,deadline_seconds,review_deadline_seconds,"
            "bidding_deadline,bid_count,escrow_pending,escrow_id,created_at,dispute_id) "
            "VALUES (?,?,?,?,?,'open',?,?,?, ?,0,0,?,?,NULL)",
            (
                TASK_ID,
                POSTER_ID,
                "Preexisting collision",
                "Preexisting public graph",
                request.reward,
                request.bidding_deadline_seconds,
                request.execution_deadline_seconds,
                request.review_deadline_seconds,
                fixed_utc_text(PUBLISHED_US + request.bidding_deadline_seconds * 1_000_000),
                ESCROW_ID,
                fixed_utc_text(PUBLISHED_US),
            ),
        )
        fixture.connection.commit()
        conflict_before = tuple(
            fixture.connection.execute(
                "SELECT * FROM board_tasks WHERE task_id=?", (TASK_ID,)
            ).fetchone()
            or ()
        )
        bank_before = bank_snapshot(fixture.connection)
        publication = _assert_durable_block(
            fixture,
            "publication_conflict",
            expect_no_graph=False,
        )
        assert publication["publication_state"] == "blocked_integrity"
        assert (
            tuple(
                fixture.connection.execute(
                    "SELECT * FROM board_tasks WHERE task_id=?", (TASK_ID,)
                ).fetchone()
                or ()
            )
            == conflict_before
        )
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM board_task_contracts WHERE task_id=?", (TASK_ID,)
            ).fetchone()[0]
            == 0
        )
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM events WHERE task_id=? AND event_source='task-board'",
                (TASK_ID,),
            ).fetchone()[0]
            == 0
        )
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM board_task_artifact_contracts WHERE task_id=?", (TASK_ID,)
            ).fetchone()[0]
            == 0
        )
        assert bank_snapshot(fixture.connection) == bank_before
    finally:
        fixture.close()


@pytest.mark.parametrize("target", _MATERIALIZATION_TARGETS)
@pytest.mark.parametrize("mode", ["abort", "ignore"])
def test_trigger_failure_at_each_materialization_write_rolls_back_everything(
    tmp_path: Path,
    target: WriteTarget,
    mode: TriggerMode,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / f"trigger-{target}-{mode}.sqlite3")
    try:
        lock_publication(fixture)
        fixture.connection.execute(_trigger_sql(target, mode))
        fixture.connection.commit()
        _assert_persistence_failure(fixture, lambda: reconcile_publication(fixture))
    finally:
        fixture.close()


@pytest.mark.parametrize("target", _MATERIALIZATION_TARGETS)
@pytest.mark.parametrize("mode", ["operational_before", "zero_row_after"])
def test_injected_failure_at_each_materialization_write_rolls_back_everything(
    tmp_path: Path,
    target: WriteTarget,
    mode: Literal["operational_before", "zero_row_after"],
) -> None:
    fixture = prepare_authorized_publication(tmp_path / f"injected-{target}-{mode}.sqlite3")
    try:
        lock_publication(fixture)
        fixture.clock.now_us = PUBLISHED_US
        writer = failpoint_publication_writer(fixture, target=target, mode=mode)
        _assert_persistence_failure(
            fixture,
            lambda: writer.reconcile(PUBLICATION_ID, verified_caller_kid=BOARD_KID),
        )
    finally:
        fixture.close()


def test_completion_commit_failure_rolls_back_all_apparent_writes(tmp_path: Path) -> None:
    fixture = prepare_authorized_publication(tmp_path / "completion-commit.sqlite3")
    try:
        lock_publication(fixture)
        fixture.clock.now_us = PUBLISHED_US
        writer = failpoint_publication_writer(
            fixture,
            target="commit",
            mode="commit_failure",
        )
        _assert_persistence_failure(
            fixture,
            lambda: writer.reconcile(PUBLICATION_ID, verified_caller_kid=BOARD_KID),
        )
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("mechanism", "mode"),
    [
        ("trigger", "abort"),
        ("trigger", "ignore"),
        ("failpoint", "operational_before"),
        ("failpoint", "zero_row_after"),
        ("failpoint", "commit_failure"),
    ],
)
def test_failed_block_write_or_commit_never_claims_uncommitted_409(
    tmp_path: Path,
    mechanism: Literal["trigger", "failpoint"],
    mode: TriggerMode | FailMode,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / f"block-{mechanism}-{mode}.sqlite3")
    try:
        lock_publication(fixture)
        assert fixture.admission_request is not None
        fixture.connection.execute(
            "UPDATE bank_operation_receipts SET result_json='{}' WHERE operation_id=?",
            (fixture.admission_request.lock_operation_id,),
        )
        fixture.connection.commit()
        if mechanism == "trigger":
            fixture.connection.execute(_trigger_sql("block_update", cast("TriggerMode", mode)))
            fixture.connection.commit()

            def call() -> object:
                return reconcile_publication(fixture)

        else:
            target: WriteTarget = "commit" if mode == "commit_failure" else "block_update"
            writer = failpoint_publication_writer(
                fixture,
                target=target,
                mode=cast("FailMode", mode),
            )
            fixture.clock.now_us = PUBLISHED_US

            def call() -> object:
                return writer.reconcile(
                    PUBLICATION_ID,
                    verified_caller_kid=BOARD_KID,
                )

        _assert_persistence_failure(fixture, call)
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "entrypoint",
    ["get", "reconcile", "admission_replay", "command_replay"],
)
@pytest.mark.parametrize(
    ("mutation_name", "mutation"),
    [
        (
            "task_title",
            "UPDATE board_tasks SET title=title||' corrupt' WHERE task_id=?",
        ),
        (
            "timing_acceptance_window",
            "UPDATE board_task_contracts SET acceptance_window_seconds="
            "acceptance_window_seconds+1,acceptance_cutoff_us="
            "acceptance_cutoff_us+1000000 WHERE task_id=?",
        ),
        (
            "creation_event_summary",
            "UPDATE events SET summary='Corrupt creation summary' WHERE event_id="
            "(SELECT creation_event_id FROM board_task_publications WHERE operation_id=?)",
        ),
        (
            "artifact_manifest_hash",
            "UPDATE board_task_artifact_contracts SET input_manifest_sha256=? WHERE task_id=?",
        ),
        (
            "lock_event_source",
            "UPDATE events SET event_source='central-bank-corrupt' WHERE event_id="
            "(SELECT lock_receipt_event_id FROM board_task_publications WHERE operation_id=?)",
        ),
        (
            "lock_event_chronology",
            "UPDATE events SET timestamp=? WHERE event_id="
            "(SELECT lock_receipt_event_id FROM board_task_publications WHERE operation_id=?)",
        ),
        (
            "stored_result_json",
            "UPDATE board_task_publications SET result_json='{}' WHERE operation_id=?",
        ),
    ],
)
def test_published_immutable_corruption_fails_closed_at_every_replay_entrypoint(
    tmp_path: Path,
    entrypoint: str,
    mutation_name: str,
    mutation: str,
) -> None:
    fixture = prepare_authorized_publication(
        tmp_path / f"published-{mutation_name}-{entrypoint}.sqlite3"
    )
    try:
        lock_publication(fixture)
        published = reconcile_publication(fixture)
        assert published["publication_state"] == "published"
        if mutation_name == "artifact_manifest_hash":
            fixture.connection.execute(mutation, ("f" * 64, TASK_ID))
        elif mutation_name == "lock_event_chronology":
            fixture.connection.execute(
                mutation,
                (fixed_utc_text(AUTHORIZED_US - 1), PUBLICATION_ID),
            )
        else:
            mutation_identity = (
                PUBLICATION_ID
                if mutation_name
                in {"creation_event_summary", "lock_event_source", "stored_result_json"}
                else TASK_ID
            )
            fixture.connection.execute(mutation, (mutation_identity,))
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        bank_before = bank_snapshot(fixture.connection)
        assert fixture.admission_request is not None
        assert fixture.publish_request is not None
        calls: dict[str, Callable[[], object]] = {
            "get": lambda: fixture.publication_writer.get(
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            ),
            "reconcile": lambda: reconcile_publication(fixture),
            "admission_replay": lambda: fixture.publication_writer.admit(
                fixture.admission_request,
                verified_caller_kid=BOARD_KID,
            ),
            "command_replay": lambda: fixture.publication_writer.authorize_publish(
                fixture.publish_request,
                verified_caller_kid=BOARD_KID,
            ),
        }
        with BankReadGuard(fixture.connection, deny=True) as bank_reads:
            error = _service_error(calls[entrypoint])
        assert (error.status_code, error.error) == (500, "publication_state_corrupt")
        assert bank_reads.reads == []
        assert full_snapshot(fixture.connection) == before
        assert bank_snapshot(fixture.connection) == bank_before
    finally:
        fixture.close()
