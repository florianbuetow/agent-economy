"""Public-route builders for MP-08 Gateway delivery acceptance tests."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from service_auth.signing import load_public_key, public_key_to_b64

from db_gateway_service import migration, recovery
from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.money_audit import audit_money
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.services.bank_writer import BankWriter
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_operation_requests import parse_task_operation_request
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.fakes.gateway_mp08_legacy_history import (
    LegacyWorkerSettlementV4,
    prepare_legacy_worker_settlement_v4,
)
from tests.fakes.native_board_history import (
    create_task,
    file_claim,
    finalize,
    prepare_submitted,
    register_agent,
)

if TYPE_CHECKING:
    import httpx
    from fastapi.testclient import TestClient


@dataclass(frozen=True, slots=True)
class PendingEffect:
    task_id: str
    operation_id: str
    poster_id: str
    worker_id: str
    escrow_id: str
    source_reference: str


@dataclass(frozen=True, slots=True)
class NativeFinancialV4:
    source: LegacyWorkerSettlementV4
    operation_id: str
    operation_state: Literal["claimed", "completed", "blocked"]


@dataclass(slots=True)
class _OfflineConnectionOwner:
    """Minimal owner used by domain writers while constructing historical V4 data."""

    connection: sqlite3.Connection


def _quiesce_historical_database(connection: sqlite3.Connection) -> None:
    """Checkpoint and return an owned historical fixture to rollback-journal mode."""
    connection.commit()
    current_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if current_mode == "wal":
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert checkpoint is not None and tuple(checkpoint) == (0, 0, 0)
    normalized = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
    assert normalized is not None and str(normalized[0]).lower() == "delete"


def _assert_v4_financial_history(connection: sqlite3.Connection) -> None:
    """Prove the offline V4 fixture satisfies every migration preflight audit."""
    assert audit_money(connection).financially_clean
    assert audit_receipt_ownership(connection).ownership_clean
    assert audit_task_contracts(connection).contracts_clean
    assert audit_publications(connection).publications_clean
    assert [row[0] for row in connection.execute("PRAGMA integrity_check")] == ["ok"]
    assert list(connection.execute("PRAGMA foreign_key_check")) == []


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _insert_v4_native_claim(
    connection: sqlite3.Connection,
    *,
    source: LegacyWorkerSettlementV4,
    operation_id: str,
    claimed_at_us: int,
) -> None:
    """Persist the exact MP-07 V4 claim without invoking V5 runtime writers."""
    source_reference = f"task:{source.task_id}:approved"
    request = parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": source.task_id,
            "command": "approve_task",
            "expected_status": "submitted",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": source.poster_id,
            "arguments": {
                "poster_id": source.poster_id,
                "worker_id": source.worker_id,
                "escrow_id": source.escrow_id,
                "reward": source.reward,
                "source_reference": source_reference,
            },
        }
    )
    financial = canonical_request(
        "escrow_release",
        {
            "escrow_id": source.escrow_id,
            "recipient_account_id": source.worker_id,
            "source_reference": source_reference,
        },
    )
    intent = {
        "financial_request": json.loads(financial.canonical_json),
        "financial_request_sha256": financial.request_sha256,
        "expected_escrow": {
            "escrow_id": source.escrow_id,
            "task_id": source.task_id,
            "payer_account_id": source.poster_id,
            "amount": source.reward,
        },
    }
    connection.execute(
        "INSERT INTO board_task_operations "
        "(operation_id,operation_contract_version,task_id,command,canonical_request_json,"
        "request_sha256,caller_kid,actor_kind,actor_id,source_status,"
        "source_lifecycle_version,target_status,target_lifecycle_version,operation_state,"
        "effect_kind,effect_state,effect_operation_id,effect_result_reference,"
        "effect_intent_json,claimed_at_us,completed_at_us,event_id,result_json,error_json) "
        "VALUES (?,1,?,'approve_task',?,?,'gateway-task-board','agent',?,'submitted',0,"
        "'approved',1,'claimed','bank_release','pending',?,NULL,?,?,NULL,NULL,NULL,NULL)",
        (
            operation_id,
            source.task_id,
            request.canonical_json,
            request.request_sha256,
            source.poster_id,
            operation_id,
            _canonical_json(intent),
            claimed_at_us,
        ),
    )
    connection.commit()


def _complete_v4_native_claim(
    connection: sqlite3.Connection,
    *,
    source: LegacyWorkerSettlementV4,
    operation_id: str,
    completed_at_us: int,
) -> None:
    completed_at = "2099-01-01T00:00:05Z"
    cursor = connection.execute(
        "INSERT INTO events "
        "(event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
        "VALUES ('board','task.approved',?,?,?,?,?)",
        (
            completed_at,
            source.task_id,
            source.poster_id,
            "Task operation completed: approve_task",
            _canonical_json({"operation_id": operation_id, "command": "approve_task"}),
        ),
    )
    assert cursor.lastrowid is not None
    event_id = int(cursor.lastrowid)
    connection.execute(
        "UPDATE board_tasks SET status='approved',approved_at=?,escrow_pending=0 "
        "WHERE task_id=? AND status='submitted'",
        (completed_at, source.task_id),
    )
    assert connection.execute("SELECT changes()").fetchone()[0] == 1
    connection.execute(
        "UPDATE board_task_contracts SET lifecycle_version=1 "
        "WHERE task_id=? AND lifecycle_version=0",
        (source.task_id,),
    )
    assert connection.execute("SELECT changes()").fetchone()[0] == 1
    result = {
        "task_id": source.task_id,
        "command": "approve_task",
        "status": "approved",
        "lifecycle_version": 1,
        "event_id": event_id,
        "effect_result_reference": operation_id,
        "worker_id": source.worker_id,
        "reward": source.reward,
    }
    connection.execute(
        "UPDATE board_task_operations SET operation_state='completed',"
        "effect_state='succeeded',effect_result_reference=?,completed_at_us=?,event_id=?,"
        "result_json=? WHERE operation_id=? AND operation_state='claimed'",
        (operation_id, completed_at_us, event_id, _canonical_json(result), operation_id),
    )
    assert connection.execute("SELECT changes()").fetchone()[0] == 1
    connection.commit()


def _block_v4_native_claim(connection: sqlite3.Connection, *, operation_id: str) -> None:
    error = {
        "error": "effect_evidence_conflict",
        "message": "Persisted effect evidence contradicts the claimed task operation",
        "details": {"operation_id": operation_id},
    }
    connection.execute(
        "UPDATE board_task_operations SET operation_state='blocked',effect_state='conflict',"
        "error_json=? WHERE operation_id=? AND operation_state='claimed'",
        (_canonical_json(error), operation_id),
    )
    assert connection.execute("SELECT changes()").fetchone()[0] == 1
    connection.commit()


def prepare_native_pending(client: TestClient) -> PendingEffect:
    """Claim one real approval release and leave its Bank effect pending."""
    graph = create_task(client)
    worker_id, _bid_id = prepare_submitted(client, graph)
    operation_id = f"op-{uuid4()}"
    response = client.post(
        "/board/task-operations",
        json={
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
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "source_reference": f"task:{graph.task_id}:approved",
            },
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["operation_id"] == operation_id
    return PendingEffect(
        graph.task_id,
        operation_id,
        graph.poster_id,
        worker_id,
        graph.escrow_id,
        f"task:{graph.task_id}:approved",
    )


def prepare_native_refund_pending(client: TestClient) -> PendingEffect:
    """Claim one real open-task cancellation refund and leave it pending."""
    graph = create_task(client)
    task = client.get(f"/board/tasks/{graph.task_id}")
    assert task.status_code == 200, task.text
    cutoff_us = task.json()["bidding_cutoff_us"]
    assert type(cutoff_us) is int
    operation_id = f"op-{uuid4()}"
    response = client.post(
        "/board/task-operations",
        json={
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
                "cutoff_us": cutoff_us,
            },
        },
    )
    assert response.status_code == 202, response.text
    return PendingEffect(
        graph.task_id,
        operation_id,
        graph.poster_id,
        graph.poster_id,
        graph.escrow_id,
        f"task:{graph.task_id}:cancelled",
    )


def prepare_native_split_pending(client: TestClient) -> PendingEffect:
    """Claim one real disputed-task split ruling and leave settlement pending."""
    graph = create_task(client)
    worker_id, _bid_id = prepare_submitted(client, graph)
    reason = "MP-08 split projection dispute"
    dispute_operation_id = f"op-{uuid4()}"
    disputed = client.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": dispute_operation_id,
            "task_id": graph.task_id,
            "command": "dispute_task",
            "expected_status": "submitted",
            "expected_lifecycle_version": 2,
            "actor_kind": "agent",
            "actor_id": graph.poster_id,
            "arguments": {
                "poster_id": graph.poster_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "reason": reason,
            },
        },
    )
    assert disputed.status_code == 202, disputed.text
    claim_id = f"clm-{uuid4()}"
    claim = file_claim(
        client,
        graph,
        worker_id=worker_id,
        claim_id=claim_id,
        reason=reason,
    )
    assert claim.status_code == 201, claim.text
    completed_dispute = finalize(client, dispute_operation_id)
    assert completed_dispute.status_code == 200, completed_dispute.text

    config_path = Path(os.environ["CONFIG_PATH"])
    platform_key = load_public_key(config_path.parent / "platform.pub")
    platform_id = register_agent(
        client,
        name="MP-08 platform",
        public_key=f"ed25519:{public_key_to_b64(platform_key)}",
    )
    operation_id = f"op-{uuid4()}"
    ruling = client.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": graph.task_id,
            "command": "record_ruling",
            "expected_status": "disputed",
            "expected_lifecycle_version": 3,
            "actor_kind": "platform",
            "actor_id": platform_id,
            "arguments": {
                "poster_id": graph.poster_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "ruling_id": claim_id,
                "worker_pct": 50,
                "summary": "Split the escrow equally.",
                "source_reference": f"task:{graph.task_id}:ruling:{claim_id}",
            },
        },
    )
    assert ruling.status_code == 202, ruling.text
    return PendingEffect(
        graph.task_id,
        operation_id,
        graph.poster_id,
        worker_id,
        graph.escrow_id,
        f"task:{graph.task_id}:ruling:{claim_id}",
    )


def lease(client: TestClient, worker_id: str) -> httpx.Response:
    response = client.post(
        "/board/task-effect-deliveries/lease",
        json={"protocol_version": 1, "worker_id": worker_id},
    )
    assert response.status_code == 200, (
        "Missing protected MP-08 delivery lease capability: " + response.text
    )
    return response


def delivery(client: TestClient, operation_id: str) -> dict[str, object]:
    response = client.get(f"/board/task-effect-deliveries/{operation_id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["protocol_version"] == 1
    return payload["delivery"]


def prepare_legacy_v5(
    root: Path,
    *,
    kind: Literal["pending", "succeeded"],
) -> tuple[LegacyWorkerSettlementV4, DbWriter]:
    """Migrate one authentic legacy worker history to V5 and open its writer."""
    source = prepare_legacy_worker_settlement_v4(
        root,
        kind=kind,
        caller_kid="gateway-task-board",
    )
    recovery.create_backup(
        source.inventory,
        backup_root=source.backup_root,
        backup_id="before-v5-finalization",
        work_root=source.work_root,
    )
    migrate = getattr(migration, "migrate_v4_to_v5", None)
    assert callable(migrate), "Missing explicit backup-bound V4-to-V5 migration"
    migrate(
        source.inventory,
        backup_root=source.backup_root,
        backup_id="before-v5-finalization",
        work_root=source.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    return source, DbWriter(
        str(source.database),
        5000,
        "wal",
        None,
        current_task_board_caller_kid="gateway-task-board",  # type: ignore[call-arg]
    )


def prepare_native_financial_v4(
    root: Path,
    *,
    operation_state: Literal["claimed", "completed", "blocked"],
) -> NativeFinancialV4:
    """Create a real V4 native approval operation in the requested durable state."""
    source = prepare_legacy_worker_settlement_v4(
        root,
        kind="pending",
        caller_kid="gateway-task-board",
    )
    database = source.database
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    owner = _OfflineConnectionOwner(connection)
    bank_writer = BankWriter(owner)  # type: ignore[arg-type]
    claimed_at_us = parse_utc_text_to_epoch_us("2099-01-01T00:00:03Z")
    completed_at_us = parse_utc_text_to_epoch_us("2099-01-01T00:00:05Z")
    operation_id = f"op-{uuid4()}"
    try:
        connection.execute(
            "DELETE FROM board_legacy_effect_observations WHERE task_id=?",
            (source.task_id,),
        )
        connection.execute(
            "UPDATE board_tasks SET status='submitted',approved_at=NULL,escrow_pending=0 "
            "WHERE task_id=?",
            (source.task_id,),
        )
        connection.commit()
        _insert_v4_native_claim(
            connection,
            source=source,
            operation_id=operation_id,
            claimed_at_us=claimed_at_us,
        )
        if operation_state != "claimed":
            recipient = source.worker_id if operation_state == "completed" else source.poster_id
            bank_writer.escrow_release(
                {
                    "operation_id": operation_id,
                    "escrow_id": source.escrow_id,
                    "recipient_account_id": recipient,
                    "source_reference": f"task:{source.task_id}:approved",
                    "tx_id": f"tx-{uuid4()}",
                    "resolved_at": "2099-01-01T00:00:04Z",
                    "event": {
                        "event_source": "central-bank",
                        "event_type": "escrow.released",
                        "timestamp": "2099-01-01T00:00:04Z",
                        "task_id": source.task_id,
                        "agent_id": recipient,
                        "summary": "Settled native MP-08 fixture",
                        "payload": "{}",
                    },
                }
            )
            if operation_state == "completed":
                _complete_v4_native_claim(
                    connection,
                    source=source,
                    operation_id=operation_id,
                    completed_at_us=completed_at_us,
                )
            else:
                _block_v4_native_claim(connection, operation_id=operation_id)
                blocked_row = connection.execute(
                    "SELECT operation_state,effect_state FROM board_task_operations "
                    "WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                assert blocked_row is not None
                assert tuple(blocked_row) == ("blocked", "conflict")
        _assert_v4_financial_history(connection)
        _quiesce_historical_database(connection)
    finally:
        connection.close()
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()
    return NativeFinancialV4(source, operation_id, operation_state)
