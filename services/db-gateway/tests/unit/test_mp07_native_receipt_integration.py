"""Regression coverage for Bank-produced MP-07 settlement evidence."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_contract_audit import audit_task_contracts
from tests.conftest import make_event
from tests.unit import test_mp07_legacy_effect_migration as legacy
from tests.unit import test_mp07_operation_regressions as setup
from tests.unit import test_mp07_ruling_startup as ruling_startup
from tests.unit import test_mp07_schema_regressions as schema_regressions
from tests.unit import test_mp07_writer_transaction_cleanup as transaction_cleanup

pytestmark = pytest.mark.unit

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def v3_writer(tmp_path: Path) -> Iterator[DbWriter]:
    """Boot the production writers against a fresh exact-v3 database."""
    writer = DbWriter(
        db_path=str(tmp_path / "native-receipt-integration.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V4),
    )
    yield writer
    writer.close()


def test_central_bank_release_receipt_finalizes_approval_without_rewriting_hash(
    v3_writer: DbWriter,
) -> None:
    """Accept the exact provenance emitted by the production Central Bank client."""
    graph = setup._seed_graph(v3_writer, submitted=True)
    operation_id = f"op-{uuid4()}"
    pending = setup._claim_approval(v3_writer, graph, operation_id)
    expected_hash = pending["effect_intent"]["financial_request_sha256"]

    native_receipt = v3_writer.escrow_release(
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
                agent_id=graph.worker_id,
            ),
        }
    )

    assert native_receipt["receipt"]["request_sha256"] == expected_hash
    assert audit_money(v3_writer.connection).financially_clean
    assert audit_receipt_ownership(v3_writer.connection).ownership_clean
    event_source = v3_writer.connection.execute(
        "SELECT e.event_source FROM events AS e "
        "JOIN bank_operation_receipts AS r ON r.event_id=e.event_id "
        "WHERE r.operation_id=?",
        (operation_id,),
    ).fetchone()
    assert event_source is not None and event_source[0] == "central-bank"

    completed = setup._native(v3_writer).finalize_operation(
        operation_id,
        "gateway-task-board",
    )

    assert completed["operation_state"] == "completed"
    assert completed["effect_state"] == "succeeded"
    persisted_hash = v3_writer.connection.execute(
        "SELECT request_sha256 FROM bank_operation_receipts WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    assert persisted_hash is not None and persisted_hash[0] == expected_hash


def test_central_bank_release_without_event_task_finalizes_cancellation(
    v3_writer: DbWriter,
) -> None:
    """Cover the second live settlement command with the producer's nullable event task."""
    task_id, pending = transaction_cleanup._pending_cancel(v3_writer)
    financial = pending["effect_intent"]["financial_request"]
    assert isinstance(financial, dict)
    v3_writer.escrow_release(
        {
            "operation_id": pending["operation_id"],
            "escrow_id": financial["escrow_id"],
            "recipient_account_id": financial["recipient_account_id"],
            "source_reference": financial["source_reference"],
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": "2099-01-01T00:00:03Z",
            "event": make_event(
                source="central-bank",
                event_type="escrow.released",
                agent_id=financial["recipient_account_id"],
            ),
        }
    )

    completed = transaction_cleanup._native(v3_writer).finalize_operation(
        pending["operation_id"],
        "gateway-task-board",
    )

    assert completed["operation_state"] == "completed"
    assert completed["result"]["status"] == "cancelled"
    task_status = v3_writer.connection.execute(
        "SELECT status FROM board_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert task_status is not None and task_status[0] == "cancelled"


@pytest.mark.parametrize("corruption", ["source", "task"])
def test_native_ruling_audit_rejects_foreign_event_provenance(
    tmp_path: Path,
    corruption: str,
) -> None:
    """Keep restart admission strict about source and non-null task provenance."""
    database_path = tmp_path / "wrong-ruling-event-source.db"
    authority = ruling_startup._platform_authority(tmp_path)
    with closing(ruling_startup._writer(database_path, bootstrap=True)) as writer:
        completed = ruling_startup._complete_board_first_ruling(writer, authority, 50)
        assignment = "event_source='bank'" if corruption == "source" else "task_id='t-foreign'"
        writer.connection.execute(
            f"UPDATE events SET {assignment} WHERE event_id=("
            "SELECT r.event_id FROM bank_operation_receipts AS r "
            "JOIN board_task_operations AS o ON o.operation_id=r.operation_id "
            "WHERE o.task_id=? AND o.command='record_ruling')",
            (completed.graph.task_id,),
        )
        writer.connection.commit()

        audit = audit_task_contracts(writer.connection)

    assert tuple((finding.code, finding.task_id) for finding in audit.findings) == (
        ("dispute_claim_linkage_invalid", completed.graph.task_id),
    )


@pytest.mark.parametrize("corruption", ["source", "task"])
def test_legacy_settlement_audit_rejects_foreign_event_provenance(
    tmp_path: Path,
    corruption: str,
) -> None:
    """Reject a native settlement with foreign source or non-null task provenance."""
    database, _inventory = schema_regressions._migrate_case(
        tmp_path,
        "pending_approved_settlement",
    )
    connection = sqlite3.connect(database)
    try:
        evidence = legacy._add_worker_settlement_receipt(connection)
        assignment = "event_source='bank'" if corruption == "source" else "task_id='t-foreign'"
        connection.execute(
            f"UPDATE events SET {assignment} WHERE event_id=?",
            (evidence.event_id,),
        )
        connection.execute("UPDATE board_tasks SET escrow_pending=0 WHERE task_id='t-1'")
        connection.execute(
            "UPDATE board_legacy_effect_observations SET unresolved=0 WHERE task_id='t-1'"
        )
        connection.commit()
        assert audit_receipt_ownership(connection).ownership_clean

        audit = audit_task_contracts(connection)
    finally:
        connection.close()

    assert tuple((finding.code, finding.task_id) for finding in audit.findings) == (
        ("legacy_observation_invalid", "t-1"),
    )
