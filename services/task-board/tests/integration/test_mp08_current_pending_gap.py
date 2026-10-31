"""T-124 behavioral proof for status-independent financial recovery (fresh V6)."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from db_gateway_service.task_time import utc_now_us
from service_commons.exceptions import ServiceError

from tests.fakes.mp08_current_pending import (
    create_submitted_task,
    financial_controls,
    generate_test_keypair,
    persisting_release,
    post_signed,
    read_rows,
    running_board,
)

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def gateway_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "gateway.db")


def _receipt_rows(db_path: str, operation_id: str) -> tuple[dict[str, object], ...]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return tuple(
            dict(row)
            for row in connection.execute(
                "SELECT r.operation_id AS receipt_operation_id,"
                "r.operation_kind,r.canonical_request_json,r.request_sha256,"
                "r.source_reference,r.committed_at,r.event_id,r.escrow_id,r.result_json,"
                "l.leg_index,l.role,l.account_id,l.direction,l.amount,l.transaction_id,"
                "l.balance_after FROM bank_operation_receipts AS r "
                "LEFT JOIN bank_operation_legs AS l ON l.operation_id=r.operation_id "
                "WHERE r.operation_id=? ORDER BY l.leg_index",
                (operation_id,),
            ).fetchall()
        )
    finally:
        connection.close()


async def test_native_claim_recovers_after_bank_returns_without_public_stimulus(
    _gateway: Any,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poster_key, _ = generate_test_keypair()
    worker_key, _ = generate_test_keypair()
    operation_id = f"op-{uuid.uuid4()}"
    outage = AsyncMock(
        side_effect=ServiceError(
            "central_bank_unavailable",
            "Injected pre-commit outage",
            502,
            {"phase": "before_commit"},
        )
    )
    async with running_board(
        runtime_dir=Path(gateway_db_path).parent,
        gateway_base_url=_gateway.base_url,
        monkeypatch=monkeypatch,
        escrow_release=outage,
    ) as board:
        setup = await create_submitted_task(board.client, gateway_client, poster_key, worker_key)
        failed = await post_signed(
            board.client,
            path=f"/tasks/{setup.task_id}/approve",
            private_key=poster_key,
            signer_id=setup.poster_id,
            payload={
                "action": "approve_task",
                "operation_id": operation_id,
                "expected_lifecycle_version": 2,
                "task_id": setup.task_id,
                "poster_id": setup.poster_id,
            },
        )
        assert failed.status_code == 502, failed.text

    cutoff_before = read_rows(
        gateway_db_path,
        "SELECT review_cutoff_us FROM board_task_contracts WHERE task_id=?",
        (setup.task_id,),
    )
    assert cutoff_before and cast("int", cutoff_before[0][0]) > utc_now_us()
    before_financial = financial_controls(gateway_db_path)
    assert read_rows(
        gateway_db_path,
        "SELECT operation_state,effect_state FROM board_task_operations WHERE operation_id=?",
        (operation_id,),
    ) == (("claimed", "pending"),)
    assert _receipt_rows(gateway_db_path, operation_id) == ()
    release = persisting_release(gateway_client, setup=setup, operation_id=operation_id)
    async with running_board(
        runtime_dir=Path(gateway_db_path).parent,
        gateway_base_url=_gateway.base_url,
        monkeypatch=monkeypatch,
        escrow_release=release,
    ) as restarted:
        await restarted.wait_for_read_only_predicate(
            lambda: (
                read_rows(
                    gateway_db_path,
                    "SELECT operation_state,effect_state FROM board_task_operations "
                    "WHERE operation_id=?",
                    (operation_id,),
                )
                == (("completed", "succeeded"),)
            ),
            max_completed_ticks=20,
            timeout_per_tick_seconds=0.2,
        )

    after_financial = financial_controls(gateway_db_path)
    assert (
        read_rows(
            gateway_db_path,
            "SELECT review_cutoff_us FROM board_task_contracts WHERE task_id=?",
            (setup.task_id,),
        )
        == cutoff_before
    )
    operation_rows = read_rows(
        gateway_db_path,
        "SELECT operation_state,effect_state,event_id FROM board_task_operations "
        "WHERE operation_id=?",
        (operation_id,),
    )
    final = {
        "release_calls": release.await_count,
        "operation": tuple(row[:2] for row in operation_rows),
        "event_persisted": bool(operation_rows and operation_rows[0][2] is not None),
        "task": read_rows(
            gateway_db_path,
            "SELECT status,escrow_pending FROM board_tasks WHERE task_id=?",
            (setup.task_id,),
        ),
        "receipt_count": len(_receipt_rows(gateway_db_path, operation_id)),
        "worker_balance": read_rows(
            gateway_db_path,
            "SELECT balance FROM bank_accounts WHERE account_id=?",
            (setup.worker_id,),
        ),
        "conserved_total": after_financial[0] + after_financial[1],
    }
    assert before_financial[0] + before_financial[1] == after_financial[0] + after_financial[1]
    assert final == {
        "release_calls": 1,
        "operation": (("completed", "succeeded"),),
        "event_persisted": True,
        "task": (("approved", 0),),
        "receipt_count": 1,
        "worker_balance": ((setup.reward,),),
        "conserved_total": setup.reward,
    }
