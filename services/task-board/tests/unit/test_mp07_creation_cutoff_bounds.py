"""MP-07 exact composed acceptance-cutoff bounds at task creation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from service_commons.exceptions import ServiceError

from task_board_service.task_operation_contract import MAX_INTEGER
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_NOW = "2099-01-01T00:00:00Z"
_BIDDING_SECONDS = 60
_CREATED = datetime.fromisoformat(_NOW.replace("Z", "+00:00"))
_MAX_UTC = datetime.max.replace(tzinfo=UTC)
_MAX_DELTA = _MAX_UTC - _CREATED
_MAX_COMPOSED_SECONDS = _MAX_DELTA.days * 86_400 + _MAX_DELTA.seconds
_MAX_WINDOW_SECONDS = _MAX_COMPOSED_SECONDS - _BIDDING_SECONDS
_TASK_ID = "t-00000000-0000-4000-8000-000000000901"


@pytest.mark.parametrize(
    "window",
    [MAX_INTEGER // 1_000_000, _MAX_WINDOW_SECONDS + 1],
    ids=["sqlite-integer-sized", "year-10000"],
)
def test_unrepresentable_composed_cutoff_fails_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    window: int,
) -> None:
    with native_task_context(tmp_path, monkeypatch) as native:
        native.clock.value_us = parse_utc_text_to_epoch_us(_NOW)
        before = tuple(native.store.writer.connection.iterdump())

        with pytest.raises(ServiceError) as raised:
            native.open_task(_TASK_ID, TaskTerms(100, _BIDDING_SECONDS, window, 120, 90))

        assert raised.value.status_code == 400
        assert raised.value.error == "invalid_deadline"
        assert "acceptance_window_seconds" in raised.value.message
        assert tuple(native.store.writer.connection.iterdump()) == before
        assert native.store.get_task(_TASK_ID) is None


def test_largest_representable_composed_cutoff_is_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with native_task_context(tmp_path, monkeypatch) as native:
        native.clock.value_us = parse_utc_text_to_epoch_us(_NOW)
        native.open_task(_TASK_ID, TaskTerms(100, _BIDDING_SECONDS, _MAX_WINDOW_SECONDS, 120, 90))

        task = native.store.get_task(_TASK_ID)
        assert task is not None
        assert task["created_at"] == "2099-01-01T00:00:00.000000Z"
        assert task["acceptance_window_seconds"] == _MAX_WINDOW_SECONDS
        assert task["acceptance_cutoff_us"] == parse_utc_text_to_epoch_us(
            "9999-12-31T23:59:59.000000Z"
        )
        connection = native.store.writer.connection
        assert connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_kind='escrow_lock'"
            ).fetchone()[0]
            == 1
        )
