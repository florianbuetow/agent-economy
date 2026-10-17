"""Audit log tests for ledger mutations."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from central_bank_service.services.in_memory_ledger_store import InMemoryLedgerStore


def _operation_id() -> str:
    return f"op-{uuid4()}"


def _receipt(envelope: dict[str, object]) -> dict[str, object]:
    receipt = envelope["receipt"]
    assert isinstance(receipt, dict)
    return receipt


def _legs(envelope: dict[str, object]) -> list[dict[str, object]]:
    legs = _receipt(envelope)["legs"]
    assert isinstance(legs, list)
    assert all(isinstance(leg, dict) for leg in legs)
    return legs


def _configure_audit_capture(caplog: pytest.LogCaptureFixture) -> logging.Logger:
    caplog.set_level(logging.INFO, logger="central-bank")
    service_logger = logging.getLogger("central-bank")
    service_logger.addHandler(caplog.handler)
    return service_logger


def _get_audit_record(caplog: pytest.LogCaptureFixture, operation: str) -> logging.LogRecord:
    matches = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and getattr(record, "operation", None) == operation
    ]
    assert matches, f"expected INFO audit log for operation={operation}"
    return matches[-1]


@pytest.mark.unit
def test_create_account_emits_audit_log(
    tmp_path: pytest.TempPathFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Creating an account should emit a structured audit log."""
    ledger = InMemoryLedgerStore(db_path=str(tmp_path / "central-bank.db"))
    service_logger = _configure_audit_capture(caplog)

    try:
        operation_id = _operation_id()
        result = ledger.create_account("a-account", 25, operation_id)

        record = _get_audit_record(caplog, "create_account")
        assert record.account_id == "a-account"
        assert record.initial_balance == 25
        assert record.reference == "initial_balance"
        assert record.operation_id == operation_id
        assert record.tx_id == _legs(result)[0]["transaction_id"]
    finally:
        service_logger.removeHandler(caplog.handler)
        ledger.close()


@pytest.mark.unit
def test_credit_emits_audit_log(
    tmp_path: pytest.TempPathFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Crediting an account should emit a structured audit log."""
    ledger = InMemoryLedgerStore(db_path=str(tmp_path / "central-bank.db"))
    service_logger = _configure_audit_capture(caplog)

    try:
        ledger.create_account("a-account", 0, None)
        caplog.clear()

        operation_id = _operation_id()
        result = ledger.credit("a-account", 10, "salary_round_1", operation_id)

        record = _get_audit_record(caplog, "credit")
        assert record.account_id == "a-account"
        assert record.amount == 10
        assert record.reference == "salary_round_1"
        assert record.operation_id == operation_id
        assert record.tx_id == _legs(result)[0]["transaction_id"]
    finally:
        service_logger.removeHandler(caplog.handler)
        ledger.close()


@pytest.mark.unit
def test_escrow_lock_emits_audit_log(
    tmp_path: pytest.TempPathFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Locking escrow should emit a structured audit log."""
    ledger = InMemoryLedgerStore(db_path=str(tmp_path / "central-bank.db"))
    service_logger = _configure_audit_capture(caplog)

    try:
        ledger.create_account("a-payer", 100, _operation_id())
        caplog.clear()

        operation_id = _operation_id()
        result = ledger.escrow_lock("a-payer", 50, "T-001", operation_id)

        record = _get_audit_record(caplog, "escrow_lock")
        assert record.payer_account_id == "a-payer"
        assert record.amount == 50
        assert record.task_id == "T-001"
        assert record.escrow_id == _receipt(result)["escrow_id"]
        assert record.operation_id == operation_id
        assert record.reference == "T-001"
        assert record.tx_id == _legs(result)[0]["transaction_id"]
    finally:
        service_logger.removeHandler(caplog.handler)
        ledger.close()


@pytest.mark.unit
def test_escrow_release_emits_audit_log(
    tmp_path: pytest.TempPathFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Releasing escrow should emit a structured audit log."""
    ledger = InMemoryLedgerStore(db_path=str(tmp_path / "central-bank.db"))
    service_logger = _configure_audit_capture(caplog)

    try:
        ledger.create_account("a-payer", 100, _operation_id())
        ledger.create_account("a-worker", 0, None)
        escrow = ledger.escrow_lock("a-payer", 50, "T-RELEASE", _operation_id())
        escrow_id = str(_receipt(escrow)["escrow_id"])
        caplog.clear()

        operation_id = _operation_id()
        source_reference = "task:T-RELEASE:approved"
        result = ledger.escrow_release(
            escrow_id,
            "a-worker",
            operation_id,
            source_reference,
        )

        record = _get_audit_record(caplog, "escrow_release")
        assert record.escrow_id == escrow_id
        assert record.recipient == "a-worker"
        assert record.amount == 50
        assert record.reference == escrow_id
        assert record.operation_id == operation_id
        assert record.source_reference == source_reference
        assert record.tx_id == _legs(result)[0]["transaction_id"]
        assert _legs(result)[0]["amount"] == 50
    finally:
        service_logger.removeHandler(caplog.handler)
        ledger.close()


@pytest.mark.unit
def test_escrow_split_emits_audit_log(
    tmp_path: pytest.TempPathFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Splitting escrow should emit a structured audit log."""
    ledger = InMemoryLedgerStore(db_path=str(tmp_path / "central-bank.db"))
    service_logger = _configure_audit_capture(caplog)

    try:
        ledger.create_account("a-poster", 100, _operation_id())
        ledger.create_account("a-worker", 0, None)
        escrow = ledger.escrow_lock("a-poster", 100, "T-SPLIT", _operation_id())
        escrow_id = str(_receipt(escrow)["escrow_id"])
        caplog.clear()

        operation_id = _operation_id()
        source_reference = "task:T-SPLIT:ruling:r-one"
        result = ledger.escrow_split(
            escrow_id,
            "a-worker",
            40,
            "a-poster",
            operation_id,
            source_reference,
        )

        record = _get_audit_record(caplog, "escrow_split")
        assert record.escrow_id == escrow_id
        assert record.worker_account_id == "a-worker"
        assert record.poster_account_id == "a-poster"
        assert record.worker_amount == 40
        assert record.poster_amount == 60
        assert record.reference == escrow_id
        assert record.operation_id == operation_id
        assert record.source_reference == source_reference
        worker_leg, poster_leg = _legs(result)
        assert worker_leg["amount"] == 40
        assert poster_leg["amount"] == 60
    finally:
        service_logger.removeHandler(caplog.handler)
        ledger.close()
