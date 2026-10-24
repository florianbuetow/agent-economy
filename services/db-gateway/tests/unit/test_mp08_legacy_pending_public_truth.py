"""Regression for receipt-complete legacy settlement before bookkeeping adoption."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event
from tests.fakes.gateway_mp08_effects import prepare_legacy_v5

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def test_legacy_pending_exact_receipt_is_publicly_complete_before_adoption(
    tmp_path: Path,
) -> None:
    """Money plus an already-current task is complete despite pending bookkeeping."""
    source, writer = prepare_legacy_v5(tmp_path, kind="pending")
    try:
        writer.escrow_release(
            {
                "operation_id": source.operation_id,
                "escrow_id": source.escrow_id,
                "recipient_account_id": source.worker_id,
                "source_reference": f"task:{source.task_id}:approved",
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": "2099-01-01T00:00:04Z",
                "event": {
                    **make_event(
                        source="central-bank",
                        event_type="escrow.released",
                        task_id=source.task_id,
                        agent_id=source.worker_id,
                    ),
                    "timestamp": "2099-01-01T00:00:04Z",
                },
            }
        )
        task = DbReader(writer.connection).get_task(source.task_id)
        assert task is not None
        assert task["financial_effect"] == {
            "schema_version": 1,
            "settlement_state": "complete",
            "receipt_state": "confirmed",
            "outcome": "worker_paid",
            "operation_id": source.operation_id,
            "receipt_reference": source.operation_id,
            "business_projection_pending": False,
            "pending_target_status": None,
        }
    finally:
        writer.close()
