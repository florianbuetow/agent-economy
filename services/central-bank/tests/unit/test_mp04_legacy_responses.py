"""The Bank preserves verified legacy outcomes and typed ownership failures."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from central_bank_service.services.ledger_db_client import LedgerDbClient

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_OPERATION_ID = "op-00000000-0000-4000-8000-000000000201"


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _observation(kind: str) -> dict[str, Any]:
    is_credit = kind == "credit"
    fields = (
        {"account_id": "a-poster", "amount": 50, "reference": "old-funding"}
        if is_credit
        else {"payer_account_id": "a-poster", "amount": 50, "task_id": "t-old"}
    )
    transaction = {
        "tx_id": "tx-old",
        "account_id": "a-poster",
        "type": kind,
        "amount": 50,
        "balance_after": 50,
        "reference": "old-funding" if is_credit else "t-old",
        "timestamp": "",
        "event_id": 0,
    }
    observation: dict[str, Any] = {
        "operation_id": None,
        "observed_kind": "observed_credit" if is_credit else "observed_escrow_lock",
        "transaction": transaction,
        "observed_fact_sha256": _hash(transaction),
    }
    projection = None
    if not is_credit:
        lock = {
            "escrow_id": "esc-old",
            "payer_account_id": "a-poster",
            "amount": 50,
            "task_id": "t-old",
            "created_at": "",
            "event_id": 0,
        }
        observation.update(
            escrow_lock=lock,
            observed_status="locked",
            lock_fact_sha256=_hash(lock),
            resolution_fact=None,
            resolution_fact_sha256=None,
        )
        projection = {key: value for key, value in lock.items() if key != "event_id"} | {
            "status": "locked",
            "resolved_at": None,
        }
    return {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": _OPERATION_ID,
            "operation_kind": kind,
            "request_sha256": _hash({"request_version": 1, "operation_kind": kind, **fields}),
        },
        "observation": observation,
        "current_escrow": projection,
    }


class _Client(LedgerDbClient):
    def install_gateway(self, gateway: AsyncMock) -> None:
        self._gateway = gateway


def _client() -> tuple[LedgerDbClient, AsyncMock]:
    client = _Client.__new__(_Client)
    gateway = AsyncMock()
    client.install_gateway(gateway)
    return client, gateway


async def _call(client: LedgerDbClient, kind: str) -> dict[str, object]:
    if kind == "credit":
        return await client.credit("a-poster", 50, "old-funding", _OPERATION_ID)
    if kind == "escrow_lock":
        return await client.escrow_lock("a-poster", 50, "t-old", _OPERATION_ID)
    if kind == "escrow_release":
        return await client.escrow_release(
            "esc-old", "a-worker", _OPERATION_ID, "task:t-old:approved"
        )
    return await client.escrow_split(
        "esc-old", "a-worker", 50, "a-poster", _OPERATION_ID, "task:t-old:ruling:r-one"
    )


@pytest.mark.parametrize(
    ("kind", "gateway_method"), [("credit", "credit_account"), ("escrow_lock", "escrow_lock")]
)
async def test_bank_returns_verified_historical_observation_unchanged(
    kind: str,
    gateway_method: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, gateway = _client()
    response = _observation(kind)
    method = getattr(gateway, gateway_method)
    method.return_value = response
    logger = logging.getLogger("central-bank")
    monkeypatch.setattr(logger, "propagate", False)
    caplog.set_level(logging.INFO, logger=logger.name)
    logger.addHandler(caplog.handler)
    try:
        result = await _call(client, kind)
    finally:
        logger.removeHandler(caplog.handler)
    assert result == response
    method.assert_awaited_once()
    assert method.await_args.kwargs["operation_id"] == _OPERATION_ID
    records = [record for record in caplog.records if record.getMessage() == "audit"]
    assert len(records) == 1
    assert records[0].operation == "legacy_observation"
    assert records[0].operation_id is None
    assert records[0].attempt_operation_id == _OPERATION_ID
    assert records[0].tx_id == "tx-old"


async def test_native_lock_audit_uses_winning_transaction_not_retry_candidate(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, gateway = _client()
    projection = {
        "escrow_id": "esc-winner",
        "payer_account_id": "a-poster",
        "task_id": "t-old",
        "amount": 50,
        "status": "locked",
        "created_at": "2026-10-17T10:00:00Z",
        "resolved_at": None,
    }
    gateway.escrow_lock.return_value = {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": _OPERATION_ID,
            "operation_kind": "escrow_lock",
            "request_sha256": _hash(
                {
                    "request_version": 1,
                    "operation_kind": "escrow_lock",
                    "payer_account_id": "a-poster",
                    "task_id": "t-old",
                    "amount": 50,
                }
            ),
            "source_reference": "t-old",
            "committed_at": projection["created_at"],
            "event_id": 1,
            "escrow_id": "esc-winner",
            "result": {"escrow_status_at_commit": "locked"},
            "legs": [
                {
                    "role": "payer",
                    "account_id": "a-poster",
                    "direction": "debit",
                    "amount": 50,
                    "transaction_id": "tx-winner",
                    "balance_after": 50,
                }
            ],
        },
        "current_escrow": projection,
    }
    logger = logging.getLogger("central-bank")
    monkeypatch.setattr(logger, "propagate", False)
    caplog.set_level(logging.INFO, logger=logger.name)
    logger.addHandler(caplog.handler)
    try:
        await _call(client, "escrow_lock")
    finally:
        logger.removeHandler(caplog.handler)
    records = [record for record in caplog.records if record.getMessage() == "audit"]
    assert len(records) == 1
    assert records[0].tx_id == "tx-winner"


@pytest.mark.parametrize(
    ("kind", "gateway_method", "code", "status", "details"),
    [
        (
            "credit",
            "credit_account",
            "legacy_operation_conflict",
            409,
            {
                "winning_operation_id": None,
                "observed_kind": "observed_credit",
                "tx_id": "tx-old",
                "observed_fact_sha256": "a" * 64,
            },
        ),
        (
            "escrow_lock",
            "escrow_lock",
            "legacy_operation_conflict",
            409,
            {
                "winning_operation_id": None,
                "observed_kind": "observed_escrow_lock",
                "tx_id": "tx-old",
                "observed_fact_sha256": "a" * 64,
                "escrow_id": "esc-old",
                "observed_status": "locked",
                "lock_fact_sha256": "b" * 64,
                "resolution_fact_sha256": None,
            },
        ),
        (
            "escrow_release",
            "escrow_release",
            "historical_settlement_unreplayable",
            409,
            {
                "winning_operation_id": None,
                "escrow_id": "esc-old",
                "observed_status": "released",
                "lock_fact_sha256": "b" * 64,
                "resolution_fact_sha256": "c" * 64,
            },
        ),
        (
            "escrow_split",
            "escrow_split",
            "historical_settlement_unreplayable",
            409,
            {
                "winning_operation_id": None,
                "escrow_id": "esc-old",
                "observed_status": "split",
                "lock_fact_sha256": "b" * 64,
                "resolution_fact_sha256": "c" * 64,
            },
        ),
        (
            "credit",
            "credit_account",
            "receipt_ownership_missing",
            500,
            {"record_kind": "credit", "record_id": "tx-old"},
        ),
        (
            "escrow_lock",
            "escrow_lock",
            "receipt_ownership_failed",
            500,
            {"record_kind": "escrow_lock", "record_id": "esc-old"},
        ),
        (
            "credit",
            "credit_account",
            "operation_id_conflict",
            409,
            {"winning_operation_id": _OPERATION_ID},
        ),
    ],
)
async def test_bank_preserves_typed_history_errors_and_exact_safe_details(
    kind: str, gateway_method: str, code: str, status: int, details: dict[str, object]
) -> None:
    client, gateway = _client()
    method = getattr(gateway, gateway_method)
    method.side_effect = ServiceError(code, "Financial ownership result", status, details)
    with pytest.raises(ServiceError) as error:
        await _call(client, kind)
    assert (error.value.error, error.value.status_code, error.value.details) == (
        code,
        status,
        details,
    )
    method.assert_awaited_once()
