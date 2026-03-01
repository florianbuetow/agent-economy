"""Ledger safety tests (idempotency + concurrency)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from service_commons.exceptions import ServiceError

from central_bank_service.services.ledger import Ledger

pytestmark = pytest.mark.unit


def test_credit_is_idempotent_by_reference(tmp_path):
    """Duplicate credits with the same reference should not double-credit."""
    db_path = str(tmp_path / "central-bank.db")
    ledger = Ledger(db_path=db_path)
    try:
        ledger.create_account("a-test", 0)

        first = ledger.credit("a-test", 10, "salary_round_1")
        second = ledger.credit("a-test", 10, "salary_round_1")

        assert second == first
        account = ledger.get_account("a-test")
        assert account is not None
        assert account["balance"] == 10
    finally:
        ledger.close()


def test_credit_duplicate_reference_different_amount_errors(tmp_path):
    """Duplicate credits with the same reference but different amount should fail fast."""
    db_path = str(tmp_path / "central-bank.db")
    ledger = Ledger(db_path=db_path)
    try:
        ledger.create_account("a-test", 0)

        ledger.credit("a-test", 10, "salary_round_1")

        with pytest.raises(ServiceError) as exc_info:
            ledger.credit("a-test", 11, "salary_round_1")

        assert exc_info.value.error == "PAYLOAD_MISMATCH"
        account = ledger.get_account("a-test")
        assert account is not None
        assert account["balance"] == 10
    finally:
        ledger.close()


def test_escrow_lock_is_idempotent_by_task_id(tmp_path):
    """Duplicate escrow locks for the same (payer, task_id) should not double-debit."""
    db_path = str(tmp_path / "central-bank.db")
    ledger = Ledger(db_path=db_path)
    try:
        ledger.create_account("a-payer", 100)

        first = ledger.escrow_lock("a-payer", 50, "T-001")
        second = ledger.escrow_lock("a-payer", 50, "T-001")

        assert second["escrow_id"] == first["escrow_id"]
        account = ledger.get_account("a-payer")
        assert account is not None
        assert account["balance"] == 50
    finally:
        ledger.close()


def test_escrow_lock_same_task_different_amount_conflicts(tmp_path):
    """Escrow lock conflicts if a different amount is requested for the same task."""
    db_path = str(tmp_path / "central-bank.db")
    ledger = Ledger(db_path=db_path)
    try:
        ledger.create_account("a-payer", 100)

        ledger.escrow_lock("a-payer", 50, "T-001")

        with pytest.raises(ServiceError) as exc_info:
            ledger.escrow_lock("a-payer", 60, "T-001")

        assert exc_info.value.error == "ESCROW_ALREADY_LOCKED"
        assert exc_info.value.status_code == 409
        account = ledger.get_account("a-payer")
        assert account is not None
        assert account["balance"] == 50
    finally:
        ledger.close()


def test_escrow_release_is_atomic(tmp_path):
    """Concurrent release attempts must not double-credit the recipient."""
    db_path = str(tmp_path / "central-bank.db")
    ledger_a = Ledger(db_path=db_path)
    ledger_b = Ledger(db_path=db_path)
    try:
        ledger_a.create_account("a-payer", 100)
        ledger_a.create_account("a-worker", 0)
        escrow = ledger_a.escrow_lock("a-payer", 50, "T-RELEASE")
        escrow_id = str(escrow["escrow_id"])

        def release(ledger: Ledger):
            return ledger.escrow_release(escrow_id, "a-worker")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(release, ledger_a), pool.submit(release, ledger_b)]

        results = []
        errors = []
        for fut in futures:
            try:
                results.append(fut.result())
            except Exception as exc:
                errors.append(exc)

        assert len(results) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], ServiceError)
        assert errors[0].error == "ESCROW_ALREADY_RESOLVED"

        worker = ledger_a.get_account("a-worker")
        assert worker is not None
        assert worker["balance"] == 50
    finally:
        ledger_a.close()
        ledger_b.close()


def test_escrow_split_is_atomic(tmp_path):
    """Concurrent split attempts must not double-credit either party."""
    db_path = str(tmp_path / "central-bank.db")
    ledger_a = Ledger(db_path=db_path)
    ledger_b = Ledger(db_path=db_path)
    try:
        ledger_a.create_account("a-poster", 100)
        ledger_a.create_account("a-worker", 0)
        escrow = ledger_a.escrow_lock("a-poster", 100, "T-SPLIT")
        escrow_id = str(escrow["escrow_id"])

        def split(ledger: Ledger):
            return ledger.escrow_split(escrow_id, "a-worker", 40, "a-poster")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(split, ledger_a), pool.submit(split, ledger_b)]

        results = []
        errors = []
        for fut in futures:
            try:
                results.append(fut.result())
            except Exception as exc:
                errors.append(exc)

        assert len(results) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], ServiceError)
        assert errors[0].error == "ESCROW_ALREADY_RESOLVED"

        worker = ledger_a.get_account("a-worker")
        poster = ledger_a.get_account("a-poster")
        assert worker is not None
        assert poster is not None
        assert worker["balance"] == 40
        assert poster["balance"] == 60
    finally:
        ledger_a.close()
        ledger_b.close()
