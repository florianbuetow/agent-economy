"""MP-07 exact composed acceptance-cutoff bounds at task creation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.task_operation_contract import MAX_INTEGER
from tests.unit.test_mp07_creation_window import creation_boundary

if TYPE_CHECKING:
    from typing import Any

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
__all__ = ["creation_boundary"]

_NOW = "2099-01-01T00:00:00Z"
_BIDDING_SECONDS = 60
_CREATED = datetime.fromisoformat(_NOW.replace("Z", "+00:00"))
_MAX_UTC = datetime.max.replace(tzinfo=UTC)
_MAX_DELTA = _MAX_UTC - _CREATED
_MAX_COMPOSED_SECONDS = _MAX_DELTA.days * 86_400 + _MAX_DELTA.seconds
_MAX_WINDOW_SECONDS = _MAX_COMPOSED_SECONDS - _BIDDING_SECONDS


@pytest.mark.parametrize(
    "window",
    [MAX_INTEGER // 1_000_000, _MAX_WINDOW_SECONDS + 1],
    ids=["sqlite-integer-sized", "year-10000"],
)
async def test_unrepresentable_composed_cutoff_fails_before_effects(
    request: pytest.FixtureRequest,
    window: int,
) -> None:
    creation_boundary: tuple[Any, Any, Any, dict[str, Any]] = request.getfixturevalue(
        "creation_boundary"
    )
    coordinator, manager, store, payload = creation_boundary
    payload["acceptance_window_seconds"] = window

    with pytest.raises(ServiceError) as raised:
        await coordinator.create_task("task-token", "escrow-token")

    assert raised.value.status_code == 400
    assert raised.value.error == "invalid_deadline"
    assert "acceptance_window_seconds" in raised.value.message
    assert manager.token_validator.decode_escrow_token_payload.call_count == 0
    assert manager.central_bank_client.escrow_lock.await_count == 0
    assert store.inserted == []


async def test_largest_representable_composed_cutoff_is_retained(
    request: pytest.FixtureRequest,
) -> None:
    creation_boundary: tuple[Any, Any, Any, dict[str, Any]] = request.getfixturevalue(
        "creation_boundary"
    )
    coordinator, manager, store, payload = creation_boundary
    payload["acceptance_window_seconds"] = _MAX_WINDOW_SECONDS

    await coordinator.create_task("task-token", "escrow-token")

    assert manager.central_bank_client.escrow_lock.await_count == 1
    assert len(store.inserted) == 1
    assert store.inserted[0]["created_at"] == _NOW
    assert store.inserted[0]["acceptance_window_seconds"] == _MAX_WINDOW_SECONDS
