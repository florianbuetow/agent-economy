"""Ledger safety tests (idempotency + concurrency)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from central_bank_service.services.ledger import Ledger

pytestmark = pytest.mark.unit


def _operation_id() -> str:
    return f"op-{uuid4()}"


def _receipt(envelope: dict[str, object]) -> dict[str, object]:
    receipt = envelope["receipt"]
    assert isinstance(receipt, dict)
    return receipt


def test_credit_is_idempotent_by_reference(tmp_path):
    """Duplicate credits with the same reference should not double-credit."""
    db_path = str(tmp_path / "central-bank.db")
    ledger = Ledger(db_path=db_path)
    try:
        ledger.create_account("a-test", 0, None)

        operation_id = _operation_id()
        first = ledger.credit("a-test", 10, "salary_round_1", operation_id)
        second = ledger.credit("a-test", 10, "salary_round_1", operation_id)

        assert second == first
        assert _receipt(second)["operation_id"] == operation_id
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
        ledger.create_account("a-test", 0, None)

        winner = _operation_id()
        ledger.credit("a-test", 10, "salary_round_1", winner)

        with pytest.raises(ServiceError) as exc_info:
            ledger.credit("a-test", 11, "salary_round_1", _operation_id())

        assert (exc_info.value.status_code, exc_info.value.error, exc_info.value.details) == (
            409,
            "financial_operation_already_committed",
            {"winning_operation_id": winner},
        )
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
        ledger.create_account("a-payer", 100, _operation_id())

        operation_id = _operation_id()
        first = ledger.escrow_lock("a-payer", 50, "T-001", operation_id)
        second = ledger.escrow_lock("a-payer", 50, "T-001", operation_id)

        assert second == first
        assert _receipt(second)["escrow_id"] == _receipt(first)["escrow_id"]
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
        ledger.create_account("a-payer", 100, _operation_id())

        winner = _operation_id()
        ledger.escrow_lock("a-payer", 50, "T-001", winner)

        with pytest.raises(ServiceError) as exc_info:
            ledger.escrow_lock("a-payer", 60, "T-001", _operation_id())

        assert (exc_info.value.status_code, exc_info.value.error, exc_info.value.details) == (
            409,
            "financial_operation_already_committed",
            {"winning_operation_id": winner},
        )
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
        ledger_a.create_account("a-payer", 100, _operation_id())
        ledger_a.create_account("a-worker", 0, None)
        escrow = ledger_a.escrow_lock("a-payer", 50, "T-RELEASE", _operation_id())
        escrow_id = str(_receipt(escrow)["escrow_id"])
        release_operation_ids = (_operation_id(), _operation_id())

        def release(ledger: Ledger, operation_id: str):
            return ledger.escrow_release(
                escrow_id,
                "a-worker",
                operation_id,
                "task:T-RELEASE:approved",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(release, ledger_a, release_operation_ids[0]),
                pool.submit(release, ledger_b, release_operation_ids[1]),
            ]

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
        winning_operation_id = str(_receipt(results[0])["operation_id"])
        assert winning_operation_id in release_operation_ids
        assert (errors[0].status_code, errors[0].error, errors[0].details) == (
            409,
            "financial_operation_already_committed",
            {"winning_operation_id": winning_operation_id},
        )

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
        ledger_a.create_account("a-poster", 100, _operation_id())
        ledger_a.create_account("a-worker", 0, None)
        escrow = ledger_a.escrow_lock("a-poster", 100, "T-SPLIT", _operation_id())
        escrow_id = str(_receipt(escrow)["escrow_id"])
        split_operation_ids = (_operation_id(), _operation_id())

        def split(ledger: Ledger, operation_id: str):
            return ledger.escrow_split(
                escrow_id,
                "a-worker",
                40,
                "a-poster",
                operation_id,
                "task:T-SPLIT:ruling:r-one",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(split, ledger_a, split_operation_ids[0]),
                pool.submit(split, ledger_b, split_operation_ids[1]),
            ]

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
        winning_operation_id = str(_receipt(results[0])["operation_id"])
        assert winning_operation_id in split_operation_ids
        assert (errors[0].status_code, errors[0].error, errors[0].details) == (
            409,
            "financial_operation_already_committed",
            {"winning_operation_id": winning_operation_id},
        )

        worker = ledger_a.get_account("a-worker")
        poster = ledger_a.get_account("a-poster")
        assert worker is not None
        assert poster is not None
        assert worker["balance"] == 40
        assert poster["balance"] == 60
    finally:
        ledger_a.close()
        ledger_b.close()
