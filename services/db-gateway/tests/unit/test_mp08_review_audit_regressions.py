"""Review regressions for delivery/source coherence and worker fencing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from db_gateway_service import task_time
from db_gateway_service.effect_recovery_audit import audit_effect_recovery
from tests.fakes.gateway_mp08_effects import prepare_native_pending
from tests.fakes.native_board_history import FIXED_NOW_US, BusinessClock, TaskGraph, settle_release

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
pytest_plugins = ["tests.fakes.gateway_mp08_app"]


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))


def test_completed_delivery_rejects_claimed_pending_native_source(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
) -> None:
    effect = prepare_native_pending(gateway_mp08_app)
    receipt = settle_release(
        gateway_mp08_app,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.worker_id,
        source_reference=effect.source_reference,
    )
    assert receipt.status_code in {200, 201}, receipt.text
    event_id = receipt.json()["receipt"]["event_id"]
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='completed',"
        "next_attempt_at_us=NULL,completed_at_us=enqueued_at_us,completion_event_id=? "
        "WHERE effect_operation_id=?",
        (event_id, effect.operation_id),
    )
    db_writer.connection.commit()
    result = audit_effect_recovery(db_writer.connection)
    assert not result.recovery_clean
    assert "native_delivery_source_state_mismatch" in {finding.code for finding in result.findings}


def test_audit_rejects_noncanonical_leased_worker_identity(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
) -> None:
    effect = prepare_native_pending(gateway_mp08_app)
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='leased',"
        "next_attempt_at_us=NULL,total_attempt_count=1,cycle_attempt_count=1,"
        "lease_generation=1,lease_owner_id=?,lease_acquired_at_us=enqueued_at_us,"
        "lease_expires_at_us=enqueued_at_us+1 WHERE effect_operation_id=?",
        (
            "recovery-00000000-0000-5000-8000-000000000001",
            effect.operation_id,
        ),
    )
    db_writer.connection.commit()
    result = audit_effect_recovery(db_writer.connection)
    assert not result.recovery_clean
    assert "delivery_worker_identity_invalid" in {finding.code for finding in result.findings}
