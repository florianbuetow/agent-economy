"""Controls for the four authentic MP-08 V4 migration histories."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, classify_schema
from db_gateway_service.task_contract_audit import audit_task_contracts
from tests.fakes.gateway_mp08_legacy_history import (
    prepare_historical_approved_worker_payment_v4,
    prepare_legacy_court_claim_v4,
    prepare_legacy_worker_settlement_v4,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _assert_clean_v4(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert classify_schema(connection).state is SchemaState.CURRENT_V4
    assert audit_money(connection).financially_clean
    assert audit_receipt_ownership(connection).ownership_clean
    assert audit_task_contracts(connection).contracts_clean
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='table' AND name='board_task_effect_deliveries'"
    ).fetchone() == (0,)
    return connection


def test_pending_worker_settlement_is_derived_by_verified_migrations(tmp_path: Path) -> None:
    history = prepare_legacy_worker_settlement_v4(
        tmp_path / "pending",
        kind="pending",
        caller_kid="gateway-task-board",
    )
    connection = _assert_clean_v4(history.database)
    try:
        assert connection.execute(
            "SELECT lifecycle_version FROM board_task_contracts WHERE task_id=?",
            (history.task_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT observation_kind,effect_state,external_reference,unresolved "
            "FROM board_legacy_effect_observations WHERE observation_id=?",
            (history.observation_id,),
        ).fetchone() == (
            "worker_settlement_pending",
            "pending",
            history.operation_id,
            1,
        )
    finally:
        connection.close()


def test_succeeded_worker_settlement_retains_exact_native_receipt(tmp_path: Path) -> None:
    history = prepare_legacy_worker_settlement_v4(
        tmp_path / "succeeded",
        kind="succeeded",
        caller_kid="gateway-task-board",
    )
    connection = _assert_clean_v4(history.database)
    try:
        assert connection.execute(
            "SELECT observation_kind,effect_state,external_reference,unresolved "
            "FROM board_legacy_effect_observations WHERE observation_id=?",
            (history.observation_id,),
        ).fetchone() == (
            "worker_settlement_succeeded",
            "succeeded",
            history.operation_id,
            1,
        )
        assert connection.execute(
            "SELECT operation_kind,escrow_id FROM bank_operation_receipts WHERE operation_id=?",
            (history.operation_id,),
        ).fetchone() == ("escrow_release", history.escrow_id)
    finally:
        connection.close()


def test_historical_approved_payment_has_only_pre_receipt_observations(
    tmp_path: Path,
) -> None:
    history = prepare_historical_approved_worker_payment_v4(
        tmp_path / "historical-approved",
        caller_kid="gateway-task-board",
    )
    connection = _assert_clean_v4(history.database)
    try:
        assert connection.execute(
            "SELECT status,escrow_pending FROM board_tasks WHERE task_id=?",
            (history.task_id,),
        ).fetchone() == ("approved", 0)
        assert connection.execute("SELECT COUNT(*) FROM bank_operation_receipts").fetchone() == (0,)
        assert connection.execute(
            "SELECT observed_kind FROM bank_legacy_transaction_observations ORDER BY observed_kind"
        ).fetchall() == [
            ("observed_credit",),
            ("observed_escrow_lock",),
            ("observed_escrow_release",),
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM board_task_operations WHERE task_id=?",
            (history.task_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM board_legacy_effect_observations WHERE task_id=?",
            (history.task_id,),
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_court_claim_observation_is_preserved_without_financial_source(tmp_path: Path) -> None:
    history = prepare_legacy_court_claim_v4(
        tmp_path / "court",
        caller_kid="gateway-task-board",
    )
    connection = _assert_clean_v4(history.database)
    try:
        assert connection.execute(
            "SELECT status,escrow_pending FROM board_tasks WHERE task_id=?",
            (history.task_id,),
        ).fetchone() == ("submitted", 0)
        assert connection.execute(
            "SELECT observation_kind,effect_state,external_reference,unresolved "
            "FROM board_legacy_effect_observations WHERE observation_id=?",
            (history.observation_id,),
        ).fetchone() == (
            "court_claim_succeeded",
            "succeeded",
            history.external_reference,
            1,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts "
            "WHERE escrow_id=? AND operation_kind IN ('escrow_release','escrow_split')",
            (history.escrow_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM board_task_operations WHERE task_id=?",
            (history.task_id,),
        ).fetchone() == (0,)
    finally:
        connection.close()
