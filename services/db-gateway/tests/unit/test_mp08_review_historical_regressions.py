"""Review regressions for exhaustive pre-receipt payment classification."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from db_gateway_service import migration, recovery
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from tests.fakes.gateway_mp08_legacy_history import (
    prepare_historical_approved_worker_payment_v4,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def test_corrupt_legacy_transaction_fact_cannot_claim_historical_payment(
    tmp_path: Path,
) -> None:
    source = prepare_historical_approved_worker_payment_v4(
        tmp_path,
        caller_kid="gateway-task-board",
    )
    recovery.create_backup(
        source.inventory,
        backup_root=source.backup_root,
        backup_id="before-v5-review-historical",
        work_root=source.work_root,
    )
    migration.migrate_v4_to_v5(
        source.inventory,
        backup_root=source.backup_root,
        backup_id="before-v5-review-historical",
        work_root=source.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    writer = DbWriter(
        str(source.database),
        5000,
        "wal",
        None,
        current_task_board_caller_kid="gateway-task-board",
    )
    try:
        before = DbReader(writer.connection).get_task(source.task_id)
        assert before is not None
        assert before["financial_effect"]["settlement_state"] == "historical"
        writer.connection.execute(
            "UPDATE bank_legacy_transaction_observations SET observed_fact_sha256=?",
            ("0" * 64,),
        )
        writer.connection.commit()
        task = DbReader(writer.connection).get_task(source.task_id)
        assert task is not None
        assert task["financial_effect"]["settlement_state"] != "historical"
        assert task["financial_effect"]["receipt_state"] != "historical"
        assert task["financial_effect"]["outcome"] is None
    finally:
        writer.close()
