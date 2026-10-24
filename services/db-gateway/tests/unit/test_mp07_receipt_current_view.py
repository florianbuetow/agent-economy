"""A later account credit cannot invalidate an immutable settlement receipt."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from tests.conftest import make_event
from tests.unit import test_mp07_operation_regressions as setup

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def test_finalization_accepts_receipt_after_later_authorized_balance_change(tmp_path: Path) -> None:
    writer = DbWriter(
        str(tmp_path / "receipt-current-view.db"),
        5000,
        "wal",
        load_schema_sql(SchemaState.CURRENT_V5),
        current_task_board_caller_kid="gateway-task-board",
    )
    try:
        graph = setup._seed_graph(writer, submitted=True)
        operation_id = f"op-{uuid4()}"
        setup._claim_approval(writer, graph, operation_id)
        setup._release(writer, graph, operation_id, recipient_id=graph.worker_id)
        writer.credit_account(
            {
                "operation_id": f"op-{uuid4()}",
                "tx_id": f"tx-{uuid4()}",
                "account_id": graph.worker_id,
                "amount": 7,
                "reference": f"authorized-follow-up-{uuid4()}",
                "timestamp": "2099-01-01T00:00:04Z",
                "event": make_event(
                    source="bank", event_type="salary.paid", agent_id=graph.worker_id
                ),
            }
        )
        assert audit_money(writer.connection).financially_clean
        assert audit_receipt_ownership(writer.connection).ownership_clean
        before = setup._state(writer, graph.task_id)
        result = setup._outcome(
            lambda: setup._native(writer).finalize_operation(operation_id, "gateway-task-board")
        )
        assert result["kind"] == "success", result
        completed = result["result"]
        assert isinstance(completed, dict)
        assert completed["operation_state"] == "completed"
        after = setup._state(writer, graph.task_id)
        assert after.ledger == before.ledger
        assert (
            writer.connection.execute(
                "SELECT balance FROM bank_accounts WHERE account_id=?", (graph.worker_id,)
            ).fetchone()[0]
            == 107
        )
        assert (
            writer.connection.execute(
                "SELECT COUNT(*) FROM events WHERE task_id=? AND event_type='task.approved'",
                (graph.task_id,),
            ).fetchone()[0]
            == 1
        )
        replay = setup._native(writer).finalize_operation(operation_id, "gateway-task-board")
        assert replay == result["result"]
        assert setup._state(writer, graph.task_id) == after
    finally:
        writer.close()


@pytest.mark.parametrize("target", ["receipt", "transaction", "escrow"])
def test_finalizer_rejects_disagreement_in_settlement_timestamps(
    tmp_path: Path, target: str
) -> None:
    writer = DbWriter(
        str(tmp_path / "receipt-timestamps.db"),
        5000,
        "wal",
        load_schema_sql(SchemaState.CURRENT_V5),
        current_task_board_caller_kid="gateway-task-board",
    )
    try:
        graph = setup._seed_graph(writer, submitted=True)
        operation_id = f"op-{uuid4()}"
        setup._claim_approval(writer, graph, operation_id)
        setup._release(writer, graph, operation_id, recipient_id=graph.worker_id)
        assert audit_receipt_ownership(writer.connection).ownership_clean
        statements = {
            "receipt": (
                "UPDATE bank_operation_receipts SET committed_at=? WHERE operation_id=?",
                operation_id,
            ),
            "transaction": (
                "UPDATE bank_transactions SET timestamp=? WHERE tx_id=(SELECT transaction_id "
                "FROM bank_operation_legs WHERE operation_id=?)",
                operation_id,
            ),
            "escrow": ("UPDATE bank_escrow SET resolved_at=? WHERE escrow_id=?", graph.escrow_id),
        }
        statement, identity = statements[target]
        writer.connection.execute(statement, ("2099-01-01T00:00:04Z", identity))
        writer.connection.commit()
        before = setup._state(writer, graph.task_id)
        timestamps_sql = (
            "SELECT r.committed_at, t.timestamp, e.resolved_at "
            "FROM bank_operation_receipts r JOIN bank_operation_legs l "
            "ON l.operation_id=r.operation_id JOIN bank_transactions t "
            "ON t.tx_id=l.transaction_id JOIN bank_escrow e ON e.escrow_id=r.escrow_id "
            "WHERE r.operation_id=?"
        )
        timestamps_before = tuple(
            writer.connection.execute(timestamps_sql, (operation_id,)).fetchone()
        )
        outcome = setup._outcome(
            lambda: setup._native(writer).finalize_operation(operation_id, "gateway-task-board")
        )
        assert outcome["kind"] == "service_error", outcome
        assert outcome["status"] == 409 and outcome["error"] == "effect_evidence_conflict", outcome
        after = setup._state(writer, graph.task_id)
        assert (after.task, after.events, after.counters, after.ledger) == (
            before.task,
            before.events,
            before.counters,
            before.ledger,
        )
        assert (
            tuple(writer.connection.execute(timestamps_sql, (operation_id,)).fetchone())
            == timestamps_before
        )
        operation_state = setup._operation_state(writer, operation_id)
        assert operation_state is not None and operation_state[:2] == ("blocked", "conflict")
        assert (
            setup._outcome(
                lambda: setup._native(writer).finalize_operation(operation_id, "gateway-task-board")
            )
            == outcome
        )
    finally:
        writer.close()
