"""T-122 Court evidence and platform-authority contract for native operations."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_auth.signing import generate_keypair, public_key_to_b64
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
from tests.fakes.native_publication_fixture import publish_native_task

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

_BOARD_KID = "gateway-task-board"
_DEADLINE_ACTOR = "system-task-board-deadline"
_NOW_US = parse_utc_text_to_epoch_us("2099-01-01T00:00:03Z")
_REASON = "The submitted deliverable does not satisfy the frozen specification."


@dataclass(frozen=True)
class _Authority:
    agent_id: str
    public_key: str


@dataclass(frozen=True)
class _Graph:
    task_id: str
    poster_id: str
    worker_id: str
    other_id: str
    escrow_id: str
    bid_id: str


@dataclass(frozen=True)
class _State:
    task: tuple[object, ...]
    operation_rows: tuple[tuple[object, ...], ...]
    board_events: tuple[tuple[object, ...], ...]
    court_rows: tuple[tuple[object, ...], ...]
    ledger_rows: tuple[tuple[object, ...], ...]


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
            "Complete the submitted Court fixture.",
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
        "Complete the submitted Court fixture.",
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


@pytest.fixture
def v3_writer(tmp_path: Path) -> Iterator[DbWriter]:
    writer = DbWriter(
        db_path=str(tmp_path / "court-operation-contract.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
        current_task_board_caller_kid=_BOARD_KID,
    )
    yield writer
    writer.close()


@pytest.fixture
def platform_authority(tmp_path: Path) -> _Authority:
    _private_key, public_key = generate_keypair("economic-platform", tmp_path)
    return _Authority(
        agent_id=f"a-platform-{uuid4()}",
        public_key=f"ed25519:{public_key_to_b64(public_key)}",
    )


def _native(writer: DbWriter, authority: _Authority) -> TaskOperationWriter:
    return TaskOperationWriter(
        writer.connection,
        clock_us=lambda: _NOW_US,
        deadline_actor_id=_DEADLINE_ACTOR,
        platform_public_key=authority.public_key,
        current_task_board_caller_kid=_BOARD_KID,
    )


@contextmanager
def _isolated_writer(database_root: Path, label: str) -> Iterator[DbWriter]:
    """Give one authority scenario its own fresh exact-v3 database."""
    writer = DbWriter(
        db_path=str(database_root / f"court-authority-{label}.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
        current_task_board_caller_kid=_BOARD_KID,
    )
    try:
        yield writer
    finally:
        writer.close()


def _agent_body(
    agent_id: str,
    *,
    public_key: str | None = None,
    name: str | None = None,
) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": agent_id if name is None else name,
        "public_key": f"ed25519:{uuid4()}" if public_key is None else public_key,
        "registered_at": "2098-12-31T23:59:50Z",
        "event": make_event(source="identity", event_type="agent.registered", agent_id=agent_id),
    }


def _seed_submitted(
    writer: DbWriter,
    *,
    authority: _Authority | None,
) -> _Graph:
    suffix = str(uuid4())
    graph = _Graph(
        task_id=f"t-{uuid4()}",
        poster_id=f"a-poster-{suffix}",
        worker_id=f"a-worker-{suffix}",
        other_id=f"a-other-{suffix}",
        escrow_id=f"esc-{uuid4()}",
        bid_id=f"bid-{uuid4()}",
    )
    for agent_id in (graph.poster_id, graph.worker_id, graph.other_id):
        writer.register_agent(_agent_body(agent_id))
    if authority is not None:
        writer.register_agent(
            _agent_body(
                authority.agent_id,
                public_key=authority.public_key,
                name="A display name with no authority semantics",
            )
        )
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
    for agent_id in (graph.worker_id, graph.other_id):
        writer.create_account(
            {
                "account_id": agent_id,
                "balance": 0,
                "created_at": "2098-12-31T23:59:52Z",
                "event": make_event(source="bank", event_type="account.created", agent_id=agent_id),
            }
        )
    publish_native_task(
        writer,
        caller_kid=_BOARD_KID,
        clock_us=parse_utc_text_to_epoch_us("2099-01-01T00:00:00Z"),
        task_id=graph.task_id,
        poster_id=graph.poster_id,
        escrow_id=graph.escrow_id,
        title="MP-07 Court contract fixture",
        spec="A submitted fixture for Court operation evidence.",
        reward=100,
        bidding_deadline_seconds=86400,
        acceptance_window_seconds=86400,
        execution_deadline_seconds=86400,
        review_deadline_seconds=86400,
        lock_timestamp="2098-12-31T23:59:53Z",
    )
    _seed_stored_bid_history(writer, graph)
    # Explicit phase fixture: this file proves Court transitions, not the public
    # accept/asset/submit journey already covered by the lifecycle race suites.
    writer.connection.execute(
        "UPDATE board_tasks SET status='submitted', worker_id=?, accepted_bid_id=?, "
        "accepted_at='2099-01-01T00:00:01Z', execution_deadline='2099-01-02T00:00:01Z', "
        "submitted_at='2099-01-01T00:00:02Z', review_deadline='2099-01-02T00:00:02Z' "
        "WHERE task_id=?",
        (graph.worker_id, graph.bid_id, graph.task_id),
    )
    writer.connection.execute(
        "UPDATE board_task_contracts SET lifecycle_version=2, "
        "execution_cutoff_us=?, review_cutoff_us=?, accepted_reward=100, "
        "accepted_bid_amount=100 WHERE task_id=?",
        (
            parse_utc_text_to_epoch_us("2099-01-02T00:00:01Z"),
            parse_utc_text_to_epoch_us("2099-01-02T00:00:02Z"),
            graph.task_id,
        ),
    )
    writer.connection.commit()
    return graph


def _request(payload: dict[str, object]) -> TaskOperationRequest:
    return parse_task_operation_request(payload)


def _dispute_payload(graph: _Graph, operation_id: str) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": graph.task_id,
        "command": "dispute_task",
        "expected_status": "submitted",
        "expected_lifecycle_version": 2,
        "actor_kind": "agent",
        "actor_id": graph.poster_id,
        "arguments": {
            "poster_id": graph.poster_id,
            "worker_id": graph.worker_id,
            "escrow_id": graph.escrow_id,
            "reward": 100,
            "reason": _REASON,
        },
    }


def _ruling_payload(
    graph: _Graph,
    operation_id: str,
    authority_id: str,
    claim_id: str,
    worker_pct: int,
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": graph.task_id,
        "command": "record_ruling",
        "expected_status": "disputed",
        "expected_lifecycle_version": 3,
        "actor_kind": "platform",
        "actor_id": authority_id,
        "arguments": {
            "poster_id": graph.poster_id,
            "worker_id": graph.worker_id,
            "escrow_id": graph.escrow_id,
            "reward": 100,
            "ruling_id": claim_id,
            "worker_pct": worker_pct,
            "summary": "Court resolved the frozen claim evidence.",
            "source_reference": f"task:{graph.task_id}:ruling:{claim_id}",
        },
    }


def _file_claim(
    writer: DbWriter,
    graph: _Graph,
    *,
    claim_id: str,
    claimant_id: str | None = None,
    respondent_id: str | None = None,
    reason: str = _REASON,
) -> dict[str, Any]:
    return writer.file_claim(
        {
            "claim_id": claim_id,
            "task_id": graph.task_id,
            "claimant_id": graph.poster_id if claimant_id is None else claimant_id,
            "respondent_id": graph.worker_id if respondent_id is None else respondent_id,
            "reason": reason,
            "status": "rebuttal_pending",
            "rebuttal_deadline": "2099-01-01T01:00:00Z",
            "filed_at": "2099-01-01T00:00:04Z",
            "event": make_event(source="court", event_type="claim.filed", task_id=graph.task_id),
        }
    )


def _state(writer: DbWriter, graph: _Graph) -> _State:
    task = writer.connection.execute(
        "SELECT status, worker_id, dispute_reason, dispute_id, rebuttal_deadline, "
        "ruling_id, worker_pct, ruling_summary, disputed_at, ruled_at, escrow_pending, "
        "(SELECT lifecycle_version FROM board_task_contracts WHERE task_id=t.task_id) "
        "FROM board_tasks AS t WHERE task_id=?",
        (graph.task_id,),
    ).fetchone()
    assert task is not None
    operation_rows = tuple(
        tuple(row)
        for row in writer.connection.execute(
            "SELECT operation_id, command, actor_kind, actor_id, operation_state, effect_kind, "
            "effect_state, effect_result_reference, effect_intent_json, event_id, result_json, "
            "error_json FROM board_task_operations WHERE task_id=? ORDER BY operation_id",
            (graph.task_id,),
        ).fetchall()
    )
    board_events = tuple(
        tuple(row)
        for row in writer.connection.execute(
            "SELECT event_id, event_type, agent_id, payload FROM events "
            "WHERE task_id=? AND event_source='board' ORDER BY event_id",
            (graph.task_id,),
        ).fetchall()
    )
    court_rows = tuple(
        tuple(row)
        for row in writer.connection.execute(
            "SELECT 'claim', claim_id, claimant_id, respondent_id, reason, status, "
            "rebuttal_deadline, filed_at FROM court_claims WHERE task_id=? "
            "UNION ALL SELECT 'ruling', ruling_id, claim_id, task_id, summary, "
            "CAST(worker_pct AS TEXT), judge_votes, ruled_at FROM court_rulings WHERE task_id=? "
            "ORDER BY 1, 2",
            (graph.task_id, graph.task_id),
        ).fetchall()
    )
    ledger_rows = tuple(
        tuple(row)
        for row in writer.connection.execute(
            "SELECT 'escrow', escrow_id, payer_account_id, task_id, amount, status, resolved_at, "
            "event_id FROM bank_escrow WHERE escrow_id=? "
            "UNION ALL SELECT 'receipt', operation_id, operation_kind, source_reference, "
            "request_sha256, result_json, CAST(event_id AS TEXT), escrow_id "
            "FROM bank_operation_receipts WHERE escrow_id=? ORDER BY 1, 2",
            (graph.escrow_id, graph.escrow_id),
        ).fetchall()
    )
    return _State(tuple(task), operation_rows, board_events, court_rows, ledger_rows)


def _outcome(call: Callable[[], object]) -> dict[str, object]:
    try:
        result = call()
    except ServiceError as exc:
        return {
            "kind": "service_error",
            "status": exc.status_code,
            "error": exc.error,
            "details": exc.details,
        }
    return {"kind": "success", "result": result}


def _prepare_disputed_fixture(
    writer: DbWriter,
    graph: _Graph,
    claim_id: str,
) -> None:
    _file_claim(writer, graph, claim_id=claim_id)
    writer.connection.execute(
        "UPDATE board_tasks SET status='disputed', dispute_reason=?, dispute_id=?, "
        "rebuttal_deadline='2099-01-01T01:00:00Z', disputed_at='2099-01-01T00:00:05Z' "
        "WHERE task_id=?",
        (_REASON, claim_id, graph.task_id),
    )
    writer.connection.execute(
        "UPDATE board_task_contracts SET lifecycle_version=3 WHERE task_id=?",
        (graph.task_id,),
    )
    writer.connection.commit()


def _settle_ruling_effect(
    writer: DbWriter,
    graph: _Graph,
    operation_id: str,
    claim_id: str,
    worker_pct: int,
) -> dict[str, Any]:
    common = {
        "operation_id": operation_id,
        "escrow_id": graph.escrow_id,
        "source_reference": f"task:{graph.task_id}:ruling:{claim_id}",
        "resolved_at": "2099-01-01T00:00:06Z",
        "event": make_event(source="central-bank"),
    }
    if worker_pct in {0, 100}:
        recipient = graph.poster_id if worker_pct == 0 else graph.worker_id
        return writer.escrow_release(
            {
                **common,
                "recipient_account_id": recipient,
                "tx_id": f"tx-{uuid4()}",
                "event": make_event(
                    source="central-bank",
                    event_type="escrow.released",
                ),
            }
        )
    return writer.escrow_split(
        {
            **common,
            "worker_account_id": graph.worker_id,
            "poster_account_id": graph.poster_id,
            "worker_pct": worker_pct,
            "worker_tx_id": f"tx-{uuid4()}",
            "poster_tx_id": f"tx-{uuid4()}",
            "event": make_event(
                source="central-bank",
                event_type="escrow.split",
            ),
        }
    )


def test_court_writer_source_control_persists_exact_claim(
    v3_writer: DbWriter,
    platform_authority: _Authority,
) -> None:
    graph = _seed_submitted(v3_writer, authority=platform_authority)
    claim_id = f"clm-{uuid4()}"

    result = _file_claim(v3_writer, graph, claim_id=claim_id)

    row = v3_writer.connection.execute(
        "SELECT claim_id, task_id, claimant_id, respondent_id, reason, status, "
        "rebuttal_deadline FROM court_claims WHERE claim_id=?",
        (claim_id,),
    ).fetchone()
    assert result["claim_id"] == claim_id
    assert row is not None
    assert tuple(row) == (
        claim_id,
        graph.task_id,
        graph.poster_id,
        graph.worker_id,
        _REASON,
        "rebuttal_pending",
        "2099-01-01T01:00:00Z",
    )


def test_platform_identity_source_control_binds_key_not_display_name(
    v3_writer: DbWriter,
    platform_authority: _Authority,
) -> None:
    graph = _seed_submitted(v3_writer, authority=platform_authority)

    configured = v3_writer.connection.execute(
        "SELECT agent_id, name FROM identity_agents WHERE public_key=?",
        (platform_authority.public_key,),
    ).fetchone()
    arbitrary_name_match = v3_writer.connection.execute(
        "SELECT agent_id FROM identity_agents WHERE name=?",
        ("A display name with no authority semantics",),
    ).fetchone()

    assert configured is not None and arbitrary_name_match is not None
    assert tuple(configured) == (
        platform_authority.agent_id,
        "A display name with no authority semantics",
    )
    assert arbitrary_name_match[0] == platform_authority.agent_id
    assert graph.poster_id != platform_authority.agent_id


def test_platform_identity_source_control_can_be_absent_at_startup(
    v3_writer: DbWriter,
    platform_authority: _Authority,
) -> None:
    _seed_submitted(v3_writer, authority=None)

    row = v3_writer.connection.execute(
        "SELECT agent_id FROM identity_agents WHERE public_key=?",
        (platform_authority.public_key,),
    ).fetchone()

    assert row is None


@pytest.mark.parametrize(
    ("worker_pct", "kind", "roles"),
    [
        (0, "escrow_release", ("recipient",)),
        (50, "escrow_split", ("poster", "worker")),
        (100, "escrow_release", ("recipient",)),
    ],
)
def test_bank_source_control_persists_ruling_share_policy(
    v3_writer: DbWriter,
    platform_authority: _Authority,
    worker_pct: int,
    kind: str,
    roles: tuple[str, ...],
) -> None:
    graph = _seed_submitted(v3_writer, authority=platform_authority)
    claim_id = f"clm-{uuid4()}"
    operation_id = f"op-{uuid4()}"
    _prepare_disputed_fixture(v3_writer, graph, claim_id)

    _settle_ruling_effect(v3_writer, graph, operation_id, claim_id, worker_pct)

    receipt = v3_writer.connection.execute(
        "SELECT operation_kind, source_reference FROM bank_operation_receipts WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    legs = tuple(
        row[0]
        for row in v3_writer.connection.execute(
            "SELECT role FROM bank_operation_legs WHERE operation_id=? ORDER BY role",
            (operation_id,),
        ).fetchall()
    )
    assert receipt is not None
    assert tuple(receipt) == (kind, f"task:{graph.task_id}:ruling:{claim_id}")
    assert legs == roles


def _assert_exact_dispute_flow(
    writer: DbWriter,
    authority: _Authority,
    graph: _Graph,
    operation_id: str,
    request: TaskOperationRequest,
    pending: dict[str, Any],
) -> None:
    native = _native(writer, authority)
    absent = native.finalize_operation(operation_id, _BOARD_KID)
    assert absent == pending
    claim_id = f"clm-{uuid4()}"
    _file_claim(writer, graph, claim_id=claim_id)
    completed = native.finalize_operation(operation_id, _BOARD_KID)
    replay = native.claim_operation(request, _BOARD_KID)
    final_replay = native.finalize_operation(operation_id, _BOARD_KID)
    result = completed["result"]
    state = _state(writer, graph)

    assert completed["operation_state"] == "completed"
    assert completed["effect_state"] == "succeeded"
    assert completed["effect_result_reference"] == claim_id
    assert replay == completed == final_replay
    assert isinstance(result, dict)
    assert result["status"] == "disputed"
    assert result["dispute_id"] == claim_id
    assert state.task[0] == "disputed"
    assert state.task[2:5] == (_REASON, claim_id, "2099-01-01T01:00:00Z")
    assert state.task[11] == 3
    assert sum(row[1] == "task.disputed" for row in state.board_events) == 1


def _assert_wrong_court_evidence_blocks(
    writer: DbWriter,
    authority: _Authority,
    mutation: str,
) -> None:
    graph = _seed_submitted(writer, authority=authority)
    operation_id = f"op-{uuid4()}"
    native = _native(writer, authority)
    pending = native.claim_operation(
        _request(_dispute_payload(graph, operation_id)),
        _BOARD_KID,
    )
    assert pending["operation_state"] == "claimed"
    claim_id = f"clm-{uuid4()}"
    _file_claim(
        writer,
        graph,
        claim_id=claim_id,
        claimant_id=graph.other_id if mutation == "claimant" else None,
        respondent_id=graph.other_id if mutation == "respondent" else None,
        reason="Different persisted reason" if mutation == "reason" else _REASON,
    )
    if mutation == "escrow":
        cursor = writer.connection.execute(
            "UPDATE bank_escrow SET payer_account_id=? WHERE escrow_id=?",
            (graph.other_id, graph.escrow_id),
        )
        assert cursor.rowcount == 1
        writer.connection.commit()
    before = _state(writer, graph)

    outcome = _outcome(lambda: native.finalize_operation(operation_id, _BOARD_KID))
    after = _state(writer, graph)

    assert outcome == {
        "kind": "service_error",
        "status": 409,
        "error": "effect_evidence_conflict",
        "details": {"operation_id": operation_id},
    }
    assert after.task == before.task
    assert after.board_events == before.board_events
    assert after.court_rows == before.court_rows
    assert after.ledger_rows == before.ledger_rows
    operation = next(row for row in after.operation_rows if row[0] == operation_id)
    assert operation[4:7] == ("blocked", "court_claim", "conflict")


def _assert_platform_authority_and_ruling_flows(
    database_root: Path,
    authority: _Authority,
) -> None:
    with _isolated_writer(database_root, "missing-registration") as writer:
        missing_graph = _seed_submitted(writer, authority=None)
        missing_claim = f"clm-{uuid4()}"
        _prepare_disputed_fixture(writer, missing_graph, missing_claim)
        matching_key_count = writer.connection.execute(
            "SELECT COUNT(*) FROM identity_agents WHERE public_key=?",
            (authority.public_key,),
        ).fetchone()
        assert matching_key_count is not None and matching_key_count[0] == 0
        missing_operation = f"op-{uuid4()}"
        missing_before = _state(writer, missing_graph)
        missing = _outcome(
            lambda: _native(writer, authority).claim_operation(
                _request(
                    _ruling_payload(
                        missing_graph,
                        missing_operation,
                        authority.agent_id,
                        missing_claim,
                        100,
                    )
                ),
                _BOARD_KID,
            )
        )
        assert missing == {
            "kind": "service_error",
            "status": 503,
            "error": "platform_identity_not_ready",
            "details": {},
        }
        assert _state(writer, missing_graph) == missing_before
        matching_key_count = writer.connection.execute(
            "SELECT COUNT(*) FROM identity_agents WHERE public_key=?",
            (authority.public_key,),
        ).fetchone()
        assert matching_key_count is not None and matching_key_count[0] == 0

    with _isolated_writer(database_root, "wrong-platform-id") as writer:
        wrong_graph = _seed_submitted(writer, authority=authority)
        wrong_claim = f"clm-{uuid4()}"
        _prepare_disputed_fixture(writer, wrong_graph, wrong_claim)
        wrong_operation = f"op-{uuid4()}"
        wrong_before = _state(writer, wrong_graph)
        wrong = _outcome(
            lambda: _native(writer, authority).claim_operation(
                _request(
                    _ruling_payload(
                        wrong_graph,
                        wrong_operation,
                        wrong_graph.poster_id,
                        wrong_claim,
                        100,
                    )
                ),
                _BOARD_KID,
            )
        )
        assert wrong == {
            "kind": "service_error",
            "status": 403,
            "error": "forbidden",
            "details": {},
        }
        assert _state(writer, wrong_graph) == wrong_before

    for worker_pct in (0, 50, 100):
        with _isolated_writer(database_root, f"ruling-{worker_pct}") as writer:
            graph = _seed_submitted(writer, authority=authority)
            claim_id = f"clm-{uuid4()}"
            _prepare_disputed_fixture(writer, graph, claim_id)
            operation_id = f"op-{uuid4()}"
            request = _request(
                _ruling_payload(
                    graph,
                    operation_id,
                    authority.agent_id,
                    claim_id,
                    worker_pct,
                )
            )
            native = _native(writer, authority)
            pending = native.claim_operation(request, _BOARD_KID)
            assert pending["operation_state"] == "claimed"
            assert pending["effect_kind"] == (
                "bank_release" if worker_pct in {0, 100} else "bank_split"
            )
            assert (
                writer.connection.execute(
                    "SELECT 1 FROM court_rulings WHERE task_id=?", (graph.task_id,)
                ).fetchone()
                is None
            )
            absent_receipt = native.finalize_operation(operation_id, _BOARD_KID)
            assert absent_receipt == pending
            _settle_ruling_effect(writer, graph, operation_id, claim_id, worker_pct)
            completed = native.finalize_operation(operation_id, _BOARD_KID)
            result = completed["result"]
            assert completed["operation_state"] == "completed"
            assert isinstance(result, dict)
            assert result["status"] == "ruled"
            assert result["ruling_id"] == claim_id
            assert result["worker_pct"] == worker_pct
            assert native.claim_operation(request, _BOARD_KID) == completed


def test_native_court_operation_claim_finalization_and_authority_contract(
    v3_writer: DbWriter,
    platform_authority: _Authority,
    tmp_path: Path,
) -> None:
    """Gate all Court scenarios behind the first missing native producer."""
    graph = _seed_submitted(v3_writer, authority=platform_authority)
    operation_id = f"op-{uuid4()}"
    request = _request(_dispute_payload(graph, operation_id))
    before = _state(v3_writer, graph)

    outcome = _outcome(
        lambda: _native(v3_writer, platform_authority).claim_operation(request, _BOARD_KID)
    )
    if outcome.get("error") == "unsupported_operation" and outcome.get("status") == 501:
        assert _state(v3_writer, graph) == before
        pytest.fail(
            "Missing native Court-operation capability: dispute_task must return a claimed "
            "court_claim envelope (HTTP 202); exact/missing/mismatched Court evidence, platform "
            "identity readiness, ruling linkage, and 0/partial/100 settlement scenarios remain "
            "prepared behind this producer gate"
        )
    assert outcome["kind"] == "success"
    pending = outcome["result"]
    assert isinstance(pending, dict)
    assert pending["operation_state"] == "claimed"
    assert pending["effect_kind"] == "court_claim"
    assert pending["effect_state"] == "pending"
    assert pending["effect_operation_id"] is None
    assert pending["event_id"] is None
    assert pending["result"] is None

    _assert_exact_dispute_flow(
        v3_writer,
        platform_authority,
        graph,
        operation_id,
        request,
        pending,
    )
    for mutation in ("claimant", "respondent", "reason", "escrow"):
        _assert_wrong_court_evidence_blocks(v3_writer, platform_authority, mutation)
    _assert_platform_authority_and_ruling_flows(tmp_path, platform_authority)
