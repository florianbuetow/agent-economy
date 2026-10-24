"""Review regression for combined effective-due queue ordering."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from db_gateway_service import task_time
from tests.fakes.gateway_mp08_effects import lease, prepare_native_pending
from tests.fakes.native_board_history import FIXED_NOW_US, BusinessClock

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
pytest_plugins = ["tests.fakes.gateway_mp08_app"]


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))


def test_earlier_pending_leases_before_later_expired_overflow_candidate(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = prepare_native_pending(gateway_mp08_app)
    expired = prepare_native_pending(gateway_mp08_app)
    now = 253_402_300_749_999_999
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET next_attempt_at_us=? WHERE effect_operation_id=?",
        (FIXED_NOW_US, pending.operation_id),
    )
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='leased',"
        "next_attempt_at_us=NULL,total_attempt_count=1,cycle_attempt_count=1,"
        "lease_generation=1,lease_owner_id=?,lease_acquired_at_us=enqueued_at_us,"
        "lease_expires_at_us=? WHERE effect_operation_id=?",
        (
            "recovery-00000000-0000-4000-8000-000000000001",
            now,
            expired.operation_id,
        ),
    )
    db_writer.connection.commit()
    monkeypatch.setattr(task_time, "utc_now_us", lambda: now)
    response = lease(
        gateway_mp08_app,
        "recovery-00000000-0000-4000-8000-000000000002",
    )
    assert response.json()["delivery"]["effect_operation_id"] == pending.operation_id
    stored = db_writer.connection.execute(
        "SELECT delivery_state,last_failure_code FROM board_task_effect_deliveries "
        "WHERE effect_operation_id=?",
        (expired.operation_id,),
    ).fetchone()
    assert stored is not None and tuple(stored) == ("leased", None)
