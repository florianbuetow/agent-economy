"""MP-07 active-claim and legacy-observation exclusion for generic Board writes."""

from __future__ import annotations

import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import parse_task_operation_request
from tests.conftest import make_event
from tests.fakes.mp05_publication_migration_history import (
    V3Source,
    _with_task_board_authority,
    publish_verified_backup,
)
from tests.unit import test_mp07_legacy_effect_migration as legacy
from tests.unit import test_mp07_migration_contract as parent_contract
from tests.unit.routers import test_mp07_task_operation_boundary as boundary

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _snapshot(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(connection.iterdump())


def _operation_writer(connection: sqlite3.Connection, now_us: int) -> TaskOperationWriter:
    return TaskOperationWriter(connection, lambda: now_us, "a-deadline", "platform-key")


def _claim(
    writer: DbWriter,
    body: dict[str, object],
    *,
    now_us: int,
) -> str:
    request = parse_task_operation_request(body)
    result = _operation_writer(writer.connection, now_us).claim_operation(
        request, "gateway-task-board"
    )
    assert result["operation_state"] == "claimed"
    return request.operation_id


def _assert_transition_pending(
    call: Any,
    *,
    winning_operation_id: str,
) -> None:
    with pytest.raises(ServiceError) as raised:
        call()
    assert raised.value.status_code == 409
    assert raised.value.error == "transition_pending"
    assert raised.value.details == {"winning_operation_id": winning_operation_id}


def test_cancel_claim_blocks_legacy_bid_atomically(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    graph = boundary._seed_graph(app_with_writer)
    bidder_id = f"a-late-{uuid.uuid4()}"
    assert (
        app_with_writer.post("/identity/agents", json=boundary._agent_body(bidder_id)).status_code
        == 201
    )
    operation_id = f"op-{uuid.uuid4()}"
    contract = db_writer.connection.execute(
        "SELECT acceptance_cutoff_us FROM board_task_contracts WHERE task_id=?",
        (graph.task_id,),
    ).fetchone()
    assert contract is not None
    claim_id = _claim(
        db_writer,
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
                "cutoff_us": contract[0],
            },
        },
        now_us=1,
    )
    before = _snapshot(db_writer.connection)
    _assert_transition_pending(
        lambda: db_writer.submit_bid(
            {
                "bid_id": f"bid-{uuid.uuid4()}",
                "task_id": graph.task_id,
                "bidder_id": bidder_id,
                "proposal": "late",
                "amount": 80,
                "submitted_at": "2099-01-01T00:00:02Z",
                "event": make_event(
                    source="board", event_type="bid.submitted", task_id=graph.task_id
                ),
            },
            {"status": "open"},
        ),
        winning_operation_id=claim_id,
    )
    assert _snapshot(db_writer.connection) == before


def _accepted_graph(client: TestClient, writer: DbWriter) -> boundary._Graph:
    graph = boundary._seed_graph(client)
    request = parse_task_operation_request(boundary._accept_command(graph, f"op-{uuid.uuid4()}"))
    result = _operation_writer(writer.connection, 1).claim_operation(request, "gateway-task-board")
    assert result["operation_state"] == "completed"
    return graph


