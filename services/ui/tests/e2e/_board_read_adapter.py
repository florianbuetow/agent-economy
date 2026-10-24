"""Test-only Task Board read adapter for the isolated UI browser server.

This suite intentionally starts only the UI process. The adapter supplies
Board-shaped wire records from the disposable visual seed database; it is not
evidence that the seeded Bank rows passed production receipt validation. Paid,
refund and split stories are declared explicitly below. All other stories use
only their seeded escrow fact and never infer payment from task status.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx

_E2E_DATABASE_ENV = "UI_E2E_DATABASE_PATH"
_FINANCIAL_OPERATION_IDS = {
    "t-task1": ("worker_paid", "op-00000000-0000-4000-8000-000000000101"),
    "t-task3": ("worker_paid", "op-00000000-0000-4000-8000-000000000103"),
    "t-task4": ("poster_refunded", "op-00000000-0000-4000-8000-000000000104"),
    "t-task5": ("split", "op-00000000-0000-4000-8000-000000000105"),
    "t-task-e1": ("worker_paid", "op-00000000-0000-4000-8000-000000000201"),
}


def _absent_effect(*, escrow_locked: bool) -> dict[str, object]:
    return {
        "schema_version": 1,
        "settlement_state": "locked" if escrow_locked else "attention_required",
        "receipt_state": "absent",
        "outcome": None,
        "operation_id": None,
        "receipt_reference": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    }


def _declared_receipt_effect(task_id: str) -> dict[str, object] | None:
    declared = _FINANCIAL_OPERATION_IDS.get(task_id)
    if declared is None:
        return None
    outcome, operation_id = declared
    return {
        "schema_version": 1,
        "settlement_state": "complete",
        "receipt_state": "confirmed",
        "outcome": outcome,
        "operation_id": operation_id,
        "receipt_reference": operation_id,
        "business_projection_pending": False,
        "pending_target_status": None,
    }


class SeededTaskBoardReader:
    """Return coherent public Board projections for disposable visual stories."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def _records(self) -> list[dict[str, Any]]:
        connection = sqlite3.connect(str(self._database_path))
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT bt.*, btc.lifecycle_version, "
                "(SELECT COUNT(*) FROM board_bids bb WHERE bb.task_id = bt.task_id) "
                "AS live_bid_count, "
                "(SELECT status FROM bank_escrow be WHERE be.escrow_id = bt.escrow_id) "
                "AS seeded_escrow_status "
                "FROM board_tasks bt "
                "LEFT JOIN board_task_contracts btc ON btc.task_id = bt.task_id "
                "ORDER BY bt.created_at DESC"
            ).fetchall()
        finally:
            connection.close()

        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["bid_count"] = record.pop("live_bid_count")
            escrow_status = record.pop("seeded_escrow_status")
            declared = _declared_receipt_effect(str(record["task_id"]))
            record["financial_effect"] = (
                declared
                if declared is not None
                else _absent_effect(escrow_locked=escrow_status == "locked")
            )
            record["acceptance_deadline"] = record["bidding_deadline"]
            records.append(record)
        return records

    async def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
    ) -> list[dict[str, Any]]:
        return [
            record
            for record in self._records()
            if (status is None or record["status"] == status)
            and (poster_id is None or record["poster_id"] == poster_id)
            and (worker_id is None or record["worker_id"] == worker_id)
        ]

    async def get_task(self, task_id: str) -> dict[str, Any]:
        for record in self._records():
            if record["task_id"] == task_id:
                return record
        request = httpx.Request("GET", f"http://task-board.invalid/tasks/{task_id}")
        response = httpx.Response(
            404,
            request=request,
            json={"error": "task_not_found", "message": "Task not found", "details": {}},
        )
        raise httpx.HTTPStatusError("task not found", request=request, response=response)


def reader_from_environment(environment: dict[str, str]) -> SeededTaskBoardReader:
    """Build the child-process adapter from an explicit disposable DB path."""
    raw_path = environment.get(_E2E_DATABASE_ENV)
    if raw_path is None:
        raise RuntimeError(f"{_E2E_DATABASE_ENV} is required for UI E2E reads")
    return SeededTaskBoardReader(Path(raw_path))
