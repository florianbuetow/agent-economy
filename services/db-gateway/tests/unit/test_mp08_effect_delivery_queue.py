"""T-124 durable queue scheduling, fencing, retry and fairness acceptance."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service import task_time
from tests.fakes.gateway_mp08_effects import delivery, lease, prepare_native_pending
from tests.fakes.native_board_history import (
    FIXED_NOW_US,
    BusinessClock,
    TaskGraph,
    create_task,
    prepare_submitted,
    settle_release,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
pytest_plugins = ["tests.fakes.gateway_mp08_app"]

_WORKER_A = "recovery-00000000-0000-4000-8000-000000000001"
_WORKER_B = "recovery-00000000-0000-4000-8000-000000000002"


@pytest.fixture(autouse=True)
def _fixed_business_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))


@pytest.fixture
def app_with_writer(gateway_mp08_app: TestClient) -> TestClient:
    return gateway_mp08_app


def _require_delivery_table(writer: DbWriter) -> None:
    exists = writer.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='board_task_effect_deliveries'"
    ).fetchone()
    assert exists is not None and tuple(exists) == (1,), (
        "Missing V5 effect-delivery table capability"
    )


def test_native_claim_atomically_enqueues_same_financial_identity(
    gateway_mp08_app: TestClient,
) -> None:
    effect = prepare_native_pending(gateway_mp08_app)
    row = delivery(gateway_mp08_app, effect.operation_id)
    assert row["effect_operation_id"] == effect.operation_id
    assert row["source_kind"] == "native_task_operation"
    assert row["source_id"] == effect.operation_id
    assert row["effect_kind"] == "bank_release"
    assert row["delivery_state"] == "pending"
    assert row["total_attempt_count"] == 0
    assert row["cycle_attempt_count"] == 0
    assert row["lease_generation"] == 0


def test_delivery_insert_failure_rolls_back_native_claim_and_task_projection(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
) -> None:
    graph = create_task(gateway_mp08_app)
    worker_id, _bid_id = prepare_submitted(gateway_mp08_app, graph)
    _require_delivery_table(db_writer)
    operation_id = f"op-{uuid4()}"
    task_before = db_writer.connection.execute(
        "SELECT status,approved_at,escrow_pending FROM board_tasks WHERE task_id=?",
        (graph.task_id,),
    ).fetchone()
    assert task_before is not None
    db_writer.connection.execute(
        "CREATE TEMP TRIGGER mp08_reject_delivery BEFORE INSERT "
        "ON board_task_effect_deliveries BEGIN SELECT RAISE(ABORT,'mp08 enqueue cut'); END"
    )
    response = gateway_mp08_app.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": graph.task_id,
            "command": "approve_task",
            "expected_status": "submitted",
            "expected_lifecycle_version": 2,
            "actor_kind": "agent",
            "actor_id": graph.poster_id,
            "arguments": {
                "poster_id": graph.poster_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": f"task:{graph.task_id}:approved",
            },
        },
    )
    assert response.status_code == 500
    operation_count = db_writer.connection.execute(
        "SELECT COUNT(*) FROM board_task_operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    delivery_count = db_writer.connection.execute(
        "SELECT COUNT(*) FROM board_task_effect_deliveries WHERE effect_operation_id=?",
        (operation_id,),
    ).fetchone()
    assert operation_count is not None and tuple(operation_count) == (0,)
    assert delivery_count is not None and tuple(delivery_count) == (0,)
    task_after = db_writer.connection.execute(
        "SELECT status,approved_at,escrow_pending FROM board_tasks WHERE task_id=?",
        (graph.task_id,),
    ).fetchone()
    assert task_after is not None
    assert tuple(task_after) == tuple(task_before)


def test_pending_at_exact_now_is_leased_with_fenced_increment(
    app_with_writer: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = BusinessClock(now_us=FIXED_NOW_US + 4_000_000)
    monkeypatch.setattr(task_time, "utc_now_us", clock)
    effect = prepare_native_pending(app_with_writer)
    response = lease(app_with_writer, _WORKER_A)
    assert response.status_code == 200, response.text
    item = response.json()["delivery"]
    assert item["effect_operation_id"] == effect.operation_id
    assert item["worker_id"] == _WORKER_A
    assert item["lease_generation"] == 1
    assert item["total_attempt_count"] == 1
    assert item["cycle_attempt_count"] == 1
    assert item["lease_acquired_at_us"] == clock.now_us
    assert item["lease_expires_at_us"] > item["lease_acquired_at_us"]


@pytest.mark.parametrize(
    ("due_offset", "eligible"),
    [(-1, True), (0, True), (1, False)],
)
def test_pending_due_boundary_before_equal_and_after_now(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    due_offset: int,
    eligible: bool,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET next_attempt_at_us=? WHERE effect_operation_id=?",
        (FIXED_NOW_US + due_offset, effect.operation_id),
    )
    db_writer.connection.commit()
    response = lease(app_with_writer, _WORKER_A)
    item = response.json()["delivery"]
    if eligible:
        assert item is not None
        assert item["effect_operation_id"] == effect.operation_id
    else:
        assert item is None
        assert delivery(app_with_writer, effect.operation_id)["delivery_state"] == "pending"


def test_failure_at_inclusive_budget_dead_letters_only_after_receipt_lookup(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = BusinessClock(now_us=FIXED_NOW_US + 5_000_000)
    monkeypatch.setattr(task_time, "utc_now_us", clock)
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET cycle_attempt_count=8, "
        "total_attempt_count=8 WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/"
        f"{leased['lease_generation']}/fail",
        json={
            "protocol_version": 1,
            "worker_id": _WORKER_A,
            "error_code": "central_bank_unavailable",
        },
    )
    assert response.status_code == 200, response.text
    row = response.json()["delivery"]
    assert row["delivery_state"] == "dead_letter"
    assert row["last_failure_code"] == "central_bank_unavailable"
    assert row["next_evidence_check_at_us"] > clock.now_us


@pytest.mark.parametrize("conflicting", [False, True])
def test_failure_at_inclusive_budget_adopts_or_blocks_authoritative_evidence(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    conflicting: bool,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    _require_delivery_table(db_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.poster_id if conflicting else effect.worker_id,
        source_reference=f"task:{effect.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}, receipt.text
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET total_attempt_count=8,cycle_attempt_count=8 "
        "WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/"
        f"{leased['lease_generation']}/fail",
        json={
            "protocol_version": 1,
            "worker_id": _WORKER_A,
            "error_code": "request_timeout",
        },
    )
    assert response.status_code == (409 if conflicting else 200), response.text
    expected = "integrity_blocked" if conflicting else "completed"
    assert delivery(app_with_writer, effect.operation_id)["delivery_state"] == expected


@pytest.mark.parametrize(
    ("worker", "generation_delta"),
    [(_WORKER_B, 0), (_WORKER_A, -1), (_WORKER_A, 1)],
)
def test_wrong_worker_or_generation_cannot_report_attempt(
    app_with_writer: TestClient,
    worker: str,
    generation_delta: int,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/"
        f"{leased['lease_generation'] + generation_delta}/fail",
        json={
            "protocol_version": 1,
            "worker_id": worker,
            "error_code": "request_timeout",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "effect_delivery_attempt_stale"


def test_dead_letter_retry_preserves_identity_counters_and_failure_history(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter', "
        "next_attempt_at_us=NULL, next_evidence_check_at_us=enqueued_at_us, "
        "last_failure_code='request_timeout', last_failure_at_us=enqueued_at_us, "
        "total_attempt_count=8, cycle_attempt_count=8, lease_generation=8 "
        "WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    before = delivery(app_with_writer, effect.operation_id)
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/retry", json={}
    )
    assert response.status_code == 200, response.text
    after = response.json()["delivery"]
    assert after["effect_operation_id"] == before["effect_operation_id"]
    assert after["delivery_state"] == "pending"
    assert after["total_attempt_count"] == 8
    assert after["cycle_attempt_count"] == 0
    assert after["lease_generation"] == 8
    assert after["last_failure_code"] == "request_timeout"
    assert after["operator_retry_count"] == 1


@pytest.mark.parametrize(
    "counter",
    ["total_attempt_count", "lease_generation", "operator_retry_count"],
)
def test_operator_retry_rejects_each_exhausted_counter_without_mutation(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    counter: str,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    assert counter in {"total_attempt_count", "lease_generation", "operator_retry_count"}
    retry_fields = (
        ",last_operator_retry_at_us=enqueued_at_us,"
        "last_operator_retry_caller_kid='gateway-task-board'"
        if counter == "operator_retry_count"
        else ""
    )
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter',"
        "next_attempt_at_us=NULL,next_evidence_check_at_us=enqueued_at_us,"
        "last_failure_code='request_timeout',last_failure_at_us=enqueued_at_us,"
        f"{counter}=9223372036854775807{retry_fields} "  # nosec B608
        "WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    before = tuple(db_writer.connection.iterdump())
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/retry", json={}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "effect_delivery_counter_exhausted"
    assert tuple(db_writer.connection.iterdump()) == before


@pytest.mark.parametrize("state", ["leased", "completed"])
def test_operator_retry_rejects_leased_and_completed_states(
    app_with_writer: TestClient,
    state: str,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    if state == "leased":
        lease(app_with_writer, _WORKER_A)
    else:
        receipt = settle_release(
            app_with_writer,
            TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
            operation_id=effect.operation_id,
            recipient_id=effect.worker_id,
            source_reference=effect.source_reference,
        )
        assert receipt.status_code in {200, 201}, receipt.text
        finalized = app_with_writer.post(
            f"/board/task-operations/{effect.operation_id}/finalize", json={}
        )
        assert finalized.status_code == 200, finalized.text
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/retry", json={}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "effect_delivery_not_retryable"


def test_integrity_blocked_delivery_is_not_operator_retryable(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='integrity_blocked',"
        "next_attempt_at_us=NULL,last_failure_code='effect_evidence_conflict',"
        "last_failure_at_us=enqueued_at_us WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    before = tuple(db_writer.connection.iterdump())
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/retry", json={}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "effect_delivery_not_retryable"
    assert tuple(db_writer.connection.iterdump()) == before


def test_clock_exhaustion_rolls_back_entire_lease_transaction(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_native_pending(app_with_writer)
    before = tuple(db_writer.connection.iterdump())
    monkeypatch.setattr(task_time, "utc_now_us", lambda: 253402300800000000)
    response = app_with_writer.post(
        "/board/task-effect-deliveries/lease",
        json={"protocol_version": 1, "worker_id": _WORKER_A},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "effect_delivery_clock_exhausted"
    assert tuple(db_writer.connection.iterdump()) == before


def test_future_lease_timestamp_overflow_rolls_back_entire_transaction(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare_native_pending(app_with_writer)
    before = tuple(db_writer.connection.iterdump())
    monkeypatch.setattr(task_time, "utc_now_us", lambda: 253402300799999998)
    response = app_with_writer.post(
        "/board/task-effect-deliveries/lease",
        json={"protocol_version": 1, "worker_id": _WORKER_A},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "effect_delivery_clock_exhausted"
    assert tuple(db_writer.connection.iterdump()) == before


def test_backoff_timestamp_overflow_rolls_back_failure_report(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    failed_at = 253402300799999998
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET lease_acquired_at_us=?,"
        "lease_expires_at_us=? WHERE effect_operation_id=?",
        (failed_at - 1, 253402300799999999, effect.operation_id),
    )
    db_writer.connection.commit()
    monkeypatch.setattr(task_time, "utc_now_us", lambda: failed_at)
    before = tuple(db_writer.connection.iterdump())
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/"
        f"{leased['lease_generation']}/fail",
        json={
            "protocol_version": 1,
            "worker_id": _WORKER_A,
            "error_code": "request_timeout",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"] == "effect_delivery_clock_exhausted"
    assert tuple(db_writer.connection.iterdump()) == before


def test_evidence_reschedule_timestamp_overflow_rolls_back_lease_command(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    due = 253402300799999998
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter',"
        "next_attempt_at_us=NULL,next_evidence_check_at_us=?,"
        "last_failure_code='request_timeout',last_failure_at_us=? "
        "WHERE effect_operation_id=?",
        (due, due, effect.operation_id),
    )
    db_writer.connection.commit()
    monkeypatch.setattr(task_time, "utc_now_us", lambda: due)
    before = tuple(db_writer.connection.iterdump())
    response = app_with_writer.post(
        "/board/task-effect-deliveries/lease",
        json={"protocol_version": 1, "worker_id": _WORKER_A},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "effect_delivery_clock_exhausted"
    assert tuple(db_writer.connection.iterdump()) == before


@pytest.mark.parametrize("counter", ["total_attempt_count", "lease_generation"])
def test_exhausted_first_row_does_not_starve_later_due_work(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    counter: str,
) -> None:
    candidates = [prepare_native_pending(app_with_writer), prepare_native_pending(app_with_writer)]
    first, second = sorted(candidates, key=lambda item: item.operation_id)
    _require_delivery_table(db_writer)
    assert counter in {"total_attempt_count", "lease_generation"}
    db_writer.connection.execute(
        f"UPDATE board_task_effect_deliveries SET {counter}=9223372036854775807 "  # nosec B608
        "WHERE effect_operation_id=?",
        (first.operation_id,),
    )
    db_writer.connection.commit()
    response = lease(app_with_writer, _WORKER_A)
    assert response.status_code == 200, response.text
    leased = response.json()["delivery"]
    assert leased is not None
    assert leased["effect_operation_id"] == second.operation_id
    assert delivery(app_with_writer, first.operation_id)["delivery_state"] == "dead_letter"


@pytest.mark.parametrize("counter", ["total_attempt_count", "lease_generation"])
def test_counter_exhaustion_adopts_exact_receipt_before_dead_letter(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    counter: str,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.worker_id,
        source_reference=f"task:{effect.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}
    assert counter in {"total_attempt_count", "lease_generation"}
    db_writer.connection.execute(
        f"UPDATE board_task_effect_deliveries SET {counter}=9223372036854775807 "  # nosec B608
        "WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    response = lease(app_with_writer, _WORKER_A)
    assert response.json()["delivery"] is None
    assert delivery(app_with_writer, effect.operation_id)["delivery_state"] == "completed"


@pytest.mark.parametrize("counter", ["total_attempt_count", "lease_generation"])
def test_counter_exhaustion_blocks_incompatible_evidence(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    counter: str,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.poster_id,
        source_reference=f"task:{effect.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}
    assert counter in {"total_attempt_count", "lease_generation"}
    db_writer.connection.execute(
        f"UPDATE board_task_effect_deliveries SET {counter}=9223372036854775807 "  # nosec B608
        "WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    response = lease(app_with_writer, _WORKER_A)
    assert response.json()["delivery"] is None
    assert delivery(app_with_writer, effect.operation_id)["delivery_state"] == ("integrity_blocked")


def test_equal_due_rows_use_binary_operation_id_order(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    first = prepare_native_pending(app_with_writer)
    second = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET next_attempt_at_us=? "
        "WHERE effect_operation_id IN (?, ?)",
        (FIXED_NOW_US, first.operation_id, second.operation_id),
    )
    db_writer.connection.commit()
    item = lease(app_with_writer, _WORKER_A).json()["delivery"]
    assert item["effect_operation_id"] == min(first.operation_id, second.operation_id)


def test_due_dead_letter_exact_receipt_is_adopted_before_later_lease(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    adopted = prepare_native_pending(app_with_writer)
    ordinary = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(adopted.task_id, adopted.poster_id, adopted.escrow_id),
        operation_id=adopted.operation_id,
        recipient_id=adopted.worker_id,
        source_reference=f"task:{adopted.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter', "
        "next_attempt_at_us=NULL, next_evidence_check_at_us=enqueued_at_us, "
        "last_failure_code='request_timeout', last_failure_at_us=enqueued_at_us "
        "WHERE effect_operation_id=?",
        (adopted.operation_id,),
    )
    db_writer.connection.commit()
    before = delivery(app_with_writer, adopted.operation_id)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    after = delivery(app_with_writer, adopted.operation_id)
    assert leased["effect_operation_id"] == ordinary.operation_id
    assert after["delivery_state"] == "completed"
    for key in ("total_attempt_count", "cycle_attempt_count", "lease_generation"):
        assert after[key] == before[key]


def test_due_dead_letter_without_receipt_reschedules_and_does_not_starve(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    absent = prepare_native_pending(app_with_writer)
    ordinary = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter', "
        "next_attempt_at_us=NULL, next_evidence_check_at_us=enqueued_at_us, "
        "last_failure_code='request_timeout', last_failure_at_us=enqueued_at_us "
        "WHERE effect_operation_id=?",
        (absent.operation_id,),
    )
    db_writer.connection.commit()
    before = delivery(app_with_writer, absent.operation_id)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    after = delivery(app_with_writer, absent.operation_id)
    assert leased["effect_operation_id"] == ordinary.operation_id
    assert after["delivery_state"] == "dead_letter"
    assert after["next_evidence_check_at_us"] > before["next_evidence_check_at_us"]
    for key in ("total_attempt_count", "cycle_attempt_count", "lease_generation"):
        assert after[key] == before[key]


def test_one_lease_command_inspects_at_most_one_due_dead_letter(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    effects = [prepare_native_pending(app_with_writer), prepare_native_pending(app_with_writer)]
    effects.sort(key=lambda item: item.operation_id)
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='dead_letter', "
        "next_attempt_at_us=NULL, next_evidence_check_at_us=enqueued_at_us, "
        "last_failure_code='request_timeout', last_failure_at_us=enqueued_at_us"
    )
    db_writer.connection.commit()
    before = {item.operation_id: delivery(app_with_writer, item.operation_id) for item in effects}
    response = lease(app_with_writer, _WORKER_A)
    assert response.json()["delivery"] is None
    after = {item.operation_id: delivery(app_with_writer, item.operation_id) for item in effects}
    assert (
        after[effects[0].operation_id]["next_evidence_check_at_us"]
        > (before[effects[0].operation_id]["next_evidence_check_at_us"])
    )
    assert after[effects[1].operation_id] == before[effects[1].operation_id]


def test_first_failure_uses_exact_two_second_backoff(
    app_with_writer: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    failed_at = FIXED_NOW_US + 10_000_000
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=failed_at))
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/"
        f"{leased['lease_generation']}/fail",
        json={
            "protocol_version": 1,
            "worker_id": _WORKER_A,
            "error_code": "request_timeout",
        },
    )
    assert response.status_code == 200, response.text
    row = response.json()["delivery"]
    assert row["delivery_state"] == "pending"
    assert row["next_attempt_at_us"] == failed_at + 2_000_000


def test_later_failure_backoff_is_capped_at_sixty_seconds(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET total_attempt_count=7, "
        "cycle_attempt_count=7 WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    failed_at = FIXED_NOW_US + 20_000_000
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=failed_at))
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/"
        f"{leased['lease_generation']}/fail",
        json={
            "protocol_version": 1,
            "worker_id": _WORKER_A,
            "error_code": "request_timeout",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["delivery"]["next_attempt_at_us"] == failed_at + 60_000_000


def test_expired_lease_at_exact_equality_records_expiry_and_continues(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired, later = sorted(
        [prepare_native_pending(app_with_writer), prepare_native_pending(app_with_writer)],
        key=lambda item: item.operation_id,
    )
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET next_attempt_at_us=? WHERE effect_operation_id=?",
        (FIXED_NOW_US, expired.operation_id),
    )
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET next_attempt_at_us=? WHERE effect_operation_id=?",
        (FIXED_NOW_US + 45_000_000, later.operation_id),
    )
    db_writer.connection.commit()
    first = lease(app_with_writer, _WORKER_A).json()["delivery"]
    assert first["effect_operation_id"] == expired.operation_id
    expiry = first["lease_expires_at_us"]
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=expiry))
    response = lease(app_with_writer, _WORKER_B)
    assert response.json()["delivery"]["effect_operation_id"] == later.operation_id
    row = delivery(app_with_writer, expired.operation_id)
    assert row["last_failure_code"] == "lease_expired"
    assert row["last_failure_at_us"] == expiry


@pytest.mark.parametrize("conflicting", [False, True])
def test_attempt_n_expiry_adopts_or_blocks_authoritative_evidence(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
    conflicting: bool,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    _require_delivery_table(db_writer)
    receipt = settle_release(
        app_with_writer,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.poster_id if conflicting else effect.worker_id,
        source_reference=f"task:{effect.task_id}:approved",
    )
    assert receipt.status_code in {200, 201}, receipt.text
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET total_attempt_count=8,cycle_attempt_count=8 "
        "WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    monkeypatch.setattr(
        task_time,
        "utc_now_us",
        BusinessClock(now_us=leased["lease_expires_at_us"]),
    )
    response = lease(app_with_writer, _WORKER_B)
    assert response.json()["delivery"] is None
    expected = "integrity_blocked" if conflicting else "completed"
    assert delivery(app_with_writer, effect.operation_id)["delivery_state"] == expected


def test_attempt_n_expiry_dead_letters_when_authoritative_receipt_is_absent(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET total_attempt_count=8,cycle_attempt_count=8 "
        "WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    monkeypatch.setattr(
        task_time,
        "utc_now_us",
        BusinessClock(now_us=leased["lease_expires_at_us"]),
    )
    response = lease(app_with_writer, _WORKER_B)
    assert response.json()["delivery"] is None
    row = delivery(app_with_writer, effect.operation_id)
    assert row["delivery_state"] == "dead_letter"
    assert row["last_failure_code"] == "lease_expired"
    assert row["next_evidence_check_at_us"] > leased["lease_expires_at_us"]


def test_expired_lease_cannot_report_failure_directly(
    app_with_writer: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    leased = lease(app_with_writer, _WORKER_A).json()["delivery"]
    monkeypatch.setattr(
        task_time,
        "utc_now_us",
        BusinessClock(now_us=leased["lease_expires_at_us"]),
    )
    response = app_with_writer.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/attempts/"
        f"{leased['lease_generation']}/fail",
        json={
            "protocol_version": 1,
            "worker_id": _WORKER_A,
            "error_code": "request_timeout",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "effect_delivery_attempt_stale"
