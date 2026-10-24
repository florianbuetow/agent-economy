"""Receipt adoption precedes unused absent-evidence scheduling at the time ceiling."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from db_gateway_service import task_time
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from tests.fakes.gateway_mp08_effects import PendingEffect, delivery, prepare_native_pending
from tests.fakes.gateway_mp08_migration_helpers import database_snapshot
from tests.fakes.native_board_history import (
    FIXED_NOW_US,
    BusinessClock,
    TaskGraph,
    settle_release,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
pytest_plugins = ["tests.fakes.gateway_mp08_app"]

_MAX_TIME = 253_402_300_799_999_999
_WORKER = "recovery-00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))


def _exact_receipt(client: TestClient, effect: PendingEffect) -> None:
    receipt = settle_release(
        client,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.worker_id,
        source_reference=effect.source_reference,
    )
    assert receipt.status_code in {200, 201}, receipt.text


def _lease_at_ceiling(client: TestClient) -> object:
    return client.post(
        "/board/task-effect-deliveries/lease",
        json={"protocol_version": 1, "worker_id": _WORKER},
    )


def test_attempt_n_expiry_adopts_exact_receipt_before_overflowing_absent_schedule(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = prepare_native_pending(gateway_mp08_app)
    _exact_receipt(gateway_mp08_app, effect)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='leased',"
        "total_attempt_count=8,cycle_attempt_count=8,lease_generation=8,"
        "next_attempt_at_us=NULL,lease_owner_id=?,lease_acquired_at_us=?,"
        "lease_expires_at_us=? WHERE effect_operation_id=?",
        (_WORKER, _MAX_TIME - 1, _MAX_TIME, effect.operation_id),
    )
    db_writer.connection.commit()
    monkeypatch.setattr(task_time, "utc_now_us", lambda: _MAX_TIME)
    response = _lease_at_ceiling(gateway_mp08_app)
    assert response.status_code == 200, response.text
    assert response.json()["delivery"] is None
    assert delivery(gateway_mp08_app, effect.operation_id)["delivery_state"] == "completed"


@pytest.mark.parametrize("counter", ["total_attempt_count", "lease_generation"])
def test_counter_ceiling_adopts_exact_receipt_before_absent_schedule(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
    counter: str,
) -> None:
    effect = prepare_native_pending(gateway_mp08_app)
    _exact_receipt(gateway_mp08_app, effect)
    db_writer.connection.execute(
        f"UPDATE board_task_effect_deliveries SET {counter}=?,next_attempt_at_us=? "  # nosec B608
        "WHERE effect_operation_id=?",
        (SQLITE_MAX_INTEGER, _MAX_TIME, effect.operation_id),
    )
    db_writer.connection.commit()
    monkeypatch.setattr(task_time, "utc_now_us", lambda: _MAX_TIME)
    response = _lease_at_ceiling(gateway_mp08_app)
    assert response.status_code == 200, response.text
    assert response.json()["delivery"] is None
    assert delivery(gateway_mp08_app, effect.operation_id)["delivery_state"] == "completed"


@pytest.mark.parametrize("exhaustion", ["expiry", "total_attempt_count", "lease_generation"])
def test_absent_evidence_ceiling_overflow_is_503_and_byte_identical(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
    exhaustion: str,
) -> None:
    effect = prepare_native_pending(gateway_mp08_app)
    if exhaustion == "expiry":
        db_writer.connection.execute(
            "UPDATE board_task_effect_deliveries SET delivery_state='leased',"
            "total_attempt_count=8,cycle_attempt_count=8,lease_generation=8,"
            "next_attempt_at_us=NULL,lease_owner_id=?,lease_acquired_at_us=?,"
            "lease_expires_at_us=? WHERE effect_operation_id=?",
            (_WORKER, _MAX_TIME - 1, _MAX_TIME, effect.operation_id),
        )
    else:
        db_writer.connection.execute(
            f"UPDATE board_task_effect_deliveries SET {exhaustion}=?,next_attempt_at_us=? "  # nosec B608
            "WHERE effect_operation_id=?",
            (SQLITE_MAX_INTEGER, _MAX_TIME, effect.operation_id),
        )
    db_writer.connection.commit()
    before = database_snapshot(Path(db_writer._db_path))
    monkeypatch.setattr(task_time, "utc_now_us", lambda: _MAX_TIME)
    response = _lease_at_ceiling(gateway_mp08_app)
    assert response.status_code == 503
    assert response.json()["error"] == "effect_delivery_clock_exhausted"
    assert database_snapshot(Path(db_writer._db_path)) == before
