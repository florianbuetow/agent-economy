"""Review regressions for authority and lease-fence precedence over clocks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from db_gateway_service import task_time
from db_gateway_service.config import get_settings
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


def test_foreign_owner_remains_hidden_when_gateway_clock_is_exhausted(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = prepare_native_pending(gateway_mp08_app)
    authority = gateway_mp08_app.app.state.board_authority
    monkeypatch.setattr(task_time, "utc_now_us", lambda: 253_402_300_800_000_000)
    with pytest.raises(Exception) as raised:
        db_writer.get_effect_delivery(
            effect.operation_id,
            "gateway-task-board-foreign",
            authority,
        )
    assert getattr(raised.value, "status_code", None) == 404


def test_stale_attempt_precedes_unrepresentable_failure_backoff(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = prepare_native_pending(gateway_mp08_app)
    worker_id = "recovery-00000000-0000-4000-8000-000000000001"
    leased = lease(gateway_mp08_app, worker_id).json()["delivery"]
    authority = gateway_mp08_app.app.state.board_authority
    monkeypatch.setattr(task_time, "utc_now_us", lambda: 253_402_300_799_999_998)
    with pytest.raises(Exception) as raised:
        db_writer.fail_effect_delivery(
            effect.operation_id,
            leased["lease_generation"],
            worker_id,
            "request_timeout",
            "gateway-task-board",
            authority,
            get_settings().board_effect_recovery,
        )
    assert getattr(raised.value, "status_code", None) == 409
    assert getattr(raised.value, "error", None) == "effect_delivery_attempt_stale"
