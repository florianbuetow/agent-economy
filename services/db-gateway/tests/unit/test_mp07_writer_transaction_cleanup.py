"""MP-07 generic-writer rejections must release their SQLite transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import parse_task_operation_request
from tests.conftest import make_event
from tests.unit import test_mp07_native_write_boundary as boundary

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit


def _native(writer: DbWriter) -> TaskOperationWriter:
    return TaskOperationWriter(
        writer.connection,
        clock_us=lambda: 1,
        deadline_actor_id="system-task-board-deadline",
        platform_public_key="ed25519:test-platform-authority",
    )


def _pending_cancel(writer: DbWriter) -> tuple[str, dict[str, object]]:
    graph = boundary._seed_open_task(writer)
    operation_id = f"op-{uuid4()}"
    request = parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": graph.task_id,
            "command": "cancel_task",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": graph.poster_id,
            "arguments": {
                "poster_id": graph.poster_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": f"task:{graph.task_id}:cancelled",
                "cutoff_us": 4070995200000000,
            },
        }
    )
    claimed = _native(writer).claim_operation(request, boundary._BOARD_KID)
    assert claimed["operation_state"] == "claimed"
    assert claimed["effect_state"] == "pending"
    assert writer.connection.in_transaction is False
    return graph.task_id, claimed


def test_transition_pending_rejection_releases_generic_writer_transaction(
    db_writer: DbWriter,
) -> None:
    task_id, claimed = _pending_cancel(db_writer)

    with pytest.raises(ServiceError) as raised:
        db_writer.update_task_status(
            task_id,
            {
                "updates": {"status": "cancelled"},
                "event": make_event(source="board", event_type="task.cancelled", task_id=task_id),
            },
            {"status": "open"},
        )

    assert raised.value.status_code == 409
    assert raised.value.error == "transition_pending"
    assert raised.value.details == {"winning_operation_id": claimed["operation_id"]}
    assert (
        _native(db_writer).finalize_operation(str(claimed["operation_id"]), boundary._BOARD_KID)
        == claimed
    )
    assert db_writer.connection.in_transaction is False


def test_pending_native_finalization_releases_its_read_transaction(db_writer: DbWriter) -> None:
    _, claimed = _pending_cancel(db_writer)

    assert (
        _native(db_writer).finalize_operation(str(claimed["operation_id"]), boundary._BOARD_KID)
        == claimed
    )
    assert db_writer.connection.in_transaction is False
