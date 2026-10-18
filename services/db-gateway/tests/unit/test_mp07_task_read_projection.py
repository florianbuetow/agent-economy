"""MP-07 task reads expose authoritative contracts and recovery state."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_time import parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

_CREATED_AT = "2099-01-01T00:00:00Z"
_BIDDING_DEADLINE = "2099-01-01T00:01:00Z"
_ACCEPTED_AT = "2099-01-01T00:00:10Z"
_EXECUTION_DEADLINE = "2099-01-01T00:02:10Z"
_SUBMITTED_AT = "2099-01-01T00:00:20Z"
_REVIEW_DEADLINE = "2099-01-01T00:01:50Z"


def _seed_submitted(writer: DbWriter) -> dict[str, str]:
    suffix = str(uuid4())
    graph = {
        "task_id": f"t-{suffix}",
        "poster_id": f"a-poster-{suffix}",
        "worker_id": f"a-worker-{suffix}",
        "escrow_id": f"esc-{suffix}",
        "bid_id": f"bid-{suffix}",
    }
    connection = writer.connection
    connection.executemany(
        "INSERT INTO identity_agents VALUES (?, ?, ?, ?)",
        (
            (graph["poster_id"], "Poster", f"ed25519:{uuid4()}", _CREATED_AT),
            (graph["worker_id"], "Worker", f"ed25519:{uuid4()}", _CREATED_AT),
        ),
    )
    connection.executemany(
        "INSERT INTO bank_accounts VALUES (?, ?, ?)",
        ((graph["poster_id"], 0, _CREATED_AT), (graph["worker_id"], 0, _CREATED_AT)),
    )
    connection.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id,payer_account_id,amount,task_id,status,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            graph["escrow_id"],
            graph["poster_id"],
            100,
            graph["task_id"],
            "locked",
            _CREATED_AT,
        ),
    )
    connection.execute(
        "INSERT INTO board_tasks "
        "(task_id,poster_id,title,spec,reward,status,bidding_deadline_seconds,"
        "deadline_seconds,review_deadline_seconds,bidding_deadline,execution_deadline,"
        "review_deadline,bid_count,escrow_pending,escrow_id,worker_id,accepted_bid_id,"
        "created_at,accepted_at,submitted_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            graph["task_id"],
            graph["poster_id"],
            "Read projection",
            "Expose authoritative task-contract fields.",
            100,
            "submitted",
            60,
            120,
            90,
            _BIDDING_DEADLINE,
            _EXECUTION_DEADLINE,
            _REVIEW_DEADLINE,
            1,
            0,
            graph["escrow_id"],
            graph["worker_id"],
            graph["bid_id"],
            _CREATED_AT,
            _ACCEPTED_AT,
            _SUBMITTED_AT,
        ),
    )
    connection.execute(
        "INSERT INTO board_bids VALUES (?,?,?,?,?,?)",
        (
            graph["bid_id"],
            graph["task_id"],
            graph["worker_id"],
            "Deliver the projection.",
            75,
            "2099-01-01T00:00:05Z",
        ),
    )
    connection.execute(
        "INSERT INTO board_task_contracts VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            graph["task_id"],
            3,
            2,
            30,
            parse_utc_text_to_epoch_us(_BIDDING_DEADLINE),
            parse_utc_text_to_epoch_us("2099-01-01T00:01:30Z"),
            parse_utc_text_to_epoch_us(_EXECUTION_DEADLINE),
            parse_utc_text_to_epoch_us(_REVIEW_DEADLINE),
            100,
            75,
        ),
    )
    connection.commit()
    return graph


@pytest.fixture
def task_reader(tmp_path: Path) -> Iterator[tuple[DbWriter, DbReader, dict[str, str]]]:
    writer = DbWriter(
        db_path=str(tmp_path / "task-read-projection.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V3),
    )
    graph = _seed_submitted(writer)
    reader = DbReader(writer.connection)
    yield writer, reader, graph
    writer.close()


def _only_listed(reader: DbReader) -> dict[str, object]:
    tasks = reader.list_tasks(None, None, None, 10, 0)
    assert len(tasks) == 1
    return tasks[0]


def _expected_contract() -> dict[str, object]:
    return {
        "lifecycle_version": 3,
        "timing_contract_version": 2,
        "acceptance_window_seconds": 30,
        "bidding_cutoff_us": parse_utc_text_to_epoch_us(_BIDDING_DEADLINE),
        "acceptance_cutoff_us": parse_utc_text_to_epoch_us("2099-01-01T00:01:30Z"),
        "execution_cutoff_us": parse_utc_text_to_epoch_us(_EXECUTION_DEADLINE),
        "review_cutoff_us": parse_utc_text_to_epoch_us(_REVIEW_DEADLINE),
        "accepted_reward": 100,
        "accepted_bid_amount": 75,
    }


def test_get_and_list_expose_identical_task_contracts(
    task_reader: tuple[DbWriter, DbReader, dict[str, str]],
) -> None:
    _writer, reader, graph = task_reader
    fetched = reader.get_task(graph["task_id"])
    listed = _only_listed(reader)
    assert fetched is not None
    expected = _expected_contract()
    assert {key: fetched[key] for key in expected} == expected
    assert {key: listed[key] for key in expected} == expected
    assert fetched["pending_transition"] is None
    assert listed["pending_transition"] is None
    assert fetched["legacy_reconciliations"] == []
    assert listed["legacy_reconciliations"] == []


def _insert_active_operation(
    writer: DbWriter,
    graph: dict[str, str],
    *,
    operation_state: str,
) -> str:
    operation_id = f"op-{uuid4()}"
    effect_state = "pending" if operation_state == "claimed" else "conflict"
    error_json = None
    if operation_state == "blocked":
        error_json = '{"details":{},"error":"effect_evidence_conflict","message":"blocked"}'
    writer.connection.execute(
        "INSERT INTO board_task_operations "
        "(operation_id,operation_contract_version,task_id,command,canonical_request_json,"
        "request_sha256,caller_kid,actor_kind,actor_id,source_status,"
        "source_lifecycle_version,target_status,target_lifecycle_version,operation_state,"
        "effect_kind,effect_state,effect_operation_id,effect_result_reference,"
        "effect_intent_json,claimed_at_us,completed_at_us,event_id,result_json,error_json) "
        "VALUES (?,1,?,'approve_task','{}',?,'gateway-task-board','agent',?,'submitted',"
        "3,'approved',4,?,'bank_release',?,?,NULL,'{}',1,NULL,NULL,NULL,?)",
        (
            operation_id,
            graph["task_id"],
            "0" * 64,
            graph["poster_id"],
            operation_state,
            effect_state,
            operation_id,
            error_json,
        ),
    )
    writer.connection.commit()
    return operation_id


@pytest.mark.parametrize("operation_state", ["claimed", "blocked"])
def test_active_operation_is_separate_from_unchanged_source_state(
    task_reader: tuple[DbWriter, DbReader, dict[str, str]],
    operation_state: str,
) -> None:
    writer, reader, graph = task_reader
    operation_id = _insert_active_operation(writer, graph, operation_state=operation_state)
    expected = {
        "operation_id": operation_id,
        "command": "approve_task",
        "target_status": "approved",
        "target_lifecycle_version": 4,
        "operation_state": operation_state,
        "effect_state": "pending" if operation_state == "claimed" else "conflict",
    }
    fetched = reader.get_task(graph["task_id"])
    listed = _only_listed(reader)
    assert fetched is not None
    assert fetched["status"] == listed["status"] == "submitted"
    assert fetched["lifecycle_version"] == listed["lifecycle_version"] == 3
    assert fetched["pending_transition"] == listed["pending_transition"] == expected


def _insert_observation(
    writer: DbWriter,
    graph: dict[str, str],
    *,
    observation_id: str,
    observation_kind: str,
    effect_state: str,
    outcome: str,
    unresolved: int,
) -> None:
    fact = json.dumps(
        {"private_cutover_fact": observation_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    writer.connection.execute(
        "INSERT INTO board_legacy_effect_observations VALUES (?,?,?,?,?,?,?,?,?)",
        (
            observation_id,
            graph["task_id"],
            observation_kind,
            fact,
            hashlib.sha256(fact.encode()).hexdigest(),
            f"external-{observation_id}",
            effect_state,
            outcome,
            unresolved,
        ),
    )


def test_unresolved_legacy_reconciliations_are_sorted_and_do_not_fabricate_commands(
    task_reader: tuple[DbWriter, DbReader, dict[str, str]],
) -> None:
    writer, reader, graph = task_reader
    _insert_observation(
        writer,
        graph,
        observation_id=f"obs-{uuid4()}",
        observation_kind="worker_settlement_pending",
        effect_state="pending",
        outcome="approved",
        unresolved=0,
    )
    unresolved = [f"obs-{uuid4()}", f"obs-{uuid4()}"]
    for observation_id, kind, state, outcome in (
        (unresolved[1], "court_claim_succeeded", "succeeded", "disputed"),
        (unresolved[0], "worker_settlement_pending", "pending", "approved"),
    ):
        _insert_observation(
            writer,
            graph,
            observation_id=observation_id,
            observation_kind=kind,
            effect_state=state,
            outcome=outcome,
            unresolved=1,
        )
    writer.connection.commit()
    expected_ids = sorted(unresolved)
    fetched = reader.get_task(graph["task_id"])
    listed = _only_listed(reader)
    assert fetched is not None
    assert fetched["legacy_reconciliations"] == listed["legacy_reconciliations"]
    assert [item["observation_id"] for item in fetched["legacy_reconciliations"]] == expected_ids
    assert all(
        set(item)
        == {
            "observation_id",
            "observation_kind",
            "effect_state",
            "target_outcome_hint",
        }
        for item in fetched["legacy_reconciliations"]
    )
    assert fetched["pending_transition"] is None


def test_completed_native_operation_is_not_projected_as_pending(
    task_reader: tuple[DbWriter, DbReader, dict[str, str]],
) -> None:
    writer, reader, graph = task_reader
    operation_id = f"op-{uuid4()}"
    event = writer.connection.execute(
        "INSERT INTO events "
        "(event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
        "VALUES ('board','task.submitted',?,?,?,'submitted','{}')",
        (_SUBMITTED_AT, graph["task_id"], graph["worker_id"]),
    )
    assert event.lastrowid is not None
    event_id = int(event.lastrowid)
    writer.connection.execute(
        "INSERT INTO board_task_operations "
        "(operation_id,operation_contract_version,task_id,command,canonical_request_json,"
        "request_sha256,caller_kid,actor_kind,actor_id,source_status,"
        "source_lifecycle_version,target_status,target_lifecycle_version,operation_state,"
        "effect_kind,effect_state,effect_operation_id,effect_result_reference,"
        "effect_intent_json,claimed_at_us,completed_at_us,event_id,result_json,error_json) "
        "VALUES (?,1,?,'submit_deliverable','{}',?,'gateway-task-board','agent',?,'accepted',"
        "2,'submitted',3,'completed','none','none',NULL,NULL,NULL,1,2,?,'{}',NULL)",
        (operation_id, graph["task_id"], "0" * 64, graph["worker_id"], event_id),
    )
    writer.connection.commit()
    fetched = reader.get_task(graph["task_id"])
    listed = _only_listed(reader)
    assert fetched is not None
    assert fetched["pending_transition"] is None
    assert listed["pending_transition"] is None


@pytest.mark.parametrize("method", ["get", "list"])
def test_missing_task_contract_fails_closed(
    task_reader: tuple[DbWriter, DbReader, dict[str, str]],
    method: str,
) -> None:
    writer, reader, graph = task_reader
    writer.connection.execute(
        "DELETE FROM board_task_contracts WHERE task_id=?",
        (graph["task_id"],),
    )
    writer.connection.commit()
    with pytest.raises(ServiceError) as raised:
        if method == "get":
            reader.get_task(graph["task_id"])
        else:
            reader.list_tasks(None, None, None, 10, 0)
    assert raised.value.status_code == 500
    assert raised.value.error == "task_contract_missing"
    assert raised.value.details == {"task_id": graph["task_id"]}
