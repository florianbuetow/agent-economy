"""Audit log tests for ledger mutations."""

from __future__ import annotations

import logging

import pytest

from central_bank_service.services.in_memory_ledger_store import InMemoryLedgerStore


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
        ledger.create_account("a-account", 25)

        record = _get_audit_record(caplog, "create_account")
        assert record.account_id == "a-account"
        assert record.initial_balance == 25
        assert record.reference == "initial_balance"
        assert record.tx_id.startswith("tx-")
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
        ledger.create_account("a-account", 0)
        caplog.clear()

        result = ledger.credit("a-account", 10, "salary_round_1")

        record = _get_audit_record(caplog, "credit")
        assert record.account_id == "a-account"
        assert record.amount == 10
        assert record.reference == "salary_round_1"
        assert record.tx_id == result["tx_id"]
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
        ledger.create_account("a-payer", 100)
        caplog.clear()

        result = ledger.escrow_lock("a-payer", 50, "T-001")

        record = _get_audit_record(caplog, "escrow_lock")
        assert record.payer_account_id == "a-payer"
        assert record.amount == 50
        assert record.task_id == "T-001"
        assert record.escrow_id == result["escrow_id"]
        assert record.reference == "T-001"
        assert record.tx_id.startswith("tx-")
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
        ledger.create_account("a-payer", 100)
        ledger.create_account("a-worker", 0)
        escrow = ledger.escrow_lock("a-payer", 50, "T-RELEASE")
        caplog.clear()

        result = ledger.escrow_release(str(escrow["escrow_id"]), "a-worker")

        record = _get_audit_record(caplog, "escrow_release")
        assert record.escrow_id == str(escrow["escrow_id"])
        assert record.recipient == "a-worker"
        assert record.amount == 50
        assert record.reference == str(escrow["escrow_id"])
        assert record.tx_id.startswith("tx-")
        assert result["amount"] == 50
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
        ledger.create_account("a-poster", 100)
        ledger.create_account("a-worker", 0)
        escrow = ledger.escrow_lock("a-poster", 100, "T-SPLIT")
        caplog.clear()

        result = ledger.escrow_split(str(escrow["escrow_id"]), "a-worker", 40, "a-poster")

        record = _get_audit_record(caplog, "escrow_split")
        assert record.escrow_id == str(escrow["escrow_id"])
        assert record.worker_account_id == "a-worker"
        assert record.poster_account_id == "a-poster"
        assert record.worker_amount == 40
        assert record.poster_amount == 60
        assert record.reference == str(escrow["escrow_id"])
        assert result["worker_amount"] == 40
        assert result["poster_amount"] == 60
    finally:
        service_logger.removeHandler(caplog.handler)
        ledger.close()
