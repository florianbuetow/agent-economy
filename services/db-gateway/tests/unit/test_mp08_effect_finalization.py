"""T-124 exact receipt adoption and atomic source/delivery finalization."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from db_gateway_service import task_time
from db_gateway_service.core.state import get_app_state
from tests.fakes.gateway_mp08_effects import (
    delivery,
    lease,
    prepare_native_pending,
)
from tests.fakes.native_board_history import FIXED_NOW_US, BusinessClock, TaskGraph, settle_release

if TYPE_CHECKING:
    import sqlite3

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
