"""Regression proofs for native MP-07 task-operation persistence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import (
    TaskOperationRequest,
    parse_task_operation_request,
)
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event
from tests.fakes.native_publication_fixture import publish_native_task

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

pytestmark = pytest.mark.unit

_CREATED_AT = "2099-01-01T00:00:00Z"
_BIDDING_DEADLINE = "2099-01-02T00:00:00Z"
_NOW_US = parse_utc_text_to_epoch_us("2099-01-01T00:00:02Z")
_BOARD_KID = "gateway-task-board"
_DEADLINE_ACTOR = "system-task-board-deadline"


@dataclass(frozen=True)
class _Graph:
    task_id: str
    poster_id: str
    worker_id: str
    second_worker_id: str
    escrow_id: str
    bid_id: str


@dataclass(frozen=True)
class _State:
    task: tuple[tuple[object, ...], ...]
    operations: tuple[tuple[object, ...], ...]
    events: tuple[tuple[object, ...], ...]
    counters: tuple[tuple[object, ...], ...]
    ledger: tuple[tuple[object, ...], ...]


@pytest.fixture
def v3_writer(tmp_path: Path) -> Iterator[DbWriter]:
    """Boot an actual DbWriter against a fresh exact-v3 database."""
    writer = DbWriter(
        db_path=str(tmp_path / "operation-regressions.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
        current_task_board_caller_kid=_BOARD_KID,
    )
    yield writer
    writer.close()


def _native(writer: DbWriter) -> TaskOperationWriter:
    return TaskOperationWriter(
        writer.connection,
        clock_us=lambda: _NOW_US,
        deadline_actor_id=_DEADLINE_ACTOR,
        platform_public_key="ed25519:test-platform",
        current_task_board_caller_kid=_BOARD_KID,
    )


def _agent_body(agent_id: str) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": agent_id,
        "public_key": f"ed25519:{uuid4()}",
        "registered_at": "2098-12-31T23:59:55Z",
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
            "Deliver the requested work.",
            100,
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
        "Deliver the requested work.",
        100,
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


def _seed_graph(writer: DbWriter, *, submitted: bool) -> _Graph:
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
        writer.register_agent(_agent_body(agent_id))
    writer.create_account(
        {
            "operation_id": f"op-{uuid4()}",
            "account_id": graph.poster_id,
            "balance": 100,
            "created_at": "2098-12-31T23:59:56Z",
            "initial_credit": {
                "tx_id": f"tx-{uuid4()}",
                "amount": 100,
                "reference": "initial_balance",
                "timestamp": "2098-12-31T23:59:56Z",
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
                "created_at": "2098-12-31T23:59:57Z",
                "event": make_event(source="bank", event_type="account.created", agent_id=agent_id),
            }
        )
    publish_native_task(
        writer,
        caller_kid=_BOARD_KID,
        clock_us=parse_utc_text_to_epoch_us(_CREATED_AT),
        task_id=graph.task_id,
        poster_id=graph.poster_id,
        escrow_id=graph.escrow_id,
        title="MP-07 regression fixture",
        spec="Exercise native operation persistence and receipt finalization.",
        reward=100,
        bidding_deadline_seconds=86400,
        acceptance_window_seconds=86400,
        execution_deadline_seconds=86400,
        review_deadline_seconds=86400,
        lock_timestamp="2098-12-31T23:59:58Z",
    )
    _seed_stored_bid_history(writer, graph)
    if submitted:
        _advance_to_submitted(writer, graph)
    return graph


def _advance_to_submitted(writer: DbWriter, graph: _Graph) -> None:
    native = _native(writer)
    accept = native.claim_operation(
        _request(_accept_payload(graph, f"op-{uuid4()}")),
        _BOARD_KID,
    )
    assert accept["operation_state"] == "completed"
    writer.record_asset(
        {
            "admission": {
                "expected_status": "accepted",
                "expected_lifecycle_version": 1,
                "max_files_per_task": 10,
            },
            "asset_id": f"asset-{uuid4()}",
            "task_id": graph.task_id,
            "uploader_id": graph.worker_id,
            "filename": "deliverable.txt",
            "content_type": "text/plain",
            "size_bytes": 7,
            "storage_path": f"assets/{graph.task_id}/deliverable.txt",
            "content_hash": "a" * 64,
            "uploaded_at": "2099-01-01T00:00:02Z",
            "event": make_event(source="board", event_type="asset.uploaded", task_id=graph.task_id),
        },
        {"status": "accepted", "worker_id": graph.worker_id},
    )
    submit = native.claim_operation(
        _request(
            {
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
        ),
        _BOARD_KID,
    )
    assert submit["operation_state"] == "completed"


def _request(payload: dict[str, object]) -> TaskOperationRequest:
    return parse_task_operation_request(payload)


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
            "bid_amount": 100,
        },
    }


def _approve_payload(
    graph: _Graph,
    operation_id: str,
    *,
    source_reference: str | None = None,
) -> dict[str, object]:
    return {
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
            "source_reference": (
                f"task:{graph.task_id}:approved" if source_reference is None else source_reference
            ),
        },
    }


def _bid_payload(
    graph: _Graph,
    operation_id: str,
    *,
    bid_id: str,
    bidder_id: str,
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": graph.task_id,
        "command": "submit_bid",
        "expected_status": "open",
        "expected_lifecycle_version": 0,
        "actor_kind": "agent",
        "actor_id": bidder_id,
        "arguments": {"bid_id": bid_id, "bidder_id": bidder_id, "amount": 80},
    }


def _claim_approval(writer: DbWriter, graph: _Graph, operation_id: str) -> dict[str, Any]:
    return _native(writer).claim_operation(
        _request(_approve_payload(graph, operation_id)),
        _BOARD_KID,
    )


def _release(
    writer: DbWriter,
    graph: _Graph,
    operation_id: str,
    *,
    recipient_id: str,
    source_reference: str | None = None,
) -> dict[str, Any]:
    return writer.escrow_release(
        {
            "operation_id": operation_id,
            "escrow_id": graph.escrow_id,
            "recipient_account_id": recipient_id,
            "source_reference": (
                f"task:{graph.task_id}:approved" if source_reference is None else source_reference
            ),
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": "2099-01-01T00:00:03Z",
            "event": make_event(
                source="central-bank",
                event_type="escrow.released",
                task_id=graph.task_id,
            ),
        }
    )


def _rows(
    writer: DbWriter,
    sql: str,
    parameters: tuple[object, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(tuple(row) for row in writer.connection.execute(sql, parameters).fetchall())


def _state(writer: DbWriter, task_id: str) -> _State:
    task = _rows(
        writer,
        "SELECT t.status, t.reward, t.worker_id, t.accepted_bid_id, t.accepted_at, "
        "t.submitted_at, t.approved_at, t.cancelled_at, t.expired_at, t.escrow_pending, "
        "c.lifecycle_version, c.execution_cutoff_us, c.review_cutoff_us, "
        "c.accepted_reward, c.accepted_bid_amount FROM board_tasks AS t "
        "JOIN board_task_contracts AS c ON c.task_id=t.task_id WHERE t.task_id=?",
        (task_id,),
    )
    operations = _rows(
        writer,
        "SELECT operation_id, command, canonical_request_json, request_sha256, caller_kid, "
        "source_status, source_lifecycle_version, target_status, target_lifecycle_version, "
        "operation_state, effect_kind, effect_state, effect_operation_id, "
        "effect_result_reference, effect_intent_json, event_id, result_json, error_json "
        "FROM board_task_operations WHERE task_id=? ORDER BY operation_id",
        (task_id,),
    )
    events = _rows(
        writer,
        "SELECT event_id, event_source, event_type, task_id, agent_id, payload FROM events "
        "WHERE task_id=? AND event_source='board' ORDER BY event_id",
        (task_id,),
    )
    counters = _rows(
        writer,
        "SELECT bid_count, (SELECT COUNT(*) FROM board_bids WHERE task_id=t.task_id), "
        "(SELECT COUNT(*) FROM board_assets WHERE task_id=t.task_id) "
        "FROM board_tasks AS t WHERE task_id=?",
        (task_id,),
    )
    ledger = (
        *_rows(
            writer,
            "SELECT 'account', account_id, balance, created_at, NULL, NULL, NULL, NULL "
            "FROM bank_accounts ORDER BY account_id",
            (),
        ),
        *_rows(
            writer,
            "SELECT 'escrow', escrow_id, payer_account_id, task_id, amount, status, resolved_at, "
            "event_id FROM bank_escrow "
            "WHERE task_id=? ORDER BY escrow_id",
            (task_id,),
        ),
        *_rows(
            writer,
            "SELECT 'receipt', operation_id, operation_kind, canonical_request_json, "
            "request_sha256, source_reference, event_id, result_json "
            "FROM bank_operation_receipts WHERE escrow_id IN "
            "(SELECT escrow_id FROM bank_escrow WHERE task_id=?) ORDER BY operation_id",
            (task_id,),
        ),
        *_rows(
            writer,
            "SELECT 'transaction', tx_id, account_id, type, amount, balance_after, reference, "
            "event_id FROM bank_transactions ORDER BY tx_id",
            (),
        ),
        *_rows(
            writer,
            "SELECT 'leg', l.operation_id, l.role, l.account_id, l.direction, l.amount, "
            "l.transaction_id, l.balance_after "
            "FROM bank_operation_legs AS l JOIN bank_operation_receipts AS r "
            "ON r.operation_id=l.operation_id WHERE r.escrow_id IN "
            "(SELECT escrow_id FROM bank_escrow WHERE task_id=?) "
            "ORDER BY l.operation_id, l.leg_index",
            (task_id,),
        ),
    )
    return _State(task, operations, events, counters, ledger)


def _outcome(call: Any) -> dict[str, object]:
    try:
        result = call()
    except ServiceError as exc:
        return {
            "kind": "service_error",
            "status": exc.status_code,
            "error": exc.error,
            "details": exc.details,
        }
    except Exception as exc:
        return {"kind": "exception", "type": type(exc).__name__, "message": str(exc)}
    return {"kind": "success", "result": result}


def _operation_state(writer: DbWriter, operation_id: str) -> tuple[object, ...] | None:
    row = writer.connection.execute(
        "SELECT operation_state, effect_state, error_json FROM board_task_operations "
        "WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    return None if row is None else tuple(row)


def _replace_intent(
    writer: DbWriter,
    graph: _Graph,
    operation_id: str,
    *,
    recipient_id: str,
    expected_amount: int,
) -> None:
    financial = canonical_request(
        "escrow_release",
        {
            "escrow_id": graph.escrow_id,
            "recipient_account_id": recipient_id,
            "source_reference": f"task:{graph.task_id}:approved",
        },
    )
    intent: dict[str, object] = {
        "financial_request": json.loads(financial.canonical_json),
        "financial_request_sha256": financial.request_sha256,
        "expected_escrow": {
            "task_id": graph.task_id,
            "payer_account_id": graph.poster_id,
            "amount": expected_amount,
        },
    }
    cursor = writer.connection.execute(
        "UPDATE board_task_operations SET effect_intent_json=? WHERE operation_id=?",
        (json.dumps(intent, sort_keys=True, separators=(",", ":")), operation_id),
    )
    assert cursor.rowcount == 1
    writer.connection.commit()


def _assert_rejected_finalization(
    writer: DbWriter,
    graph: _Graph,
    operation_id: str,
    before: _State,
    outcome: dict[str, object],
    *,
    details: Mapping[str, object] | None = None,
) -> None:
    after = _state(writer, graph.task_id)
    expected_details: Mapping[str, object] = (
        {"operation_id": operation_id} if details is None else details
    )
    assert {
        "outcome": outcome,
        "task_unchanged": after.task == before.task,
        "events_unchanged": after.events == before.events,
        "counters_unchanged": after.counters == before.counters,
        "ledger_unchanged": after.ledger == before.ledger,
        "operation_state": _operation_state(writer, operation_id),
    } == {
        "outcome": {
            "kind": "service_error",
            "status": 409,
            "error": "effect_evidence_conflict",
            "details": dict(expected_details),
        },
        "task_unchanged": True,
        "events_unchanged": True,
        "counters_unchanged": True,
        "ledger_unchanged": True,
        "operation_state": (
            "blocked",
            "conflict",
            json.dumps(
                {
                    "details": dict(expected_details),
                    "error": "effect_evidence_conflict",
                    "message": "Persisted effect evidence contradicts the claimed task operation",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    }


def test_valid_receipt_finalizes_from_frozen_native_evidence(v3_writer: DbWriter) -> None:
    graph = _seed_graph(v3_writer, submitted=True)
    operation_id = f"op-{uuid4()}"
    pending = _claim_approval(v3_writer, graph, operation_id)
    assert pending["operation_state"] == "claimed"
    _release(v3_writer, graph, operation_id, recipient_id=graph.worker_id)

    completed = _native(v3_writer).finalize_operation(operation_id, _BOARD_KID)
    result = completed["result"]

    assert completed["operation_state"] == "completed"
    assert completed["effect_state"] == "succeeded"
    assert isinstance(result, dict)
    assert result["status"] == "approved"
    assert result["reward"] == 100
    assert _state(v3_writer, graph.task_id).task[0][0] == "approved"


@pytest.mark.parametrize("attack", ["wrong_recipient", "wrong_amount"])
def test_finalizer_rederives_intent_from_command_and_frozen_contract(
    v3_writer: DbWriter,
    attack: str,
) -> None:
    graph = _seed_graph(v3_writer, submitted=True)
    operation_id = f"op-{uuid4()}"
    _claim_approval(v3_writer, graph, operation_id)
    recipient = graph.poster_id if attack == "wrong_recipient" else graph.worker_id
    amount = 80 if attack == "wrong_amount" else 100
    _replace_intent(
        v3_writer,
        graph,
        operation_id,
        recipient_id=recipient,
        expected_amount=amount,
    )
    if attack == "wrong_amount":
        cursor = v3_writer.connection.execute(
            "UPDATE bank_escrow SET amount=? WHERE escrow_id=?", (amount, graph.escrow_id)
        )
        assert cursor.rowcount == 1
        v3_writer.connection.commit()
    _release(v3_writer, graph, operation_id, recipient_id=recipient)
    before = _state(v3_writer, graph.task_id)

    outcome = _outcome(lambda: _native(v3_writer).finalize_operation(operation_id, _BOARD_KID))

    _assert_rejected_finalization(v3_writer, graph, operation_id, before, outcome)


def test_completed_result_uses_accepted_reward_not_mutable_task_reward(
    v3_writer: DbWriter,
) -> None:
    graph = _seed_graph(v3_writer, submitted=True)
    operation_id = f"op-{uuid4()}"
    _claim_approval(v3_writer, graph, operation_id)
    v3_writer.connection.execute(
        "UPDATE board_tasks SET reward=70 WHERE task_id=?", (graph.task_id,)
    )
    v3_writer.connection.commit()
    _release(v3_writer, graph, operation_id, recipient_id=graph.worker_id)

    completed = _native(v3_writer).finalize_operation(operation_id, _BOARD_KID)
    result = completed["result"]

    assert isinstance(result, dict)
    assert result["status"] == "approved"
    assert result["reward"] == 100


def _corrupt_receipt_provenance(
    writer: DbWriter,
    operation_id: str,
    corruption: str,
) -> None:
    receipt = writer.connection.execute(
        "SELECT event_id FROM bank_operation_receipts WHERE operation_id=?", (operation_id,)
    ).fetchone()
    leg = writer.connection.execute(
        "SELECT transaction_id FROM bank_operation_legs WHERE operation_id=?", (operation_id,)
    ).fetchone()
    assert receipt is not None and leg is not None
    event_id = int(receipt[0])
    transaction_id = str(leg[0])
    mutations: dict[str, tuple[str, tuple[object, ...]]] = {
        "event_source": ("UPDATE events SET event_source='court' WHERE event_id=?", (event_id,)),
        "event_type": (
            "UPDATE events SET event_type='escrow.split' WHERE event_id=?",
            (event_id,),
        ),
        "event_task": ("UPDATE events SET task_id='t-unrelated' WHERE event_id=?", (event_id,)),
        "transaction_account": (
            "UPDATE bank_transactions SET account_id=(SELECT payer_account_id FROM bank_escrow "
            "WHERE escrow_id=(SELECT escrow_id FROM bank_operation_receipts WHERE operation_id=?)) "
            "WHERE tx_id=?",
            (operation_id, transaction_id),
        ),
        "transaction_type": (
            "UPDATE bank_transactions SET type='credit' WHERE tx_id=?",
            (transaction_id,),
        ),
        "transaction_amount": (
            "UPDATE bank_transactions SET amount=99 WHERE tx_id=?",
            (transaction_id,),
        ),
        "transaction_reference": (
            "UPDATE bank_transactions SET reference='esc-unrelated' WHERE tx_id=?",
            (transaction_id,),
        ),
        "transaction_event": (
            "UPDATE bank_transactions SET event_id=(SELECT event_id FROM bank_escrow WHERE "
            "escrow_id=(SELECT escrow_id FROM bank_operation_receipts WHERE operation_id=?)) "
            "WHERE tx_id=?",
            (operation_id, transaction_id),
        ),
        "leg_balance": (
            "UPDATE bank_operation_legs SET balance_after=999 WHERE operation_id=?",
            (operation_id,),
        ),
    }
    sql, parameters = mutations[corruption]
    cursor = writer.connection.execute(sql, parameters)
    assert cursor.rowcount == 1
    writer.connection.commit()


@pytest.mark.parametrize(
    "corruption",
    [
        "event_source",
        "event_type",
        "event_task",
        "transaction_account",
        "transaction_type",
        "transaction_amount",
        "transaction_reference",
        "transaction_event",
        "leg_balance",
    ],
)
def test_finalizer_rejects_semantically_inconsistent_receipt_provenance(
    v3_writer: DbWriter,
    corruption: str,
) -> None:
    graph = _seed_graph(v3_writer, submitted=True)
    operation_id = f"op-{uuid4()}"
    _claim_approval(v3_writer, graph, operation_id)
    _release(v3_writer, graph, operation_id, recipient_id=graph.worker_id)
    _corrupt_receipt_provenance(v3_writer, operation_id, corruption)
    before = _state(v3_writer, graph.task_id)

    outcome = _outcome(lambda: _native(v3_writer).finalize_operation(operation_id, _BOARD_KID))

    _assert_rejected_finalization(v3_writer, graph, operation_id, before, outcome)


def test_foreign_settlement_conflict_persists_true_winner_across_replays(
    v3_writer: DbWriter,
) -> None:
    graph = _seed_graph(v3_writer, submitted=True)
    operation_id = f"op-{uuid4()}"
    winner_id = f"op-{uuid4()}"
    request = _request(_approve_payload(graph, operation_id))
    _native(v3_writer).claim_operation(request, _BOARD_KID)
    _release(v3_writer, graph, winner_id, recipient_id=graph.worker_id)
    before = _state(v3_writer, graph.task_id)

    first = _outcome(lambda: _native(v3_writer).finalize_operation(operation_id, _BOARD_KID))
    after_first = _state(v3_writer, graph.task_id)
    claim_replay = _outcome(lambda: _native(v3_writer).claim_operation(request, _BOARD_KID))
    finalize_replay = _outcome(
        lambda: _native(v3_writer).finalize_operation(operation_id, _BOARD_KID)
    )
    after_replays = _state(v3_writer, graph.task_id)
    expected = {
        "kind": "service_error",
        "status": 409,
        "error": "effect_evidence_conflict",
        "details": {"operation_id": operation_id, "winning_operation_id": winner_id},
    }

    assert {
        "first": first,
        "claim_replay": claim_replay,
        "finalize_replay": finalize_replay,
        "task_unchanged": after_replays.task == before.task,
        "events_unchanged": after_replays.events == before.events,
        "counters_unchanged": after_replays.counters == before.counters,
        "ledger_unchanged": after_replays.ledger == before.ledger,
        "replays_stable": after_replays == after_first,
    } == {
        "first": expected,
        "claim_replay": expected,
        "finalize_replay": expected,
        "task_unchanged": True,
        "events_unchanged": True,
        "counters_unchanged": True,
        "ledger_unchanged": True,
        "replays_stable": True,
    }


def test_distinct_native_bid_is_a_valid_control(v3_writer: DbWriter) -> None:
    graph = _seed_graph(v3_writer, submitted=False)
    operation_id = f"op-{uuid4()}"

    completed = _native(v3_writer).claim_operation(
        _request(
            _bid_payload(
                graph,
                operation_id,
                bid_id=f"bid-{uuid4()}",
                bidder_id=graph.second_worker_id,
            )
        ),
        _BOARD_KID,
    )

    state = _state(v3_writer, graph.task_id)
    assert completed["operation_state"] == "completed"
    assert completed["result"]["status"] == "open"
    assert state.counters == ((2, 2, 0),)
    assert state.task[0][10] == 0


@pytest.mark.parametrize("collision", ["bidder", "bid_id"])
def test_native_bid_collisions_are_typed_and_rollback_every_effect(
    v3_writer: DbWriter,
    collision: str,
) -> None:
    graph = _seed_graph(v3_writer, submitted=False)
    operation_id = f"op-{uuid4()}"
    bid_id = f"bid-{uuid4()}" if collision == "bidder" else graph.bid_id
    bidder_id = graph.worker_id if collision == "bidder" else graph.second_worker_id
    request = _request(_bid_payload(graph, operation_id, bid_id=bid_id, bidder_id=bidder_id))
    before = _state(v3_writer, graph.task_id)

    outcome = _outcome(lambda: _native(v3_writer).claim_operation(request, _BOARD_KID))
    after = _state(v3_writer, graph.task_id)

    assert {
        "outcome": outcome,
        "task_unchanged": after.task == before.task,
        "operations_unchanged": after.operations == before.operations,
        "events_unchanged": after.events == before.events,
        "counters_unchanged": after.counters == before.counters,
        "ledger_unchanged": after.ledger == before.ledger,
        "operation_absent": _operation_state(v3_writer, operation_id) is None,
    } == {
        "outcome": {"kind": "service_error", "status": 409, "error": "bid_exists", "details": {}},
        "task_unchanged": True,
        "operations_unchanged": True,
        "events_unchanged": True,
        "counters_unchanged": True,
        "ledger_unchanged": True,
        "operation_absent": True,
    }


def test_canonical_source_reference_is_a_valid_pending_control(v3_writer: DbWriter) -> None:
    graph = _seed_graph(v3_writer, submitted=True)
    operation_id = f"op-{uuid4()}"
    before = _state(v3_writer, graph.task_id)

    pending = _native(v3_writer).claim_operation(
        _request(_approve_payload(graph, operation_id)),
        _BOARD_KID,
    )
    after = _state(v3_writer, graph.task_id)

    assert pending["operation_state"] == "claimed"
    assert pending["effect_state"] == "pending"
    assert after.task == before.task
    assert after.events == before.events
    assert after.counters == before.counters
    assert after.ledger == before.ledger
    assert len(after.operations) == len(before.operations) + 1


@pytest.mark.parametrize("substitution", ["wrong_task", "wrong_action"])
def test_caller_cannot_choose_financial_source_reference(
    v3_writer: DbWriter,
    substitution: str,
) -> None:
    graph = _seed_graph(v3_writer, submitted=True)
    operation_id = f"op-{uuid4()}"
    source_reference = (
        "task:t-unrelated:approved"
        if substitution == "wrong_task"
        else f"task:{graph.task_id}:cancelled"
    )
    request = _request(_approve_payload(graph, operation_id, source_reference=source_reference))
    before = _state(v3_writer, graph.task_id)

    outcome = _outcome(lambda: _native(v3_writer).claim_operation(request, _BOARD_KID))
    after = _state(v3_writer, graph.task_id)

    assert {
        "outcome": outcome,
        "task_unchanged": after.task == before.task,
        "operations_unchanged": after.operations == before.operations,
        "events_unchanged": after.events == before.events,
        "counters_unchanged": after.counters == before.counters,
        "ledger_unchanged": after.ledger == before.ledger,
        "operation_absent": _operation_state(v3_writer, operation_id) is None,
    } == {
        "outcome": {
            "kind": "service_error",
            "status": 409,
            "error": "payload_mismatch",
            "details": {},
        },
        "task_unchanged": True,
        "operations_unchanged": True,
        "events_unchanged": True,
        "counters_unchanged": True,
        "ledger_unchanged": True,
        "operation_absent": True,
    }


def test_regression_harness_uses_real_sqlite_writer(v3_writer: DbWriter) -> None:
    assert isinstance(v3_writer.connection, sqlite3.Connection)
    assert v3_writer.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
