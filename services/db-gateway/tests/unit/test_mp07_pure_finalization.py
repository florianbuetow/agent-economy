"""Pure native task commands are replayable claims, not finalizable effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import (
    TaskOperationRequest,
    parse_task_operation_request,
)
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

_BOARD_KID = "gateway-task-board"
_NOW_US = parse_utc_text_to_epoch_us("2099-01-01T00:00:02Z")


@dataclass(frozen=True)
class _Graph:
    task_id: str
    poster_id: str
    worker_id: str
    second_worker_id: str
    escrow_id: str
    bid_id: str


@pytest.fixture
def writer(tmp_path: Path) -> Iterator[DbWriter]:
    value = DbWriter(
        db_path=str(tmp_path / "pure-finalization.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V3),
    )
    yield value
    value.close()


def _native(writer: DbWriter) -> TaskOperationWriter:
    return TaskOperationWriter(
        writer.connection,
        clock_us=lambda: _NOW_US,
        deadline_actor_id="system-task-board-deadline",
        platform_public_key="ed25519:test-platform-authority",
    )


def _request(payload: dict[str, object]) -> TaskOperationRequest:
    return parse_task_operation_request(payload)


def _agent(agent_id: str) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": agent_id,
        "public_key": f"ed25519:{uuid4()}",
        "registered_at": "2098-12-31T23:59:50Z",
        "event": make_event(source="identity", event_type="agent.registered", agent_id=agent_id),
    }


def _seed_stored_bid_history(writer: DbWriter, graph: _Graph) -> None:
    """Prepare pre-command bid facts directly; this is fixture history, not a writer proof."""
    event = make_event(source="board", event_type="bid.submitted", task_id=graph.task_id)
    cursor = writer.connection.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute(
        "INSERT INTO board_bids (bid_id,task_id,bidder_id,proposal,amount,submitted_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            graph.bid_id,
            graph.task_id,
            graph.worker_id,
            "Complete the pure-command fixture.",
            90,
            "2099-01-01T00:00:01Z",
        ),
    )
    cursor.execute(
        "UPDATE board_tasks SET bid_count=bid_count+1 WHERE task_id=?",
        (graph.task_id,),
    )
    inserted_event = cursor.execute(
        "INSERT INTO events "
        "(event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            event["event_source"],
            event["event_type"],
            event["timestamp"],
            event["task_id"],
            event["agent_id"],
            event["summary"],
            event["payload"],
        ),
    )
    event_id = int(inserted_event.lastrowid)
    writer.connection.commit()
    bid = writer.connection.execute(
        "SELECT bid_id,task_id,bidder_id,proposal,amount,submitted_at FROM board_bids "
        "WHERE bid_id=?",
        (graph.bid_id,),
    ).fetchone()
    stored_event = writer.connection.execute(
        "SELECT event_source,event_type,timestamp,task_id,agent_id,summary,payload "
        "FROM events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    assert bid is not None and tuple(bid) == (
        graph.bid_id,
        graph.task_id,
        graph.worker_id,
        "Complete the pure-command fixture.",
        90,
        "2099-01-01T00:00:01Z",
    )
    assert stored_event is not None and tuple(stored_event) == tuple(event.values())
    assert (
        writer.connection.execute(
            "SELECT bid_count FROM board_tasks WHERE task_id=?", (graph.task_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM board_task_operations WHERE task_id=?", (graph.task_id,)
        ).fetchone()[0]
        == 0
    )


def _seed(writer: DbWriter) -> _Graph:
    suffix = str(uuid4())
    graph = _Graph(
        task_id=f"t-{uuid4()}",
        poster_id=f"a-poster-{suffix}",
        worker_id=f"a-worker-{suffix}",
        second_worker_id=f"a-second-{suffix}",
        escrow_id=f"esc-{uuid4()}",
        bid_id=f"bid-{uuid4()}",
    )
    for agent_id in (graph.poster_id, graph.worker_id, graph.second_worker_id):
        writer.register_agent(_agent(agent_id))
    writer.create_account(
        {
            "operation_id": f"op-{uuid4()}",
            "account_id": graph.poster_id,
            "balance": 100,
            "created_at": "2098-12-31T23:59:51Z",
            "initial_credit": {
                "tx_id": f"tx-{uuid4()}",
                "amount": 100,
                "reference": "initial_balance",
                "timestamp": "2098-12-31T23:59:51Z",
            },
            "event": make_event(
                source="bank", event_type="account.created", agent_id=graph.poster_id
            ),
        }
    )
    for agent_id in (graph.worker_id, graph.second_worker_id):
        writer.create_account(
            {
                "account_id": agent_id,
                "balance": 0,
                "created_at": "2098-12-31T23:59:52Z",
                "event": make_event(source="bank", event_type="account.created", agent_id=agent_id),
            }
        )
    writer.escrow_lock(
        {
            "operation_id": f"op-{uuid4()}",
            "escrow_id": graph.escrow_id,
            "payer_account_id": graph.poster_id,
            "amount": 100,
            "task_id": graph.task_id,
            "created_at": "2098-12-31T23:59:53Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=graph.task_id),
        }
    )
    writer.create_task(
        {
            "task_id": graph.task_id,
            "poster_id": graph.poster_id,
            "title": "Pure finalization contract",
            "spec": "Complete pure commands without an external effect.",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 86400,
            "acceptance_window_seconds": 86400,
            "deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
            "bidding_deadline": "2099-01-02T00:00:00Z",
            "escrow_id": graph.escrow_id,
            "created_at": "2099-01-01T00:00:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=graph.task_id),
        }
    )
    _seed_stored_bid_history(writer, graph)
    return graph


def _accept_payload(graph: _Graph, operation_id: str) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": graph.task_id,
        "command": "accept_bid",
        "expected_status": "open",
        "expected_lifecycle_version": 0,
        "actor_kind": "agent",
        "actor_id": graph.poster_id,
        "arguments": {
            "bid_id": graph.bid_id,
            "worker_id": graph.worker_id,
            "escrow_id": graph.escrow_id,
            "reward": 100,
            "bid_amount": 90,
        },
    }


def _complete_accept(writer: DbWriter, graph: _Graph) -> dict[str, Any]:
    return _native(writer).claim_operation(
        _request(_accept_payload(graph, f"op-{uuid4()}")),
        _BOARD_KID,
    )


def _pure_command(
    writer: DbWriter,
    graph: _Graph,
    command: str,
    operation_id: str,
) -> tuple[TaskOperationRequest, dict[str, Any]]:
    if command == "submit_bid":
        payload: dict[str, object] = {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": graph.task_id,
            "command": command,
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": graph.second_worker_id,
            "arguments": {
                "bid_id": f"bid-{uuid4()}",
                "bidder_id": graph.second_worker_id,
                "amount": 80,
            },
        }
    elif command == "accept_bid":
        payload = _accept_payload(graph, operation_id)
    else:
        accepted = _complete_accept(writer, graph)
        assert accepted["operation_state"] == "completed"
        writer.record_asset(
            {
                "asset_id": f"asset-{uuid4()}",
                "task_id": graph.task_id,
                "uploader_id": graph.worker_id,
                "filename": "deliverable.txt",
                "content_type": "text/plain",
                "size_bytes": 7,
                "storage_path": f"assets/{graph.task_id}/deliverable.txt",
                "content_hash": "a" * 64,
                "uploaded_at": "2099-01-01T00:00:02Z",
                "event": make_event(
                    source="board", event_type="asset.uploaded", task_id=graph.task_id
                ),
            },
            {"status": "accepted", "worker_id": graph.worker_id},
        )
        payload = {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": graph.task_id,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": graph.worker_id,
            "arguments": {"worker_id": graph.worker_id},
        }
    request = _request(payload)
    completed = _native(writer).claim_operation(request, _BOARD_KID)
    return request, completed


def _outcome(call: Any) -> dict[str, object]:
    try:
        result = call()
    except ServiceError as exc:
        return {
            "status": exc.status_code,
            "error": exc.error,
            "details": exc.details,
        }
    return {"kind": "success", "result": result}


@pytest.mark.parametrize("command", ["submit_bid", "accept_bid", "submit_deliverable"])
def test_completed_pure_operation_replays_but_cannot_be_finalized(
    writer: DbWriter,
    command: str,
) -> None:
    graph = _seed(writer)
    operation_id = f"op-{uuid4()}"
    request, completed = _pure_command(writer, graph, command, operation_id)
    assert completed["operation_state"] == "completed"
    assert completed["effect_kind"] == "none"
    assert completed["effect_state"] == "none"
    replay = _native(writer).claim_operation(request, _BOARD_KID)
    assert replay == completed
    before = tuple(writer.connection.iterdump())

    outcome = _outcome(lambda: _native(writer).finalize_operation(operation_id, _BOARD_KID))
    after = tuple(writer.connection.iterdump())

    assert outcome == {
        "status": 409,
        "error": "operation_not_finalizable",
        "details": {"operation_id": operation_id},
    }
    assert after == before
    assert _native(writer).claim_operation(request, _BOARD_KID) == completed


def test_completed_external_operation_finalization_replays_original_result(
    writer: DbWriter,
) -> None:
    graph = _seed(writer)
    _complete_accept(writer, graph)
    writer.record_asset(
        {
            "asset_id": f"asset-{uuid4()}",
            "task_id": graph.task_id,
            "uploader_id": graph.worker_id,
            "filename": "deliverable.txt",
            "content_type": "text/plain",
            "size_bytes": 7,
            "storage_path": f"assets/{graph.task_id}/deliverable.txt",
            "content_hash": "b" * 64,
            "uploaded_at": "2099-01-01T00:00:02Z",
            "event": make_event(source="board", event_type="asset.uploaded", task_id=graph.task_id),
        },
        {"status": "accepted", "worker_id": graph.worker_id},
    )
    submit_payload = {
        "protocol_version": 1,
        "operation_id": f"op-{uuid4()}",
        "task_id": graph.task_id,
        "command": "submit_deliverable",
        "expected_status": "accepted",
        "expected_lifecycle_version": 1,
        "actor_kind": "agent",
        "actor_id": graph.worker_id,
        "arguments": {"worker_id": graph.worker_id},
    }
    _native(writer).claim_operation(_request(submit_payload), _BOARD_KID)
    operation_id = f"op-{uuid4()}"
    approve = _request(
        {
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
                "worker_id": graph.worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": f"task:{graph.task_id}:approved",
            },
        }
    )
    pending = _native(writer).claim_operation(approve, _BOARD_KID)
    assert pending["operation_state"] == "claimed"
    writer.escrow_release(
        {
            "operation_id": operation_id,
            "escrow_id": graph.escrow_id,
            "recipient_account_id": graph.worker_id,
            "source_reference": f"task:{graph.task_id}:approved",
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": "2099-01-01T00:00:03Z",
            "event": make_event(
                source="central-bank",
                event_type="escrow.released",
                task_id=graph.task_id,
            ),
        }
    )
    completed = _native(writer).finalize_operation(operation_id, _BOARD_KID)
    before = tuple(writer.connection.iterdump())

    replay = _native(writer).finalize_operation(operation_id, _BOARD_KID)

    assert replay == completed
    assert tuple(writer.connection.iterdump()) == before
