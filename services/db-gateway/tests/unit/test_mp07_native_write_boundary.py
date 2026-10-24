"""MP-07 retires generic lifecycle writes once schema v3 is active."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.services.task_publication_writer import TaskPublicationWriter
from db_gateway_service.task_operation_requests import parse_task_operation_request
from db_gateway_service.task_publication_requests import parse_task_publication_request
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event
from tests.fakes.native_publication_fixture import (
    native_publication_payload,
    publish_native_task,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

_BOARD_KID = "gateway-task-board"
_CREATED_AT = "2099-01-01T00:00:00Z"
_BIDDING_DEADLINE = "2099-01-02T00:00:00Z"
_NOW_US = parse_utc_text_to_epoch_us("2099-01-01T00:00:02Z")


@dataclass(frozen=True)
class _Graph:
    task_id: str
    poster_id: str
    worker_id: str
    escrow_id: str


@pytest.fixture
def writer(tmp_path: Path) -> Iterator[DbWriter]:
    value = DbWriter(
        db_path=str(tmp_path / "native-write-boundary.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V5),
        current_task_board_caller_kid=_BOARD_KID,
    )
    yield value
    value.close()


def _snapshot(writer: DbWriter) -> tuple[str, ...]:
    return tuple(writer.connection.iterdump())


def _agent(agent_id: str) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": agent_id,
        "public_key": f"ed25519:{uuid4()}",
        "registered_at": "2098-12-31T23:59:50Z",
        "event": make_event(source="identity", event_type="agent.registered", agent_id=agent_id),
    }


def _seed_financial_graph(writer: DbWriter) -> _Graph:
    suffix = str(uuid4())
    graph = _Graph(
        task_id=f"t-{uuid4()}",
        poster_id=f"a-poster-{suffix}",
        worker_id=f"a-worker-{suffix}",
        escrow_id=f"esc-{uuid4()}",
    )
    for agent_id in (graph.poster_id, graph.worker_id):
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
    writer.create_account(
        {
            "account_id": graph.worker_id,
            "balance": 0,
            "created_at": "2098-12-31T23:59:52Z",
            "event": make_event(
                source="bank", event_type="account.created", agent_id=graph.worker_id
            ),
        }
    )
    return graph


def _publication(graph: _Graph) -> dict[str, object]:
    return native_publication_payload(
        task_id=graph.task_id,
        poster_id=graph.poster_id,
        title="Native write boundary",
        spec="Require every v4 lifecycle mutation to use a native operation.",
        reward=100,
        bidding_deadline_seconds=86400,
        acceptance_window_seconds=86400,
        execution_deadline_seconds=86400,
        review_deadline_seconds=86400,
    )


def _seed_open_task(writer: DbWriter) -> _Graph:
    graph = _seed_financial_graph(writer)
    publish_native_task(
        writer,
        caller_kid=_BOARD_KID,
        clock_us=parse_utc_text_to_epoch_us(_CREATED_AT),
        task_id=graph.task_id,
        poster_id=graph.poster_id,
        escrow_id=graph.escrow_id,
        title="Native write boundary",
        spec="Require every v4 lifecycle mutation to use a native operation.",
        reward=100,
        bidding_deadline_seconds=86400,
        acceptance_window_seconds=86400,
        execution_deadline_seconds=86400,
        review_deadline_seconds=86400,
        lock_timestamp="2098-12-31T23:59:53Z",
    )
    return graph


def _assert_native_operation_required(call: Any) -> None:
    with pytest.raises(ServiceError) as raised:
        call()
    assert raised.value.status_code == 409
    assert raised.value.error == "native_operation_required"


def test_unversioned_bid_writer_is_retired_on_an_idle_native_task(writer: DbWriter) -> None:
    graph = _seed_open_task(writer)
    before = _snapshot(writer)

    _assert_native_operation_required(
        lambda: writer.submit_bid(
            {
                "bid_id": f"bid-{uuid4()}",
                "task_id": graph.task_id,
                "bidder_id": graph.worker_id,
                "proposal": "This generic bid must never enter v3 state.",
                "amount": 90,
                "submitted_at": "2099-01-01T00:00:01Z",
                "event": make_event(
                    source="board", event_type="bid.submitted", task_id=graph.task_id
                ),
            },
            {"status": "open"},
        )
    )

    assert _snapshot(writer) == before
    assert (
        writer.connection.execute(
            "SELECT bid_count FROM board_tasks WHERE task_id = ?", (graph.task_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM board_bids WHERE task_id = ?", (graph.task_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM board_task_operations WHERE task_id = ?", (graph.task_id,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "accepted"},
        {"worker_id": "{worker_id}"},
        {"accepted_bid_id": "bid-injected"},
        {"accepted_at": "2099-01-01T00:00:03Z"},
        {"execution_deadline": "2099-01-02T00:00:03Z"},
        {"submitted_at": "2099-01-01T00:00:03Z"},
        {"review_deadline": "2099-01-02T00:00:03Z"},
        {"approved_at": "2099-01-01T00:00:03Z"},
        {"cancelled_at": "2099-01-01T00:00:03Z"},
        {"dispute_reason": "Injected dispute"},
        {"dispute_id": "clm-injected"},
        {"rebuttal_deadline": "2099-01-02T00:00:03Z"},
        {"rebuttal_submitted_at": "2099-01-01T00:00:03Z"},
        {"disputed_at": "2099-01-01T00:00:03Z"},
        {"ruling_id": "clm-injected"},
        {"worker_pct": 50},
        {"ruling_summary": "Injected ruling"},
        {"ruled_at": "2099-01-01T00:00:03Z"},
        {"expired_at": "2099-01-01T00:00:03Z"},
        {"escrow_pending": 1},
    ],
    ids=lambda value: next(iter(value)),
)
def test_generic_status_writer_cannot_mutate_native_lifecycle_fields(
    writer: DbWriter,
    updates: dict[str, object],
) -> None:
    graph = _seed_open_task(writer)
    resolved_updates = {
        key: graph.worker_id if value == "{worker_id}" else value for key, value in updates.items()
    }
    before = _snapshot(writer)

    _assert_native_operation_required(
        lambda: writer.update_task_status(
            graph.task_id,
            {
                "updates": resolved_updates,
                "event": make_event(
                    source="board", event_type="task.updated", task_id=graph.task_id
                ),
            },
            None,
        )
    )

    assert _snapshot(writer) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "accepted"),
        ("bid_count", 1),
        ("escrow_pending", 1),
        ("dispute_id", "clm-injected"),
    ],
)
def test_publication_cannot_bootstrap_native_lifecycle_state(
    writer: DbWriter,
    field: str,
    value: object,
) -> None:
    graph = _seed_financial_graph(writer)
    publication = {**_publication(graph), field: value}
    before = _snapshot(writer)

    with pytest.raises(ServiceError) as raised:
        TaskPublicationWriter(writer.connection, clock_us=lambda: _NOW_US).admit(
            parse_task_publication_request(publication), _BOARD_KID
        )

    assert raised.value.status_code == 400
    assert raised.value.error == "invalid_payload"
    assert _snapshot(writer) == before
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM board_tasks WHERE task_id = ?", (graph.task_id,)
        ).fetchone()[0]
        == 0
    )


def test_generic_task_creation_writer_remains_retired(writer: DbWriter) -> None:
    graph = _seed_financial_graph(writer)
    before = _snapshot(writer)

    with pytest.raises(ServiceError) as raised:
        writer.create_task(
            {
                "task_id": graph.task_id,
                "poster_id": graph.poster_id,
                "title": "Retired generic creation",
                "spec": "This path must not create current tasks.",
                "reward": 100,
            }
        )

    assert (raised.value.status_code, raised.value.error) == (409, "native_publication_required")
    assert _snapshot(writer) == before
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM board_task_contracts WHERE task_id = ?", (graph.task_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id = ? AND event_type = 'task.created'",
            (graph.task_id,),
        ).fetchone()[0]
        == 0
    )


def test_native_bid_command_still_completes_and_replays(writer: DbWriter) -> None:
    graph = _seed_open_task(writer)
    operation_id = f"op-{uuid4()}"
    bid_id = f"bid-{uuid4()}"
    request = parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": graph.task_id,
            "command": "submit_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": graph.worker_id,
            "arguments": {"bid_id": bid_id, "bidder_id": graph.worker_id, "amount": 90},
        }
    )
    native = TaskOperationWriter(
        writer.connection,
        clock_us=lambda: _NOW_US,
        deadline_actor_id="system-task-board-deadline",
        platform_public_key="ed25519:test-platform-authority",
        current_task_board_caller_kid=_BOARD_KID,
    )

    completed = native.claim_operation(request, _BOARD_KID)
    assert completed["operation_state"] == "completed"
    assert completed["effect_kind"] == "none"
    assert completed["result"] == {
        "task_id": graph.task_id,
        "command": "submit_bid",
        "status": "open",
        "lifecycle_version": 0,
        "event_id": completed["event_id"],
        "bid_id": bid_id,
        "bidder_id": graph.worker_id,
        "amount": 90,
    }
    after_completion = _snapshot(writer)

    assert native.claim_operation(request, _BOARD_KID) == completed
    assert _snapshot(writer) == after_completion
    bid_count = writer.connection.execute(
        "SELECT bid_count FROM board_tasks WHERE task_id = ?", (graph.task_id,)
    ).fetchone()
    bid = writer.connection.execute(
        "SELECT bid_id, bidder_id, amount FROM board_bids WHERE task_id = ?",
        (graph.task_id,),
    ).fetchone()
    operation = writer.connection.execute(
        "SELECT operation_id, operation_state FROM board_task_operations WHERE task_id = ?",
        (graph.task_id,),
    ).fetchone()
    assert bid_count is not None and tuple(bid_count) == (1,)
    assert bid is not None and tuple(bid) == (bid_id, graph.worker_id, 90)
    assert operation is not None and tuple(operation) == (operation_id, "completed")