def test_execution_expiry_claim_blocks_legacy_asset_metadata(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    graph = _accepted_graph(app_with_writer, db_writer)
    db_writer.connection.execute(
        "UPDATE board_tasks SET accepted_at='2000-01-01T00:00:00Z', "
        "execution_deadline='2000-01-02T00:00:00Z' WHERE task_id=?",
        (graph.task_id,),
    )
    db_writer.connection.execute(
        "UPDATE board_task_contracts SET execution_cutoff_us=946771200000000 WHERE task_id=?",
        (graph.task_id,),
    )
    db_writer.connection.commit()
    claim_id = _claim(
        db_writer,
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid.uuid4()}",
            "task_id": graph.task_id,
            "command": "expire_execution",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "system",
            "actor_id": "a-deadline",
            "arguments": {
                "poster_id": graph.poster_id,
                "worker_id": graph.worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": f"task:{graph.task_id}:expired",
                "cutoff_us": 946771200000000,
            },
        },
        now_us=946771200000000,
    )
    before = _snapshot(db_writer.connection)
    _assert_transition_pending(
        lambda: db_writer.record_asset(
            {
                "asset_id": f"asset-{uuid.uuid4()}",
                "task_id": graph.task_id,
                "uploader_id": graph.worker_id,
                "filename": "late.txt",
                "content_type": "text/plain",
                "size_bytes": 1,
                "storage_path": "late.txt",
                "content_hash": "0" * 64,
                "uploaded_at": "2000-01-02T00:00:00Z",
                "event": make_event(
                    source="board", event_type="asset.uploaded", task_id=graph.task_id
                ),
            },
            {"status": "accepted"},
        ),
        winning_operation_id=claim_id,
    )
    assert _snapshot(db_writer.connection) == before


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "approved", "approved_at": "2099-01-01T00:00:04Z"},
        {"rebuttal_submitted_at": "2099-01-01T00:00:04Z"},
        {"escrow_pending": 1},
    ],
)
def test_approval_claim_blocks_legacy_status_and_metadata_updates(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    updates: dict[str, object],
) -> None:
    graph = _accepted_graph(app_with_writer, db_writer)
    boundary._advance_claim_fixture_to_submitted(db_writer, graph)
    claim_id = _claim(db_writer, boundary._approve_command(graph, f"op-{uuid.uuid4()}"), now_us=2)
    before = _snapshot(db_writer.connection)
    _assert_transition_pending(
        lambda: db_writer.update_task_status(
            graph.task_id,
            {
                "updates": updates,
                "event": make_event(
                    source="board", event_type="task.updated", task_id=graph.task_id
                ),
            },
            None,
        ),
        winning_operation_id=claim_id,
    )
    assert _snapshot(db_writer.connection) == before


@pytest.mark.parametrize("updates", [{"status": "approved"}, {"escrow_pending": 0}])
def test_unresolved_legacy_observation_blocks_generic_task_writes(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    inventory, backup_root, work_root, database = parent_contract._prepared_v2(tmp_path)
    connection = sqlite3.connect(database)
    try:
        legacy._apply_case(connection, "pending_approved_settlement")
        connection.commit()
    finally:
        connection.close()
    backup_id = "legacy-write-exclusion"
    from db_gateway_service import migration, recovery

    recovery.create_backup(
        inventory, backup_root=backup_root, backup_id=backup_id, work_root=work_root
    )
    v3_result = migration.migrate_v2_to_v3(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v3_result.observation == "committed_v3_verified"
    caller_kid = "gateway-legacy-write-exclusion"
    inventory = _with_task_board_authority(inventory, handle=caller_kid)
    source = V3Source(inventory, backup_root, work_root, database, caller_kid)
    publication_backup_id = "legacy-write-exclusion-v4"
    publish_verified_backup(source, backup_id=publication_backup_id)
    v4_result = migration.migrate_v3_to_v4(
        inventory,
        backup_root=backup_root,
        backup_id=publication_backup_id,
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v4_result.observation == "committed_v4_verified"
    writer = DbWriter(str(database), 5000, "wal", None)
    try:
        observation_id = str(
            writer.connection.execute(
                "SELECT observation_id FROM board_legacy_effect_observations WHERE task_id='t-1'"
            ).fetchone()[0]
        )
        before = _snapshot(writer.connection)
        with pytest.raises(ServiceError) as raised:
            writer.update_task_status(
                "t-1",
                {
                    "updates": updates,
                    "event": make_event(source="board", event_type="task.updated", task_id="t-1"),
                },
                None,
            )
        assert raised.value.status_code == 409
        assert raised.value.error == "legacy_reconciliation_pending"
        assert raised.value.details == {"observation_id": observation_id}
        assert _snapshot(writer.connection) == before
    finally:
        writer.close()
