"""T-124 current-V5 behavioral proofs for status-independent financial recovery."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from db_gateway_service import migration, recovery
from db_gateway_service.task_time import utc_now_us
from service_commons.exceptions import ServiceError

from tests.fakes.mp08_current_pending import (
    SubmittedTask,
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

_HISTORY_MODULE_NAME = "mp08_gateway_legacy_history_fixture"
_HISTORY_PATH = (
    Path(__file__).resolve().parents[4]
    / "services/db-gateway/tests/fakes/gateway_mp08_legacy_history.py"
)
_HISTORY_SPEC = importlib.util.spec_from_file_location(_HISTORY_MODULE_NAME, _HISTORY_PATH)
assert _HISTORY_SPEC is not None and _HISTORY_SPEC.loader is not None
_HISTORY_MODULE = importlib.util.module_from_spec(_HISTORY_SPEC)
sys.modules[_HISTORY_MODULE_NAME] = _HISTORY_MODULE
_HISTORY_SPEC.loader.exec_module(_HISTORY_MODULE)
prepare_legacy_worker_settlement_v4 = _HISTORY_MODULE.prepare_legacy_worker_settlement_v4


@pytest.fixture
def prepared_gateway(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> tuple[str, Any | None]:
    if "legacy_pending" in request.node.name:
        history = prepare_legacy_worker_settlement_v4(
            tmp_path / "legacy-pending",
            kind="pending",
            caller_kid="gateway-task-board",
        )
        _migrate_legacy_history_to_v5(history, backup_id="legacy-pending-before-v5")
        return str(history.database), history
    if "legacy_succeeded" in request.node.name:
        history = prepare_legacy_worker_settlement_v4(
            tmp_path / "legacy-succeeded",
            kind="succeeded",
            caller_kid="gateway-task-board",
        )
        _migrate_legacy_history_to_v5(history, backup_id="legacy-succeeded-before-v5")
        return str(history.database), history
    return str(tmp_path / "gateway.db"), None


def _migrate_legacy_history_to_v5(history: Any, *, backup_id: str) -> None:
    recovery.create_backup(
        history.inventory,
        backup_root=history.backup_root,
        backup_id=backup_id,
        work_root=history.work_root,
    )
    result = migration.migrate_v4_to_v5(
        history.inventory,
        backup_root=history.backup_root,
        backup_id=backup_id,
        work_root=history.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid="gateway-task-board",
    )
    assert result.observation == "committed_v5_verified"


@pytest.fixture
def gateway_db_path(prepared_gateway: tuple[str, Any | None]) -> str:
    return prepared_gateway[0]


@pytest.fixture
def legacy_history(
    prepared_gateway: tuple[str, Any | None],
) -> Any:
    history = prepared_gateway[1]
    assert history is not None
    return history


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


async def test_legacy_pending_approved_task_releases_and_repairs_without_read(
    _gateway: Any,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    legacy_history: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = SubmittedTask(
        task_id=legacy_history.task_id,
        poster_id=legacy_history.poster_id,
        worker_id=legacy_history.worker_id,
        escrow_id=legacy_history.escrow_id,
        accepted_bid_id=legacy_history.accepted_bid_id,
        accepted_bid_amount=legacy_history.accepted_bid_amount,
        reward=legacy_history.reward,
    )
    operation_id = str(legacy_history.operation_id)
    before_financial = financial_controls(gateway_db_path)
    task_before = read_rows(
        gateway_db_path,
        "SELECT status,escrow_pending,approved_at FROM board_tasks WHERE task_id=?",
        (setup.task_id,),
    )
    cutoff_before = read_rows(
        gateway_db_path,
        "SELECT lifecycle_version,review_cutoff_us FROM board_task_contracts WHERE task_id=?",
        (setup.task_id,),
    )
    assert cutoff_before == ((0, legacy_history.review_cutoff_us),)
    assert cast("int", cutoff_before[0][1]) > utc_now_us()
    assert read_rows(
        gateway_db_path,
        "SELECT observation_kind,effect_state,unresolved FROM board_legacy_effect_observations "
        "WHERE task_id=?",
        (setup.task_id,),
    ) == (("worker_settlement_pending", "pending", 1),)
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
                    "SELECT unresolved FROM board_legacy_effect_observations WHERE task_id=?",
                    (setup.task_id,),
                )
                == ((0,),)
            ),
            max_completed_ticks=20,
            timeout_per_tick_seconds=0.2,
        )

    after_financial = financial_controls(gateway_db_path)
    cutoff_after = read_rows(
        gateway_db_path,
        "SELECT lifecycle_version,review_cutoff_us FROM board_task_contracts WHERE task_id=?",
        (setup.task_id,),
    )
    approved_at = task_before[0][2]
    assert cutoff_after == cutoff_before
    final = {
        "release_calls": release.await_count,
        "task": read_rows(
            gateway_db_path,
            "SELECT status,escrow_pending,approved_at FROM board_tasks WHERE task_id=?",
            (setup.task_id,),
        ),
        "observation": read_rows(
            gateway_db_path,
            "SELECT unresolved FROM board_legacy_effect_observations WHERE task_id=?",
            (setup.task_id,),
        ),
        "receipt_count": len(_receipt_rows(gateway_db_path, operation_id)),
        "worker_balance": read_rows(
            gateway_db_path,
            "SELECT balance FROM bank_accounts WHERE account_id=?",
            (setup.worker_id,),
        ),
    }
    assert before_financial[0] + before_financial[1] == after_financial[0] + after_financial[1]
    assert final == {
        "release_calls": 1,
        "task": (("approved", 0, approved_at),),
        "observation": ((0,),),
        "receipt_count": 1,
        "worker_balance": ((setup.reward,),),
    }


async def test_legacy_succeeded_receipt_repairs_projection_without_bank_call(
    _gateway: Any,
    gateway_db_path: str,
    legacy_history: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = SubmittedTask(
        task_id=legacy_history.task_id,
        poster_id=legacy_history.poster_id,
        worker_id=legacy_history.worker_id,
        escrow_id=legacy_history.escrow_id,
        accepted_bid_id=legacy_history.accepted_bid_id,
        accepted_bid_amount=legacy_history.accepted_bid_amount,
        reward=legacy_history.reward,
    )
    operation_id = str(legacy_history.operation_id)
    receipt_before = _receipt_rows(gateway_db_path, operation_id)
    assert len(receipt_before) == 1
    assert receipt_before[0]["transaction_id"] is not None
    cutoff_before = read_rows(
        gateway_db_path,
        "SELECT lifecycle_version,review_cutoff_us FROM board_task_contracts WHERE task_id=?",
        (setup.task_id,),
    )
    assert cutoff_before == ((0, legacy_history.review_cutoff_us),)
    assert cast("int", cutoff_before[0][1]) > utc_now_us()
    before_financial = financial_controls(gateway_db_path)
    forbidden_bank = AsyncMock(side_effect=AssertionError("receipt-only repair called Bank"))
    async with running_board(
        runtime_dir=Path(gateway_db_path).parent,
        gateway_base_url=_gateway.base_url,
        monkeypatch=monkeypatch,
        escrow_release=forbidden_bank,
    ) as restarted:
        await restarted.wait_for_read_only_predicate(
            lambda: (
                read_rows(
                    gateway_db_path,
                    "SELECT unresolved FROM board_legacy_effect_observations WHERE task_id=?",
                    (setup.task_id,),
                )
                == ((0,),)
            ),
            max_completed_ticks=20,
            timeout_per_tick_seconds=0.2,
        )

    receipt_after = _receipt_rows(gateway_db_path, operation_id)
    after_financial = financial_controls(gateway_db_path)
    assert receipt_after == receipt_before
    assert before_financial == after_financial
    assert forbidden_bank.await_count == 0
    assert (
        read_rows(
            gateway_db_path,
            "SELECT lifecycle_version,review_cutoff_us FROM board_task_contracts WHERE task_id=?",
            (setup.task_id,),
        )
        == cutoff_before
    )
    final = {
        "task": read_rows(
            gateway_db_path,
            "SELECT status,escrow_pending,approved_at FROM board_tasks WHERE task_id=?",
            (setup.task_id,),
        ),
        "observation": read_rows(
            gateway_db_path,
            "SELECT unresolved FROM board_legacy_effect_observations WHERE task_id=?",
            (setup.task_id,),
        ),
        "worker_balance": read_rows(
            gateway_db_path,
            "SELECT balance FROM bank_accounts WHERE account_id=?",
            (setup.worker_id,),
        ),
    }
    assert final == {
        "task": (("approved", 0, None),),
        "observation": ((0,),),
        "worker_balance": ((setup.reward,),),
    }
