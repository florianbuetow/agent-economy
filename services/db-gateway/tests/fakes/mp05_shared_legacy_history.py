"""Real v0→v3 history with two payers locking one unclaimed task identity."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from db_gateway_service import migration, recovery
from tests.fakes.mp05_publication_migration_history import (
    V3Source,
    _with_task_board_authority,
)
from tests.unit import test_mp06_migration as v0_migration_tests

if TYPE_CHECKING:
    from pathlib import Path

SHARED_LEGACY_TASK_ID = "t-bank-only"
SHARED_LEGACY_ESCROWS = ("esc-bank-only", "esc-bank-worker")


def prepare_v3_source_with_shared_legacy_locks(tmp_path: Path) -> V3Source:
    """Produce supported legacy markers without inserting any marker directly."""
    inventory, backup_root, work_root, database = v0_migration_tests._prepared_v0(tmp_path)
    connection = sqlite3.connect(database)
    try:
        _repair_task_history(connection)
        _add_second_payer_lock(connection)
        connection.commit()
    finally:
        connection.close()

    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="shared-before-mp06",
        work_root=work_root,
    )
    v1 = migration.migrate_v0_to_v1(
        inventory,
        backup_root=backup_root,
        backup_id="shared-before-mp06",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v1.observation == "committed_v1_verified"
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="shared-before-mp04",
        work_root=work_root,
    )
    v2 = migration.migrate_v1_to_v2(
        inventory,
        backup_root=backup_root,
        backup_id="shared-before-mp04",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v2.observation == "committed_v2_verified"
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="shared-before-mp07",
        work_root=work_root,
    )
    v3 = migration.migrate_v2_to_v3(
        inventory,
        backup_root=backup_root,
        backup_id="shared-before-mp07",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v3.observation == "committed_v3_verified"
    caller_kid = "gateway-task-board"
    return V3Source(
        _with_task_board_authority(inventory, handle=caller_kid),
        backup_root,
        work_root,
        database,
        caller_kid,
    )


def _add_second_payer_lock(connection: sqlite3.Connection) -> None:
    issued_at = "2026-10-17T11:59:00.000000Z"
    locked_at = "2026-10-17T11:59:01.000000Z"
    issuance_event = connection.execute(
        "INSERT INTO events "
        "(event_source,event_type,timestamp,agent_id,summary,payload) "
        "VALUES ('bank','account.created',?,'a-worker','legacy worker funding','{}')",
        (issued_at,),
    )
    lock_event = connection.execute(
        "INSERT INTO events "
        "(event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
        "VALUES ('bank','escrow.locked',?,?,'a-worker','second legacy lock','{}')",
        (locked_at, SHARED_LEGACY_TASK_ID),
    )
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id,account_id,type,amount,balance_after,reference,timestamp,event_id) "
        "VALUES ('tx-worker-credit','a-worker','credit',50,50,'worker_initial',?,?)",
        (issued_at, int(issuance_event.lastrowid)),
    )
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id,account_id,type,amount,balance_after,reference,timestamp,event_id) "
        "VALUES ('tx-lock-bank-worker','a-worker','escrow_lock',50,0,?,?,?)",
        (SHARED_LEGACY_TASK_ID, locked_at, int(lock_event.lastrowid)),
    )
    connection.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id,payer_account_id,amount,task_id,status,created_at,event_id) "
        "VALUES ('esc-bank-worker','a-worker',50,?,'locked',?,?)",
        (SHARED_LEGACY_TASK_ID, locked_at, int(lock_event.lastrowid)),
    )


def _repair_task_history(connection: sqlite3.Connection) -> None:
    connection.execute(
        "UPDATE board_tasks SET created_at='2026-10-17T12:00:00.125000Z', "
        "bidding_deadline='2026-10-17T12:00:01.125000Z', "
        "accepted_bid_id='bid-1', accepted_at='2026-10-17T12:00:00.500000Z', "
        "execution_deadline='2026-10-17T12:00:01.500000Z', "
        "submitted_at='2026-10-17T12:00:01Z', review_deadline='2026-10-17T12:00:02Z', "
        "disputed_at='2026-10-17T12:00:01.500000Z', bid_count=1, "
        "rebuttal_deadline='2026-10-17T12:00:11.500000Z', "
        "rebuttal_submitted_at='2026-10-17T12:00:03Z' WHERE task_id='t-1'"
    )
    connection.execute(
        "UPDATE board_bids SET amount=40, submitted_at='2026-10-17T12:00:00.250000Z'"
    )
    connection.execute(
        "UPDATE court_claims SET status='rebuttal_submitted', "
        "filed_at='2026-10-17T12:00:01.500000Z', "
        "rebuttal_deadline='2026-10-17T12:00:11.500000Z'"
    )
    connection.execute("UPDATE court_rebuttals SET submitted_at='2026-10-17T12:00:03Z'")
    connection.execute(
        "UPDATE board_assets SET uploader_id='a-worker', uploaded_at='2026-10-17T12:00:00.750000Z'"
    )


__all__ = [
    "SHARED_LEGACY_ESCROWS",
    "SHARED_LEGACY_TASK_ID",
    "prepare_v3_source_with_shared_legacy_locks",
]
