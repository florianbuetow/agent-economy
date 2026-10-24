"""Review regressions for protocol-v1 submission governance detection."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import (
    TaskOperationRequest,
    parse_task_operation_request,
)
from tests.fakes.mp09_v6_artifact_fixture import (
    BOARD_KID,
    NOW_US,
    TASK_ID,
    MutableClock,
    open_v6_database,
    seed_accepted_task,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_TASK_ID = TASK_ID
_WORKER_ID = "worker-1"
_SUBMIT_US = NOW_US + 30_000_000


def _operation(number: int) -> str:
    return f"op-50000000-0000-4000-8000-{number:012d}"


def _request(number: int) -> TaskOperationRequest:
    return parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": _operation(number),
            "task_id": _TASK_ID,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": _WORKER_ID,
            "arguments": {"worker_id": _WORKER_ID},
        }
    )


def _writer(connection: sqlite3.Connection) -> TaskOperationWriter:
    return TaskOperationWriter(
        connection,
        clock_us=MutableClock(now_us=_SUBMIT_US),
        deadline_actor_id="system-task-board-deadline",
        platform_public_key="ed25519:test-platform-authority",
        current_task_board_caller_kid=BOARD_KID,
    )


def _record_asset(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO board_assets (asset_id,task_id,uploader_id,filename,content_type,"
        "size_bytes,storage_path,content_hash,uploaded_at) VALUES "
        "('asset-governance-review',?,?, 'legacy.txt','text/plain',7,"
        "'live/legacy/governance.blob',?,'2099-01-02T00:00:01Z')",
        (_TASK_ID, _WORKER_ID, "a" * 64),
    )
    connection.commit()


def _delete_task_governance(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM board_task_artifact_contracts WHERE task_id=?", (_TASK_ID,))
    connection.execute("DELETE FROM board_artifact_manifests WHERE task_id=?", (_TASK_ID,))
    connection.commit()


def _full_snapshot(connection: sqlite3.Connection) -> tuple[object, ...]:
    schema = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    )
    tables = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    )
    data: list[tuple[str, tuple[tuple[object, ...], ...]]] = []
    for table in tables:
        rows = [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]  # nosec B608
        data.append((table, tuple(sorted(rows, key=repr))))
    return schema, tuple(data)


def _assert_v1_rejected_without_mutation(
    connection: sqlite3.Connection,
    *,
    operation_number: int,
) -> None:
    request = _request(operation_number)
    before = _full_snapshot(connection)
    with pytest.raises(ServiceError) as raised:
        _writer(connection).claim_operation(request, BOARD_KID)
    assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")
    assert _full_snapshot(connection) == before


@pytest.mark.parametrize("cutover_state", ["planned", "copying", "verified", "blocked"])
def test_any_interrupted_cutover_state_blocks_new_protocol_v1_submission(
    tmp_path: Path,
    cutover_state: str,
) -> None:
    connection = open_v6_database(tmp_path / f"cutover-{cutover_state}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id="manifest-review")
        _delete_task_governance(connection)
        connection.execute(
            "INSERT INTO board_artifact_cutover (singleton,cutover_state,source_schema_version,"
            "migration_plan_sha256,source_row_set_sha256,created_at_us,completed_at_us) "
            "VALUES (1,?,5,?,?,?,NULL)",
            (cutover_state, "a" * 64, "b" * 64, NOW_US),
        )
        connection.commit()
        _record_asset(connection)
        _assert_v1_rejected_without_mutation(
            connection,
            operation_number={"planned": 1, "copying": 2, "verified": 3, "blocked": 4}[
                cutover_state
            ],
        )
    finally:
        connection.close()


@pytest.mark.parametrize("remaining_table", ["contracts", "cutover"])
def test_partial_governance_table_set_blocks_new_protocol_v1_submission(
    tmp_path: Path,
    remaining_table: str,
) -> None:
    connection = open_v6_database(tmp_path / f"partial-{remaining_table}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id="manifest-review")
        _delete_task_governance(connection)
        dropped = (
            "board_artifact_cutover"
            if remaining_table == "contracts"
            else "board_task_artifact_contracts"
        )
        connection.execute(f"DROP TABLE {dropped}")  # nosec B608
        connection.commit()
        governance_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('board_task_artifact_contracts','board_artifact_cutover')"
            ).fetchall()
        }
        assert len(governance_tables) == 1
        _record_asset(connection)
        _assert_v1_rejected_without_mutation(
            connection,
            operation_number=5 if remaining_table == "contracts" else 6,
        )
    finally:
        connection.close()


def test_both_governance_tables_without_task_contract_or_cutover_allow_pre_cutover_v1(
    tmp_path: Path,
) -> None:
    connection = open_v6_database(tmp_path / "both-empty.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id="manifest-review")
        _delete_task_governance(connection)
        assert (
            connection.execute("SELECT COUNT(*) FROM board_task_artifact_contracts").fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM board_artifact_cutover").fetchone()[0] == 0
        _record_asset(connection)
        result = _writer(connection).claim_operation(_request(7), BOARD_KID)
        assert result["operation_state"] == "completed"
        assert result["target_status"] == "submitted"
    finally:
        connection.close()


def _seed_true_v5(connection: sqlite3.Connection) -> None:
    connection.executemany(
        "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) VALUES (?,?,?,?)",
        (
            ("poster-1", "Poster", "ed25519:poster-v5", "2099-01-01T23:58:00Z"),
            (_WORKER_ID, "Worker", "ed25519:worker-v5", "2099-01-01T23:58:00Z"),
        ),
    )
    connection.executemany(
        "INSERT INTO bank_accounts (account_id,balance,created_at) VALUES (?,?,?)",
        (
            ("poster-1", 0, "2099-01-01T23:58:01Z"),
            (_WORKER_ID, 0, "2099-01-01T23:58:01Z"),
        ),
    )
    connection.execute(
        "INSERT INTO bank_escrow (escrow_id,payer_account_id,amount,task_id,status,created_at) "
        "VALUES ('escrow-v5','poster-1',100,?,'locked','2099-01-01T23:59:00Z')",
        (_TASK_ID,),
    )
    connection.execute(
        "INSERT INTO board_tasks (task_id,poster_id,title,spec,reward,status,"
        "bidding_deadline_seconds,deadline_seconds,review_deadline_seconds,bidding_deadline,"
        "execution_deadline,escrow_id,worker_id,accepted_at,created_at) VALUES "
        "(?,'poster-1','V5 task','Pre-cutover.',100,'accepted',60,3600,60,"
        "'2099-01-02T00:01:00Z','2099-01-02T01:00:00Z','escrow-v5',?,"
        "'2099-01-02T00:00:00Z','2099-01-01T23:59:00Z')",
        (_TASK_ID, _WORKER_ID),
    )
    connection.execute(
        "INSERT INTO board_task_contracts (task_id,lifecycle_version,timing_contract_version,"
        "acceptance_window_seconds,bidding_cutoff_us,acceptance_cutoff_us,execution_cutoff_us,"
        "review_cutoff_us,accepted_reward,accepted_bid_amount) VALUES "
        "(?,1,2,60,?,?,?,NULL,100,80)",
        (_TASK_ID, NOW_US + 60_000_000, NOW_US + 120_000_000, NOW_US + 3_600_000_000),
    )
    _record_asset(connection)


def test_true_v5_without_governance_tables_allows_pre_cutover_protocol_v1(
    tmp_path: Path,
) -> None:
    connection = sqlite3.connect(tmp_path / "true-v5.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V5))
        _seed_true_v5(connection)
        governance_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('board_task_artifact_contracts','board_artifact_cutover')"
        ).fetchall()
        assert governance_tables == []
        result = _writer(connection).claim_operation(_request(8), BOARD_KID)
        assert result["operation_state"] == "completed"
        assert result["target_status"] == "submitted"
    finally:
        connection.close()


def test_stored_v1_replay_precedes_interrupted_cutover_and_current_state_checks(
    tmp_path: Path,
) -> None:
    connection = sqlite3.connect(tmp_path / "v1-replay-interrupted-cutover.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V5))
        _seed_true_v5(connection)
        request = _request(9)
        original = _writer(connection).claim_operation(request, BOARD_KID)

        # This marker proves stored replay precedes governance checks. It is not migration proof.
        connection.execute(
            "CREATE TABLE board_artifact_cutover ("
            "singleton INTEGER PRIMARY KEY NOT NULL,cutover_state TEXT NOT NULL,"
            "source_schema_version INTEGER NOT NULL,migration_plan_sha256 TEXT NOT NULL,"
            "source_row_set_sha256 TEXT NOT NULL,created_at_us INTEGER NOT NULL,"
            "completed_at_us INTEGER) WITHOUT ROWID"
        )
        connection.execute(
            "INSERT INTO board_artifact_cutover (singleton,cutover_state,source_schema_version,"
            "migration_plan_sha256,source_row_set_sha256,created_at_us,completed_at_us) "
            "VALUES (1,'copying',5,?,?,?,NULL)",
            ("c" * 64, "d" * 64, NOW_US),
        )
        connection.execute("UPDATE board_tasks SET status='approved' WHERE task_id=?", (_TASK_ID,))
        connection.execute(
            "UPDATE board_task_contracts SET lifecycle_version=3 WHERE task_id=?", (_TASK_ID,)
        )
        connection.commit()

        replay = _writer(connection).claim_operation(request, BOARD_KID)
        assert replay == original
        assert replay["protocol_version"] == 1
        operation_count = connection.execute(
            "SELECT COUNT(*) FROM board_task_operations WHERE operation_id=?",
            (request.operation_id,),
        ).fetchone()
        assert operation_count is not None
        assert tuple(operation_count) == (1,)
    finally:
        connection.close()
