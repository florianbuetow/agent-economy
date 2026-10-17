"""MP-06 Central Bank monetary boundary and store-parity proofs."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from db_gateway_service.schema import SQLITE_MAX_INTEGER as GATEWAY_SQLITE_MAX_INTEGER
from service_commons.exceptions import ServiceError

from central_bank_service.monetary import SQLITE_MAX_INTEGER
from central_bank_service.services.in_memory_ledger_store import InMemoryLedgerStore
from central_bank_service.services.ledger_db_client import LedgerDbClient


def _store() -> InMemoryLedgerStore:
    return InMemoryLedgerStore(db_path=f"mp06-bank-{uuid4()}")


def _operation_id() -> str:
    return f"op-{uuid4()}"


def _receipt(envelope: dict[str, object]) -> dict[str, object]:
    receipt = envelope["receipt"]
    assert isinstance(receipt, dict)
    return receipt


def _projection(envelope: dict[str, object]) -> dict[str, object]:
    projection = envelope["current_escrow"]
    assert isinstance(projection, dict)
    return projection


def _native_lock_envelope(operation_id: str, status: object) -> dict[str, object]:
    canonical_request = {
        "request_version": 1,
        "operation_kind": "escrow_lock",
        "payer_account_id": "a-payer",
        "task_id": "t-replay",
        "amount": 10,
    }
    request_sha256 = hashlib.sha256(
        json.dumps(
            canonical_request,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": "escrow_lock",
            "request_sha256": request_sha256,
            "source_reference": "t-replay",
            "committed_at": "2026-10-16T10:00:00Z",
            "event_id": 1,
            "escrow_id": "esc-original",
            "result": {"escrow_status_at_commit": "locked"},
            "legs": [
                {
                    "role": "payer",
                    "account_id": "a-payer",
                    "direction": "debit",
                    "amount": 10,
                    "transaction_id": "tx-lock",
                    "balance_after": 90,
                }
            ],
        },
        "current_escrow": {
            "escrow_id": "esc-original",
            "payer_account_id": "a-payer",
            "task_id": "t-replay",
            "amount": 10,
            "status": status,
            "created_at": "2026-10-16T10:00:00Z",
            "resolved_at": None if status == "locked" else "2026-10-16T11:00:00Z",
        },
    }


def _client_with_gateway(**methods: Any) -> LedgerDbClient:
    client = LedgerDbClient.__new__(LedgerDbClient)
    client._gateway = SimpleNamespace(**methods)
    return client


def _assert_service_error(
    exc_info: pytest.ExceptionInfo[ServiceError],
    *,
    error: str,
    status_code: int,
) -> None:
    assert exc_info.value.error == error
    assert exc_info.value.status_code == status_code


@pytest.mark.unit
def test_bank_money_range_matches_gateway_storage_boundary() -> None:
    assert SQLITE_MAX_INTEGER == GATEWAY_SQLITE_MAX_INTEGER


@pytest.mark.unit
def test_in_memory_store_rejects_boolean_money_without_state_change() -> None:
    store = _store()

    with pytest.raises(ServiceError) as create_error:
        store.create_account("a-bool", True, None)

    _assert_service_error(create_error, error="invalid_amount", status_code=400)
    assert store.count_accounts() == 0

    store.create_account("a-payer", 10, _operation_id())
    before_account = store.get_account("a-payer")
    before_transactions = store.get_transactions("a-payer")

    with pytest.raises(ServiceError) as credit_error:
        store.credit("a-payer", True, "bool-credit", _operation_id())
    with pytest.raises(ServiceError) as lock_error:
        store.escrow_lock("a-payer", True, "t-bool-lock", _operation_id())

    _assert_service_error(credit_error, error="invalid_amount", status_code=400)
    _assert_service_error(lock_error, error="invalid_amount", status_code=400)
    assert store.get_account("a-payer") == before_account
    assert store.get_transactions("a-payer") == before_transactions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_db_client_rejects_boolean_money_before_gateway_call() -> None:
    create_account = AsyncMock()
    credit_account = AsyncMock()
    escrow_lock = AsyncMock()
    client = _client_with_gateway(
        create_account=create_account,
        credit_account=credit_account,
        escrow_lock=escrow_lock,
    )

    with pytest.raises(ServiceError) as create_error:
        await client.create_account("a-bool", True, None)
    with pytest.raises(ServiceError) as credit_error:
        await client.credit("a-bool", True, "bool-credit", _operation_id())
    with pytest.raises(ServiceError) as lock_error:
        await client.escrow_lock("a-bool", True, "t-bool-lock", _operation_id())

    _assert_service_error(create_error, error="invalid_amount", status_code=400)
    _assert_service_error(credit_error, error="invalid_amount", status_code=400)
    _assert_service_error(lock_error, error="invalid_amount", status_code=400)
    create_account.assert_not_awaited()
    credit_account.assert_not_awaited()
    escrow_lock.assert_not_awaited()


@pytest.mark.unit
def test_in_memory_total_escrowed_can_exceed_one_stored_integer() -> None:
    store = _store()
    store.create_account("a-first", SQLITE_MAX_INTEGER, _operation_id())
    store.create_account("a-second", SQLITE_MAX_INTEGER, _operation_id())
    store.escrow_lock("a-first", SQLITE_MAX_INTEGER, "t-first", _operation_id())
    store.escrow_lock("a-second", SQLITE_MAX_INTEGER, "t-second", _operation_id())

    assert store.total_escrowed() == SQLITE_MAX_INTEGER * 2


@pytest.mark.unit
def test_in_memory_credit_overflow_is_atomic() -> None:
    store = _store()
    store.create_account("a-recipient", SQLITE_MAX_INTEGER, _operation_id())
    before_account = store.get_account("a-recipient")
    before_transactions = store.get_transactions("a-recipient")

    with pytest.raises(ServiceError) as exc_info:
        store.credit("a-recipient", 1, "overflow", _operation_id())

    _assert_service_error(exc_info, error="amount_out_of_range", status_code=409)
    assert store.get_account("a-recipient") == before_account
    assert store.get_transactions("a-recipient") == before_transactions


@pytest.mark.unit
def test_in_memory_release_overflow_keeps_escrow_locked() -> None:
    store = _store()
    store.create_account("a-payer", 1, _operation_id())
    store.create_account("a-recipient", SQLITE_MAX_INTEGER, _operation_id())
    escrow = store.escrow_lock("a-payer", 1, "t-release-overflow", _operation_id())
    escrow_id = str(_receipt(escrow)["escrow_id"])
    before_transactions = store.get_transactions("a-recipient")

    with pytest.raises(ServiceError) as exc_info:
        store.escrow_release(
            escrow_id,
            "a-recipient",
            _operation_id(),
            "task:t-release-overflow:approved",
        )

    _assert_service_error(exc_info, error="amount_out_of_range", status_code=409)
    assert store.get_account("a-recipient")["balance"] == SQLITE_MAX_INTEGER
    assert store.get_transactions("a-recipient") == before_transactions
    assert store._state.escrows[escrow_id]["status"] == "locked"


@pytest.mark.unit
def test_in_memory_split_prechecks_all_legs_before_mutation() -> None:
    store = _store()
    store.create_account("a-poster", 2, _operation_id())
    escrow = store.escrow_lock("a-poster", 2, "t-split-overflow", _operation_id())
    escrow_id = str(_receipt(escrow)["escrow_id"])
    store._state.accounts["a-poster"]["balance"] = SQLITE_MAX_INTEGER
    before_transactions = store.get_transactions("a-poster")

    with pytest.raises(ServiceError) as exc_info:
        store.escrow_split(
            escrow_id,
            "a-poster",
            50,
            "a-poster",
            _operation_id(),
            "task:t-split-overflow:ruling:r-one",
        )

    _assert_service_error(exc_info, error="amount_out_of_range", status_code=409)
    assert store.get_account("a-poster")["balance"] == SQLITE_MAX_INTEGER
    assert store.get_transactions("a-poster") == before_transactions
    assert store._state.escrows[escrow_id]["status"] == "locked"


@pytest.mark.unit
@pytest.mark.parametrize("settlement", ["release", "split"])
def test_in_memory_lock_replay_after_settlement_returns_actual_status(
    settlement: str,
) -> None:
    store = _store()
    store.create_account("a-payer", 100, _operation_id())
    store.create_account("a-worker", 0, None)
    lock_operation_id = _operation_id()
    first = store.escrow_lock("a-payer", 40, f"t-{settlement}", lock_operation_id)
    escrow_id = str(_receipt(first)["escrow_id"])
    if settlement == "release":
        store.escrow_release(
            escrow_id,
            "a-worker",
            _operation_id(),
            "task:t-release:approved",
        )
        expected_status = "released"
    else:
        store.escrow_split(
            escrow_id,
            "a-worker",
            50,
            "a-payer",
            _operation_id(),
            "task:t-split:ruling:r-one",
        )
        expected_status = "split"
    payer_before = store.get_account("a-payer")
    transactions_before = store.get_transactions("a-payer")

    replay = store.escrow_lock("a-payer", 40, f"t-{settlement}", lock_operation_id)

    assert _receipt(replay) == _receipt(first)
    assert _receipt(replay)["escrow_id"] == escrow_id
    assert _projection(replay)["status"] == expected_status
    assert store.get_account("a-payer") == payer_before
    assert store.get_transactions("a-payer") == transactions_before


@pytest.mark.unit
@pytest.mark.asyncio
async def test_db_client_lock_uses_gateway_replay_status() -> None:
    operation_id = _operation_id()
    gateway_lock = AsyncMock(
        return_value=_native_lock_envelope(operation_id, "released"),
    )
    client = _client_with_gateway(escrow_lock=gateway_lock)

    result = await client.escrow_lock("a-payer", 10, "t-replay", operation_id)

    assert _receipt(result)["escrow_id"] == "esc-original"
    assert _receipt(result)["operation_id"] == operation_id
    assert _projection(result)["status"] == "released"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, True, "unknown"])
async def test_db_client_lock_rejects_missing_or_invalid_gateway_status(status: object) -> None:
    operation_id = _operation_id()
    gateway_lock = AsyncMock(return_value=_native_lock_envelope(operation_id, status))
    client = _client_with_gateway(escrow_lock=gateway_lock)

    with pytest.raises(RuntimeError, match="invalid native financial envelope"):
        await client.escrow_lock("a-payer", 10, "t-replay", operation_id)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["credit", "release", "split"])
async def test_db_client_preserves_gateway_balance_overflow_conflict(
    operation: str,
) -> None:
    overflow = ServiceError("amount_out_of_range", "Balance exceeds SQLite INTEGER range", 409, {})
    if operation == "credit":
        client = _client_with_gateway(credit_account=AsyncMock(side_effect=overflow))
        call = client.credit("a-recipient", 1, "overflow", _operation_id())
    elif operation == "release":
        client = _client_with_gateway(
            get_escrow=AsyncMock(
                return_value={
                    "escrow_id": "esc-1",
                    "payer_account_id": "a-payer",
                    "amount": 1,
                    "status": "locked",
                },
            ),
            escrow_release=AsyncMock(side_effect=overflow),
        )
        call = client.escrow_release(
            "esc-1",
            "a-recipient",
            _operation_id(),
            "task:t-overflow:approved",
        )
    else:
        client = _client_with_gateway(
            get_escrow=AsyncMock(
                return_value={
                    "escrow_id": "esc-1",
                    "payer_account_id": "a-poster",
                    "amount": 1,
                    "status": "locked",
                },
            ),
            escrow_split=AsyncMock(side_effect=overflow),
        )
        call = client.escrow_split(
            "esc-1",
            "a-worker",
            50,
            "a-poster",
            _operation_id(),
            "task:t-overflow:ruling:r-one",
        )

    with pytest.raises(ServiceError) as exc_info:
        await call

    _assert_service_error(exc_info, error="amount_out_of_range", status_code=409)
