"""T-124 startup audit for frozen membership and dynamic delivery coverage."""

from __future__ import annotations

import hashlib
import importlib
import json
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

import pytest

from db_gateway_service import task_time
from tests.fakes.gateway_mp08_effects import prepare_native_pending
from tests.fakes.native_board_history import FIXED_NOW_US, BusinessClock

if TYPE_CHECKING:
    import sqlite3

    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
pytest_plugins = ["tests.fakes.gateway_mp08_app"]


@pytest.fixture(autouse=True)
def _fixed_business_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_time, "utc_now_us", BusinessClock(now_us=FIXED_NOW_US))


@pytest.fixture
def app_with_writer(gateway_mp08_app: TestClient) -> TestClient:
    return gateway_mp08_app


class _Audit(Protocol):
    @property
    def recovery_clean(self) -> bool: ...

    @property
    def findings(self) -> tuple[Any, ...]: ...


def _audit(connection: sqlite3.Connection) -> _Audit:
    try:
        module = importlib.import_module("db_gateway_service.effect_recovery_audit")
    except ModuleNotFoundError:
        pytest.fail("Missing MP-08 effect recovery audit capability")
    function = getattr(module, "audit_effect_recovery", None)
    assert callable(function), "Missing audit_effect_recovery entry point"
    return cast("_Audit", function(connection))


def _require_delivery_table(writer: DbWriter) -> None:
    exists = writer.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='board_task_effect_deliveries'"
    ).fetchone()
    assert exists is not None and tuple(exists) == (1,), (
        "Missing V5 effect-delivery table capability"
    )


def test_audit_accepts_complete_dynamic_native_coverage(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    prepare_native_pending(app_with_writer)
    result = _audit(db_writer.connection)
    assert result.recovery_clean
    assert result.findings == ()


def test_audit_rejects_native_operation_without_delivery(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    db_writer.connection.execute(
        "DELETE FROM board_task_effect_deliveries WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    result = _audit(db_writer.connection)
    assert not result.recovery_clean
    assert "native_delivery_missing" in {finding.code for finding in result.findings}


def test_audit_rejects_completed_delivery_bound_to_nonreceipt_event(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    event_id = db_writer.connection.execute(
        "SELECT event_id FROM events WHERE task_id=? ORDER BY event_id LIMIT 1",
        (effect.task_id,),
    ).fetchone()[0]
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET delivery_state='completed', "
        "next_attempt_at_us=NULL, completed_at_us=enqueued_at_us, completion_event_id=? "
        "WHERE effect_operation_id=?",
        (event_id, effect.operation_id),
    )
    db_writer.connection.commit()
    result = _audit(db_writer.connection)
    assert not result.recovery_clean
    assert "delivery_completion_receipt_mismatch" in {finding.code for finding in result.findings}


def test_audit_recomputes_frozen_member_hashes_byte_for_byte(
    db_writer: DbWriter,
) -> None:
    tables = {
        row[0]
        for row in db_writer.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "board_task_effect_delivery_cutover" in tables, (
        "Missing V5 effect-delivery cutover capability"
    )
    db_writer.connection.execute("PRAGMA ignore_check_constraints=ON")
    try:
        db_writer.connection.execute(
            "UPDATE board_task_effect_delivery_cutover SET combined_members_sha256=?",
            ("0" * 64,),
        )
        db_writer.connection.commit()
    finally:
        db_writer.connection.execute("PRAGMA ignore_check_constraints=OFF")
    result = _audit(db_writer.connection)
    assert not result.recovery_clean
    assert "cutover_combined_membership_mismatch" in {finding.code for finding in result.findings}


def test_audit_rejects_live_native_row_claiming_migrated_cutover_membership(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    _require_delivery_table(db_writer)
    db_writer.connection.execute("PRAGMA ignore_check_constraints=ON")
    db_writer.connection.execute(
        "UPDATE board_task_effect_deliveries SET cutover_singleton=1 WHERE effect_operation_id=?",
        (effect.operation_id,),
    )
    db_writer.connection.commit()
    result = _audit(db_writer.connection)
    assert not result.recovery_clean
    assert any("cutover" in finding.code for finding in result.findings)


def test_audit_rejects_competing_native_and_legacy_sources_for_one_task(
    app_with_writer: TestClient,
    db_writer: DbWriter,
) -> None:
    effect = prepare_native_pending(app_with_writer)
    task = db_writer.connection.execute(
        "SELECT poster_id,worker_id,escrow_id,reward,accepted_bid_id,status "
        "FROM board_tasks WHERE task_id=?",
        (effect.task_id,),
    ).fetchone()
    contract = db_writer.connection.execute(
        "SELECT accepted_bid_amount FROM board_task_contracts WHERE task_id=?",
        (effect.task_id,),
    ).fetchone()
    assert task is not None and contract is not None
    external_reference = f"op-{uuid4()}"
    fact = {
        "task_id": effect.task_id,
        "task_status": str(task[5]),
        "poster_id": str(task[0]),
        "worker_id": str(task[1]),
        "escrow_id": str(task[2]),
        "reward": int(task[3]),
        "accepted_bid_id": str(task[4]),
        "accepted_bid_amount": int(contract[0]),
        "external_reference": external_reference,
        "escrow_status": "locked",
        "escrow_pending": 1,
    }
    fact_json = json.dumps(fact, sort_keys=True, separators=(",", ":"))
    db_writer.connection.execute(
        "INSERT INTO board_legacy_effect_observations VALUES "
        "(?,?, 'worker_settlement_pending', ?, ?, ?, 'pending','approved',1)",
        (
            f"obs-{uuid4()}",
            effect.task_id,
            fact_json,
            hashlib.sha256(fact_json.encode()).hexdigest(),
            external_reference,
        ),
    )
    db_writer.connection.commit()
    result = _audit(db_writer.connection)
    assert not result.recovery_clean
    assert any(
        "competing" in finding.code or "overlap" in finding.code for finding in result.findings
    )
