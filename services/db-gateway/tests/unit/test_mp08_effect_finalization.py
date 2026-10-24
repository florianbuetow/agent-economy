"""T-124 exact receipt adoption and atomic source/delivery finalization."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service import task_time
from db_gateway_service.core.state import get_app_state
from tests.conftest import make_event
from tests.fakes.gateway_mp08_effects import (
    delivery,
    lease,
    prepare_legacy_v5,
    prepare_native_pending,
)
from tests.fakes.native_board_history import FIXED_NOW_US, BusinessClock, TaskGraph, settle_release

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
pytest_plugins = ["tests.fakes.gateway_mp08_app"]

_WORKER = "recovery-00000000-0000-4000-8000-000000000011"


def _row_tuple(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...],
) -> tuple[object, ...]:
    row = connection.execute(query, parameters).fetchone()
    assert row is not None
    return tuple(row)


@pytest.fixture(autouse=True)
def _fixed_business_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))


@pytest.fixture
def app_with_writer(gateway_mp08_app: TestClient) -> TestClient:
    return gateway_mp08_app


def test_exact_native_receipt_completes_operation_delivery_and_projection(
    app_with_writer: TestClient,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER).json()["delivery"]
    graph = TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id)
    receipt = settle_release(
        app_with_writer,
        graph,
        operation_id=effect.operation_id,
        recipient_id=effect.worker_id,
        source_reference=f"task:{effect.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}, receipt.text
    finalized = app_with_writer.post(
        f"/board/task-operations/{effect.operation_id}/finalize", json={}
    )
    assert finalized.status_code == 200, finalized.text
    row = delivery(app_with_writer, effect.operation_id)
    assert row["delivery_state"] == "completed"
    state = get_app_state()
    assert state.db_writer is not None
    receipt_event = state.db_writer.connection.execute(
        "SELECT event_id FROM bank_operation_receipts WHERE operation_id=?",
        (effect.operation_id,),
    ).fetchone()
    assert receipt_event is not None
    assert row["completion_event_id"] == receipt_event[0]
    assert row["receipt_reference"] == effect.operation_id
    late_failure = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/"
        f"{leased['lease_generation']}/fail",
        json={
            "protocol_version": 1,
            "worker_id": _WORKER,
            "error_code": "request_timeout",
        },
    )
    assert late_failure.status_code == 409
    assert delivery(app_with_writer, effect.operation_id)["delivery_state"] == "completed"


def test_conflicting_receipt_blocks_native_source_and_delivery_atomically(
    app_with_writer: TestClient,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    graph = TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id)
    incompatible = settle_release(
        app_with_writer,
        graph,
        operation_id=effect.operation_id,
        recipient_id=effect.poster_id,
        source_reference=f"task:{effect.task_id}:approved",
    )
    assert incompatible.status_code in {200, 201}, incompatible.text
    response = app_with_writer.post(
        f"/board/task-operations/{effect.operation_id}/finalize", json={}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "effect_evidence_conflict"
    row = delivery(app_with_writer, effect.operation_id)
    assert row["delivery_state"] == "integrity_blocked"
    state = get_app_state()
    assert state.db_writer is not None
    source = state.db_writer.connection.execute(
        "SELECT operation_state,effect_state,error_json FROM board_task_operations "
        "WHERE operation_id=?",
        (effect.operation_id,),
    ).fetchone()
    assert source is not None
    assert source[0:2] == ("blocked", "conflict")
    assert source[2] is not None


def test_completion_preserves_prior_failure_diagnostics(app_with_writer: TestClient) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER).json()["delivery"]
    failed = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/"
        f"{leased['lease_generation']}/fail",
        json={
            "protocol_version": 1,
            "worker_id": _WORKER,
            "error_code": "request_timeout",
        },
    )
    assert failed.status_code == 200, failed.text
    graph = TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id)
    receipt = settle_release(
        app_with_writer,
        graph,
        operation_id=effect.operation_id,
        recipient_id=effect.worker_id,
        source_reference=f"task:{effect.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}, receipt.text
    finalized = app_with_writer.post(
        f"/board/task-operations/{effect.operation_id}/finalize", json={}
    )
    assert finalized.status_code == 200
    row = delivery(app_with_writer, effect.operation_id)
    assert row["last_failure_code"] == "request_timeout"
    assert row["last_failure_at_us"] is not None


def test_exact_receipt_with_contradictory_projection_blocks_as_confirmed(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.worker_id,
        source_reference=f"task:{effect.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}
    db_writer.connection.execute(
        "UPDATE board_tasks SET status='cancelled' WHERE task_id=?",
        (effect.task_id,),
    )
    db_writer.connection.commit()
    money_before = db_writer.connection.execute(
        "SELECT operation_id,request_sha256,event_id FROM bank_operation_receipts "
        "WHERE operation_id=?",
        (effect.operation_id,),
    ).fetchone()
    response = app_with_writer.post(
        f"/board/task-operations/{effect.operation_id}/finalize", json={}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "effect_evidence_conflict"
    row = delivery(app_with_writer, effect.operation_id)
    assert row["delivery_state"] == "integrity_blocked"
    assert row["receipt_reference"] == effect.operation_id
    assert (
        db_writer.connection.execute(
            "SELECT operation_id,request_sha256,event_id FROM bank_operation_receipts "
            "WHERE operation_id=?",
            (effect.operation_id,),
        ).fetchone()
        == money_before
    )


def test_legacy_pending_finalization_repairs_only_pending_projection(tmp_path: Path) -> None:
    source, writer = prepare_legacy_v5(tmp_path, kind="pending")
    try:
        finalize = getattr(writer, "finalize_legacy_effect_observation", None)
        assert callable(finalize), "Missing protected legacy finalization writer"
        before = writer.connection.execute(
            "SELECT status,approved_at,escrow_pending FROM board_tasks WHERE task_id=?",
            (source.task_id,),
        ).fetchone()
        contract_before = writer.connection.execute(
            "SELECT lifecycle_version FROM board_task_contracts WHERE task_id=?",
            (source.task_id,),
        ).fetchone()
        events_before = writer.connection.execute(
            "SELECT event_id,event_type FROM events WHERE task_id=? ORDER BY event_id",
            (source.task_id,),
        ).fetchall()
        finalize(source.observation_id, "gateway-task-board")
        assert (
            writer.connection.execute(
                "SELECT status,approved_at,escrow_pending FROM board_tasks WHERE task_id=?",
                (source.task_id,),
            ).fetchone()
            == before
        )
        writer.escrow_release(
            {
                "operation_id": source.operation_id,
                "escrow_id": source.escrow_id,
                "recipient_account_id": source.worker_id,
                "source_reference": f"task:{source.task_id}:approved",
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2099-01-01T00:00:04Z",
                "event": {
                    **make_event(
                        source="central-bank",
                        event_type="escrow.released",
                        task_id=source.task_id,
                        agent_id=source.worker_id,
                    ),
                    "timestamp": "2099-01-01T00:00:04Z",
                },
            }
        )
        finalize(source.observation_id, "gateway-task-board")
        assert _row_tuple(
            writer.connection,
            "SELECT delivery_state FROM board_task_effect_deliveries WHERE legacy_observation_id=?",
            (source.observation_id,),
        ) == ("completed",)
        completion = writer.connection.execute(
            "SELECT d.completion_event_id,r.event_id FROM board_task_effect_deliveries d "
            "JOIN bank_operation_receipts r ON r.operation_id=d.effect_operation_id "
            "WHERE d.legacy_observation_id=?",
            (source.observation_id,),
        ).fetchone()
        assert completion is not None and completion[0] == completion[1]
        assert _row_tuple(
            writer.connection,
            "SELECT status,approved_at,escrow_pending FROM board_tasks WHERE task_id=?",
            (source.task_id,),
        ) == ("approved", before[1], 0)
        assert (
            writer.connection.execute(
                "SELECT lifecycle_version FROM board_task_contracts WHERE task_id=?",
                (source.task_id,),
            ).fetchone()
            == contract_before
        )
        events_after = writer.connection.execute(
            "SELECT event_id,event_type FROM events WHERE task_id=? ORDER BY event_id",
            (source.task_id,),
        ).fetchall()
        assert all(row in events_after for row in events_before)
        assert [row for row in events_after if row[1].startswith("task.")] == [
            row for row in events_before if row[1].startswith("task.")
        ]
    finally:
        writer.close()


def test_legacy_succeeded_finalization_dispatches_no_money_and_repairs_minimally(
    tmp_path: Path,
) -> None:
    source, writer = prepare_legacy_v5(tmp_path, kind="succeeded")
    try:
        finalize = getattr(writer, "finalize_legacy_effect_observation", None)
        assert callable(finalize), "Missing protected legacy finalization writer"
        receipt_before = writer.connection.execute(
            "SELECT * FROM bank_operation_receipts ORDER BY operation_id"
        ).fetchall()
        legs_before = writer.connection.execute(
            "SELECT * FROM bank_operation_legs ORDER BY operation_id,leg_index"
        ).fetchall()
        events_before = writer.connection.execute(
            "SELECT event_id,event_source,event_type FROM events ORDER BY event_id"
        ).fetchall()
        finalize(source.observation_id, "gateway-task-board")
        assert _row_tuple(
            writer.connection,
            "SELECT delivery_state FROM board_task_effect_deliveries WHERE legacy_observation_id=?",
            (source.observation_id,),
        ) == ("completed",)
        completion = writer.connection.execute(
            "SELECT d.completion_event_id,r.event_id FROM board_task_effect_deliveries d "
            "JOIN bank_operation_receipts r ON r.operation_id=d.effect_operation_id "
            "WHERE d.legacy_observation_id=?",
            (source.observation_id,),
        ).fetchone()
        assert completion is not None and completion[0] == completion[1]
        assert _row_tuple(
            writer.connection,
            "SELECT status,approved_at,escrow_pending FROM board_tasks WHERE task_id=?",
            (source.task_id,),
        ) == ("approved", None, 0)
        assert _row_tuple(
            writer.connection,
            "SELECT lifecycle_version FROM board_task_contracts WHERE task_id=?",
            (source.task_id,),
        ) == (0,)
        assert _row_tuple(
            writer.connection,
            "SELECT unresolved FROM board_legacy_effect_observations WHERE observation_id=?",
            (source.observation_id,),
        ) == (0,)
        assert (
            writer.connection.execute(
                "SELECT * FROM bank_operation_receipts ORDER BY operation_id"
            ).fetchall()
            == receipt_before
        )
        assert (
            writer.connection.execute(
                "SELECT * FROM bank_operation_legs ORDER BY operation_id,leg_index"
            ).fetchall()
            == legs_before
        )
        assert (
            writer.connection.execute(
                "SELECT event_id,event_source,event_type FROM events ORDER BY event_id"
            ).fetchall()
            == events_before
        )
    finally:
        writer.close()
