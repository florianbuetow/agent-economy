"""T-124 public financial truth derives from receipts, never status alone."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service import task_time
from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.fakes.gateway_mp08_effects import (
    delivery,
    prepare_native_pending,
    prepare_native_refund_pending,
    prepare_native_split_pending,
)
from tests.fakes.native_board_history import (
    FIXED_NOW_US,
    BusinessClock,
    TaskGraph,
    create_task,
    register_worker,
    settle_release,
    settle_split,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


pytestmark = pytest.mark.unit
pytest_plugins = ["tests.fakes.gateway_mp08_app"]

_KEYS = {
    "schema_version",
    "settlement_state",
    "receipt_state",
    "outcome",
    "operation_id",
    "receipt_reference",
    "business_projection_pending",
    "pending_target_status",
}


@pytest.fixture(autouse=True)
def _fixed_business_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))


@pytest.fixture
def app_with_writer(gateway_mp08_app: TestClient) -> TestClient:
    return gateway_mp08_app


def _effect(client: TestClient, task_id: str) -> dict[str, object]:
    state = get_app_state()
    assert state.db_writer is not None
    if state.db_reader is None:
        state.db_reader = DbReader(state.db_writer.connection)
    detail = client.get(f"/board/tasks/{task_id}")
    assert detail.status_code == 200, detail.text
    value = detail.json()["financial_effect"]
    assert set(value) == _KEYS
    listed = client.get("/board/tasks", params={"limit": 100, "offset": 0})
    assert listed.status_code == 200, listed.text
    match = next(item for item in listed.json()["tasks"] if item["task_id"] == task_id)
    assert match["financial_effect"] == value
    return value


def test_open_locked_task_is_locked_without_payment_claim(app_with_writer: TestClient) -> None:
    graph = create_task(app_with_writer)
    assert _effect(app_with_writer, graph.task_id) == {
        "schema_version": 1,
        "settlement_state": "locked",
        "receipt_state": "absent",
        "outcome": None,
        "operation_id": None,
        "receipt_reference": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    }


def test_native_pending_exposes_identity_and_frozen_target(app_with_writer: TestClient) -> None:
    pending = prepare_native_pending(app_with_writer)
    projection = _effect(app_with_writer, pending.task_id)
    assert projection == {
        "schema_version": 1,
        "settlement_state": "pending",
        "receipt_state": "absent",
        "outcome": None,
        "operation_id": pending.operation_id,
        "receipt_reference": None,
        "business_projection_pending": True,
        "pending_target_status": "approved",
    }


def test_visible_exact_receipt_marks_projection_pending_before_adoption(
    app_with_writer: TestClient,
) -> None:
    pending = prepare_native_pending(app_with_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=pending.operation_id,
        recipient_id=pending.worker_id,
        source_reference=f"task:{pending.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}, receipt.text
    projection = _effect(app_with_writer, pending.task_id)
    assert projection["settlement_state"] == "pending"
    assert projection["receipt_state"] == "confirmed"
    assert projection["outcome"] == "worker_paid"
    assert projection["operation_id"] == pending.operation_id
    assert projection["receipt_reference"] == pending.operation_id
    assert projection["business_projection_pending"] is True
    assert projection["pending_target_status"] == "approved"


def test_terminal_status_without_exhaustive_evidence_never_implies_historical_payment(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    pending = prepare_native_pending(app_with_writer)
    db_writer.connection.execute(
        "UPDATE board_tasks SET status='approved' WHERE task_id=?",
        (pending.task_id,),
    )
    db_writer.connection.commit()
    projection = _effect(app_with_writer, pending.task_id)
    assert projection["receipt_state"] == "absent"
    assert projection["outcome"] is None
    assert projection["settlement_state"] != "historical"


@pytest.mark.parametrize("terminal_status", ["approved", "ruled"])
def test_source_free_terminal_task_without_financial_evidence_never_claims_payment(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    terminal_status: str,
) -> None:
    graph = create_task(app_with_writer)
    worker_id = register_worker(app_with_writer)
    db_writer.connection.execute(
        "UPDATE board_tasks SET status=?,worker_id=? WHERE task_id=?",
        (terminal_status, worker_id, graph.task_id),
    )
    db_writer.connection.commit()
    projection = _effect(app_with_writer, graph.task_id)
    assert projection == {
        "schema_version": 1,
        "settlement_state": "attention_required",
        "receipt_state": "absent",
        "outcome": None,
        "operation_id": None,
        "receipt_reference": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    }


def test_dead_letter_without_receipt_requires_attention(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    pending = prepare_native_pending(app_with_writer)
    exists = db_writer.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='board_task_effect_deliveries'"
    ).fetchone()
    assert exists is not None and tuple(exists) == (1,), (
        "Missing V5 effect-delivery table capability"
    )
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter', "
        "next_attempt_at_us=NULL, next_evidence_check_at_us=enqueued_at_us, "
        "last_failure_code='request_timeout', last_failure_at_us=enqueued_at_us "
        "WHERE effect_operation_id=?",
        (pending.operation_id,),
    )
    db_writer.connection.commit()
    projection = _effect(app_with_writer, pending.task_id)
    assert projection["settlement_state"] == "attention_required"
    assert projection["receipt_state"] == "absent"
    assert projection["outcome"] is None
    assert projection["business_projection_pending"] is True


def test_dead_letter_with_late_exact_receipt_is_pending_confirmed_before_due_adoption(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    pending = prepare_native_pending(app_with_writer)
    future_check = FIXED_NOW_US + 60_000_000
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter',"
        "next_attempt_at_us=NULL,next_evidence_check_at_us=?,"
        "last_failure_code='request_timeout',last_failure_at_us=enqueued_at_us "
        "WHERE effect_operation_id=?",
        (future_check, pending.operation_id),
    )
    db_writer.connection.commit()
    receipt = settle_release(
        app_with_writer,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=pending.operation_id,
        recipient_id=pending.worker_id,
        source_reference=pending.source_reference,
    )
    assert receipt.status_code in {200, 201}, receipt.text
    projection = _effect(app_with_writer, pending.task_id)
    assert projection == {
        "schema_version": 1,
        "settlement_state": "pending",
        "receipt_state": "confirmed",
        "outcome": "worker_paid",
        "operation_id": pending.operation_id,
        "receipt_reference": pending.operation_id,
        "business_projection_pending": True,
        "pending_target_status": "approved",
    }
    diagnostic = delivery(app_with_writer, pending.operation_id)
    assert diagnostic["delivery_state"] == "dead_letter"
    assert diagnostic["next_evidence_check_at_us"] == future_check


def test_completed_exact_receipt_is_complete_and_confirmed(
    app_with_writer: TestClient,
) -> None:
    pending = prepare_native_pending(app_with_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=pending.operation_id,
        recipient_id=pending.worker_id,
        source_reference=f"task:{pending.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}
    finalized = app_with_writer.post(
        f"/board/task-operations/{pending.operation_id}/finalize", json={}
    )
    assert finalized.status_code == 200, finalized.text
    assert _effect(app_with_writer, pending.task_id) == {
        "schema_version": 1,
        "settlement_state": "complete",
        "receipt_state": "confirmed",
        "outcome": "worker_paid",
        "operation_id": pending.operation_id,
        "receipt_reference": pending.operation_id,
        "business_projection_pending": False,
        "pending_target_status": None,
    }


def test_incompatible_receipt_is_conflict_without_payment_claim(
    app_with_writer: TestClient,
) -> None:
    pending = prepare_native_pending(app_with_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=pending.operation_id,
        recipient_id=pending.poster_id,
        source_reference=f"task:{pending.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}
    blocked = app_with_writer.post(
        f"/board/task-operations/{pending.operation_id}/finalize", json={}
    )
    assert blocked.status_code == 409
    projection = _effect(app_with_writer, pending.task_id)
    assert projection["settlement_state"] == "conflict"
    assert projection["receipt_state"] == "absent"
    assert projection["outcome"] is None
    assert projection["receipt_reference"] is None


def test_cancellation_refund_maps_confirmed_pending_then_complete(
    app_with_writer: TestClient,
) -> None:
    pending = prepare_native_refund_pending(app_with_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=pending.operation_id,
        recipient_id=pending.poster_id,
        source_reference=f"task:{pending.task_id}:cancelled",
    )
    assert receipt.status_code in {200, 201}, receipt.text
    before = _effect(app_with_writer, pending.task_id)
    assert before == {
        "schema_version": 1,
        "settlement_state": "pending",
        "receipt_state": "confirmed",
        "outcome": "poster_refunded",
        "operation_id": pending.operation_id,
        "receipt_reference": pending.operation_id,
        "business_projection_pending": True,
        "pending_target_status": "cancelled",
    }
    finalized = app_with_writer.post(
        f"/board/task-operations/{pending.operation_id}/finalize", json={}
    )
    assert finalized.status_code == 200, finalized.text
    after = _effect(app_with_writer, pending.task_id)
    assert after == {
        **before,
        "settlement_state": "complete",
        "business_projection_pending": False,
        "pending_target_status": None,
    }


def test_split_ruling_maps_confirmed_pending_then_complete(
    app_with_writer: TestClient,
) -> None:
    pending = prepare_native_split_pending(app_with_writer)
    receipt = settle_split(
        app_with_writer,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=pending.operation_id,
        worker_id=pending.worker_id,
        worker_pct=50,
        source_reference=pending.source_reference,
    )
    assert receipt.status_code in {200, 201}, receipt.text
    before = _effect(app_with_writer, pending.task_id)
    assert before == {
        "schema_version": 1,
        "settlement_state": "pending",
        "receipt_state": "confirmed",
        "outcome": "split",
        "operation_id": pending.operation_id,
        "receipt_reference": pending.operation_id,
        "business_projection_pending": True,
        "pending_target_status": "ruled",
    }
    finalized = app_with_writer.post(
        f"/board/task-operations/{pending.operation_id}/finalize", json={}
    )
    assert finalized.status_code == 200, finalized.text
    after = _effect(app_with_writer, pending.task_id)
    assert after == {
        **before,
        "settlement_state": "complete",
        "business_projection_pending": False,
        "pending_target_status": None,
    }


def test_exact_receipt_with_contradictory_projection_is_conflict_confirmed(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    pending = prepare_native_pending(app_with_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=pending.operation_id,
        recipient_id=pending.worker_id,
        source_reference=f"task:{pending.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}, receipt.text
    db_writer.connection.execute(
        "UPDATE board_tasks SET status='cancelled' WHERE task_id=?",
        (pending.task_id,),
    )
    db_writer.connection.commit()
    blocked = app_with_writer.post(
        f"/board/task-operations/{pending.operation_id}/finalize", json={}
    )
    assert blocked.status_code == 409
    assert _effect(app_with_writer, pending.task_id) == {
        "schema_version": 1,
        "settlement_state": "conflict",
        "receipt_state": "confirmed",
        "outcome": "worker_paid",
        "operation_id": pending.operation_id,
        "receipt_reference": pending.operation_id,
        "business_projection_pending": False,
        "pending_target_status": None,
    }


def test_competing_settlement_winner_is_conflict_absent_for_intended_operation(
    app_with_writer: TestClient,
) -> None:
    pending = prepare_native_pending(app_with_writer)
    competing_id = f"op-{uuid4()}"
    receipt = settle_release(
        app_with_writer,
        TaskGraph(pending.task_id, pending.poster_id, pending.escrow_id),
        operation_id=competing_id,
        recipient_id=pending.poster_id,
        source_reference=f"task:{pending.task_id}:cancelled",
    )
    assert receipt.status_code in {200, 201}, receipt.text
    blocked = app_with_writer.post(
        f"/board/task-operations/{pending.operation_id}/finalize", json={}
    )
    assert blocked.status_code == 409
    projection = _effect(app_with_writer, pending.task_id)
    assert projection["settlement_state"] == "conflict"
    assert projection["receipt_state"] == "absent"
    assert projection["outcome"] is None
    assert projection["receipt_reference"] is None


def test_resolved_escrow_without_intended_receipt_is_conflict_absent(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    pending = prepare_native_pending(app_with_writer)
    db_writer.connection.execute(
        "UPDATE bank_escrow SET status='released',resolved_at='2099-01-01T12:00:02Z' "
        "WHERE escrow_id=?",
        (pending.escrow_id,),
    )
    db_writer.connection.commit()
    blocked = app_with_writer.post(
        f"/board/task-operations/{pending.operation_id}/finalize", json={}
    )
    assert blocked.status_code == 409
    projection = _effect(app_with_writer, pending.task_id)
    assert projection["settlement_state"] == "conflict"
    assert projection["receipt_state"] == "absent"
    assert projection["outcome"] is None
    assert projection["receipt_reference"] is None
