"""Review regressions for exact receipts with contradictory business projection."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service import migration, recovery, task_time
from db_gateway_service.effect_recovery_audit import audit_effect_recovery
from db_gateway_service.services.bank_writer import BankWriter
from db_gateway_service.services.db_writer import DbWriter
from tests.fakes.gateway_mp08_effects import (
    prepare_native_financial_v4,
    prepare_native_pending,
)
from tests.fakes.native_board_history import (
    FIXED_NOW_US,
    BusinessClock,
    TaskGraph,
    settle_release,
)

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit
pytest_plugins = ["tests.fakes.gateway_mp08_app"]


@dataclass(frozen=True, slots=True)
class _Owner:
    connection: sqlite3.Connection


def _event(task_id: str, worker_id: str) -> dict[str, object]:
    return {
        "event_source": "central-bank",
        "event_type": "escrow.released",
        "timestamp": "2099-01-01T00:00:04Z",
        "task_id": task_id,
        "agent_id": worker_id,
        "summary": "Settled exact blocked review fixture",
        "payload": "{}",
    }


def test_runtime_blocked_exact_receipt_survives_gateway_reopen(
    gateway_mp08_app: TestClient,
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))
    effect = prepare_native_pending(gateway_mp08_app)
    receipt = settle_release(
        gateway_mp08_app,
        TaskGraph(effect.task_id, effect.poster_id, effect.escrow_id),
        operation_id=effect.operation_id,
        recipient_id=effect.worker_id,
        source_reference=effect.source_reference,
    )
    assert receipt.status_code in {200, 201}, receipt.text
    db_writer.connection.execute(
        "UPDATE board_tasks SET status='approved',approved_at=? WHERE task_id=?",
        ("2099-01-01T00:00:04Z", effect.task_id),
    )
    db_writer.connection.commit()
    blocked = gateway_mp08_app.post(
        f"/board/task-effect-deliveries/{effect.operation_id}/finalize",
        json={},
    )
    assert blocked.status_code == 409, blocked.text
    assert audit_effect_recovery(db_writer.connection).recovery_clean
    database = db_writer._db_path
    db_writer.close()
    reopened = DbWriter(
        database,
        5000,
        "wal",
        None,
        current_task_board_caller_kid="gateway-task-board",
    )
    reopened.close()


def test_v4_blocked_exact_receipt_with_contradictory_projection_migrates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = prepare_native_financial_v4(tmp_path, operation_state="claimed")
    source = native.source
    connection = sqlite3.connect(source.database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        BankWriter(_Owner(connection)).escrow_release(  # type: ignore[arg-type]
            {
                "operation_id": native.operation_id,
                "escrow_id": source.escrow_id,
                "recipient_account_id": source.worker_id,
                "source_reference": f"task:{source.task_id}:approved",
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2099-01-01T00:00:04Z",
                "event": _event(source.task_id, source.worker_id),
            }
        )
        error = json.dumps(
            {
                "details": {"operation_id": native.operation_id},
                "error": "effect_evidence_conflict",
                "message": "Persisted effect evidence contradicts the claimed task operation",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            "UPDATE board_tasks SET status='approved',approved_at=? WHERE task_id=?",
            ("2099-01-01T00:00:04Z", source.task_id),
        )
        connection.execute(
            "UPDATE board_task_operations SET operation_state='blocked',"
            "effect_state='conflict',error_json=? WHERE operation_id=?",
            (error, native.operation_id),
        )
        connection.commit()
    finally:
        connection.close()
    recovery.create_backup(
        source.inventory,
        backup_root=source.backup_root,
        backup_id="before-v5-blocked-confirmed-review",
        work_root=source.work_root,
    )
    monkeypatch.setattr(task_time, "utc_now_us", lambda: FIXED_NOW_US)
    migration.migrate_v4_to_v5(
        source.inventory,
        backup_root=source.backup_root,
        backup_id="before-v5-blocked-confirmed-review",
        work_root=source.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    migrated = sqlite3.connect(source.database)
    try:
        row = migrated.execute(
            "SELECT delivery_state,last_failure_code FROM board_task_effect_deliveries "
            "WHERE effect_operation_id=?",
            (native.operation_id,),
        ).fetchone()
        assert row == ("integrity_blocked", "effect_evidence_conflict")
    finally:
        migrated.close()
