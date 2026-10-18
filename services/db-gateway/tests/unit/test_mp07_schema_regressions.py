"""Focused MP-07 schema, audit, and migration regression proofs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import TYPE_CHECKING

import pytest
from service_auth.financial_identity import operation_id_for

from db_gateway_service import migration, recovery
from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_operation_requests import parse_task_operation_request
from tests.unit import test_mp07_legacy_effect_migration as legacy
from tests.unit import test_mp07_migration_contract as parent_contract

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _migrate_case(tmp_path: Path, name: str) -> tuple[Path, object]:
    inventory, backup_root, work_root, database = parent_contract._prepared_v2(tmp_path)
    connection = sqlite3.connect(database)
    try:
        legacy._apply_case(connection, name)
        connection.commit()
    finally:
        connection.close()
    legacy._assert_valid_v2_history(database)
    backup_id = f"regression-{name}"
    recovery.create_backup(
        inventory, backup_root=backup_root, backup_id=backup_id, work_root=work_root
    )
    result = migration.migrate_v2_to_v3(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert result.applied
    return database, inventory


def _migrate_clean(tmp_path: Path) -> Path:
    inventory, backup_root, work_root, database = parent_contract._prepared_v2(tmp_path)
    result = migration.migrate_v2_to_v3(
        inventory,
        backup_root=backup_root,
        backup_id="before-mp07",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert result.applied
    return database


def test_resolved_legacy_observation_keeps_immutable_fact_but_audits_current_projection(
    tmp_path: Path,
) -> None:
    database, _inventory = _migrate_case(tmp_path, "pending_approved_settlement")
    connection = sqlite3.connect(database)
    try:
        legacy._add_worker_settlement_receipt(connection)
        connection.execute("UPDATE board_tasks SET escrow_pending=0 WHERE task_id='t-1'")
        connection.execute(
            "UPDATE board_legacy_effect_observations SET unresolved=0 WHERE task_id='t-1'"
        )
        connection.commit()
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        result = audit_task_contracts(connection)
    finally:
        connection.close()

    assert result.contracts_clean, result.findings


@pytest.mark.parametrize("tamper", ["fact", "hash"])
def test_resolved_observation_still_rejects_immutable_fact_tampering(
    tmp_path: Path,
    tamper: str,
) -> None:
    database, _inventory = _migrate_case(tmp_path, "pending_approved_settlement")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE board_legacy_effect_observations SET unresolved=0 WHERE task_id='t-1'"
        )
        if tamper == "fact":
            raw = connection.execute(
                "SELECT observed_fact_json FROM board_legacy_effect_observations "
                "WHERE task_id='t-1'"
            ).fetchone()[0]
            fact = json.loads(str(raw))
            fact["reward"] = 51
            changed = json.dumps(fact, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "UPDATE board_legacy_effect_observations SET observed_fact_json=?, "
                "observed_fact_sha256=? WHERE task_id='t-1'",
                (changed, hashlib.sha256(changed.encode()).hexdigest()),
            )
        else:
            connection.execute(
                "UPDATE board_legacy_effect_observations SET observed_fact_sha256=? "
                "WHERE task_id='t-1'",
                ("0" * 64,),
            )
        connection.commit()
        result = audit_task_contracts(connection)
    finally:
        connection.close()
    assert not result.contracts_clean
    assert "legacy_observation_invalid" in {finding.code for finding in result.findings}


def _insert_completed_bid_operation(connection: sqlite3.Connection, target_version: int) -> None:
    operation_id = f"op-{uuid.uuid4()}"
    request = parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": "t-bank-only",
            "command": "submit_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": "a-worker",
            "arguments": {"bid_id": f"bid-{uuid.uuid4()}", "bidder_id": "a-worker", "amount": 40},
        }
    )
    event = connection.execute(
        "INSERT INTO events (event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
        "VALUES ('board','bid.submitted','now','t-bank-only','a-worker','bid','{}')"
    )
    event_id = int(event.lastrowid)
    result_json = json.dumps({"task_id": "t-bank-only", "status": "open"}, sort_keys=True)
    connection.execute(
        "INSERT INTO board_task_operations (operation_id,operation_contract_version,task_id,"
        "command,canonical_request_json,request_sha256,caller_kid,actor_kind,actor_id,"
        "source_status,source_lifecycle_version,target_status,target_lifecycle_version,"
        "operation_state,effect_kind,effect_state,effect_operation_id,effect_result_reference,"
        "effect_intent_json,claimed_at_us,completed_at_us,event_id,result_json,error_json) "
        "VALUES (?,1,'t-bank-only','submit_bid',?,?,'gateway-task-board','agent','a-worker',"
        "'open',0,'open',?,'completed','none','none',NULL,NULL,NULL,1,1,?,?,NULL)",
        (
            operation_id,
            request.canonical_json,
            request.request_sha256,
            target_version,
            event_id,
            result_json,
        ),
    )


def test_submit_bid_operation_with_unchanged_lifecycle_version_is_admitted(tmp_path: Path) -> None:
    database = _migrate_clean(tmp_path)
    connection = sqlite3.connect(database)
    try:
        _insert_completed_bid_operation(connection, 0)
        connection.commit()
    finally:
        connection.close()


def test_submit_bid_operation_cannot_increment_lifecycle_version(tmp_path: Path) -> None:
    database = _migrate_clean(tmp_path)
    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_completed_bid_operation(connection, 1)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation", ["shifted_bidding", "missing_acceptance", "missing_submission"]
)
def test_task_contract_audit_rejects_phase_and_cutoff_inconsistency(
    tmp_path: Path,
    mutation: str,
) -> None:
    database = _migrate_clean(tmp_path)
    connection = sqlite3.connect(database)
    try:
        if mutation == "shifted_bidding":
            connection.execute(
                "UPDATE board_tasks SET bidding_deadline='2026-10-17T12:00:02.125000Z' "
                "WHERE task_id='t-1'"
            )
            connection.execute(
                "UPDATE board_task_contracts SET bidding_cutoff_us=bidding_cutoff_us+1000000, "
                "acceptance_cutoff_us=acceptance_cutoff_us+1000000 WHERE task_id='t-1'"
            )
        elif mutation == "missing_acceptance":
            connection.execute(
                "UPDATE board_tasks SET accepted_at=NULL, execution_deadline=NULL "
                "WHERE task_id='t-1'"
            )
            connection.execute(
                "UPDATE board_task_contracts SET execution_cutoff_us=NULL WHERE task_id='t-1'"
            )
        else:
            connection.execute(
                "UPDATE board_tasks SET status='submitted', submitted_at=NULL, "
                "review_deadline=NULL "
                "WHERE task_id='t-1'"
            )
            connection.execute(
                "UPDATE board_task_contracts SET review_cutoff_us=NULL WHERE task_id='t-1'"
            )
        connection.commit()
        result = audit_task_contracts(connection)
    finally:
        connection.close()
    assert not result.contracts_clean
    assert "task_cutoff_inconsistent" in {finding.code for finding in result.findings}


@pytest.mark.parametrize("status", ["approved", "expired", "cancelled"])
def test_migration_rejects_terminal_locked_escrow_without_pending_or_receipt(
    tmp_path: Path,
    status: str,
) -> None:
    inventory, backup_root, work_root, database = parent_contract._prepared_v2(tmp_path)
    connection = sqlite3.connect(database)
    try:
        legacy._clear_court_history(connection)
        connection.execute(
            "UPDATE board_tasks SET status=?, escrow_pending=0, approved_at=NULL, expired_at=NULL, "
            "cancelled_at=NULL WHERE task_id='t-1'",
            (status,),
        )
        connection.commit()
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
    finally:
        connection.close()
    backup_id = f"terminal-{status}"
    before = parent_contract._snapshot(database)
    recovery.create_backup(
        inventory, backup_root=backup_root, backup_id=backup_id, work_root=work_root
    )
    with pytest.raises(migration.MigrationError) as raised:
        migration.migrate_v2_to_v3(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            busy_timeout_ms=5000,
        )
    assert raised.value.reason == "legacy_effect_history_unsupported", (
        raised.value.reason,
        raised.value.message,
        raised.value.safe_details,
    )
    assert raised.value.safe_details.get("task_id") == "t-1"
    assert parent_contract._snapshot(database) == before


def test_migration_rejects_approved_receipt_paid_to_poster(tmp_path: Path) -> None:
    inventory, backup_root, work_root, database = parent_contract._prepared_v2(tmp_path)
    connection = sqlite3.connect(database)
    try:
        legacy._clear_court_history(connection)
        connection.execute(
            "UPDATE board_tasks SET status='approved', escrow_pending=0 WHERE task_id='t-1'"
        )
        request = canonical_request(
            "escrow_release",
            {
                "escrow_id": "esc-1",
                "recipient_account_id": "a-external",
                "source_reference": "task:t-1:approved",
            },
        )
        event = connection.execute(
            "INSERT INTO events "
            "(event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
            "VALUES ('bank','escrow.released','now','t-1','a-external','wrong recipient','{}')"
        )
        event_id = int(event.lastrowid)
        connection.execute("UPDATE bank_accounts SET balance=100 WHERE account_id='a-external'")
        connection.execute(
            "UPDATE bank_escrow SET status='released', resolved_at='now' WHERE escrow_id='esc-1'"
        )
        connection.execute(
            "INSERT INTO bank_transactions VALUES "
            "('tx-wrong-recipient','a-external','escrow_release',50,100,'esc-1','now',?)",
            (event_id,),
        )
        op_id = operation_id_for("task-settlement", ("t-1", "approved"))
        connection.execute(
            "INSERT INTO bank_operation_receipts VALUES "
            "(?,1,'escrow_release',?,?,?,'now',?,'esc-1',?)",
            (
                op_id,
                request.canonical_json,
                request.request_sha256,
                request.source_reference,
                event_id,
                '{"escrow_status_at_commit":"released"}',
            ),
        )
        connection.execute(
            "INSERT INTO bank_operation_legs VALUES "
            "(?,0,'recipient','a-external','credit',50,'tx-wrong-recipient',100)",
            (op_id,),
        )
        connection.commit()
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
    finally:
        connection.close()
    backup_id = "wrong-approved-recipient"
    before = parent_contract._snapshot(database)
    recovery.create_backup(
        inventory, backup_root=backup_root, backup_id=backup_id, work_root=work_root
    )
    with pytest.raises(migration.MigrationError) as raised:
        migration.migrate_v2_to_v3(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
            busy_timeout_ms=5000,
        )
    assert raised.value.reason == "legacy_effect_history_unsupported", (
        raised.value.reason,
        raised.value.message,
        raised.value.safe_details,
    )
    assert parent_contract._snapshot(database) == before
