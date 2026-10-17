"""Strict producer-boundary tests for native financial receipts."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from service_auth.financial_responses import parse_native_financial_envelope
from service_commons.exceptions import ServiceError

from central_bank_service.services.ledger_db_client import LedgerDbClient

pytestmark = pytest.mark.unit


class _TestLedgerDbClient(LedgerDbClient):
    def install_gateway(self, gateway: AsyncMock) -> None:
        self._gateway = gateway


def _client_with_gateway(**methods: Any) -> LedgerDbClient:
    client = _TestLedgerDbClient.__new__(_TestLedgerDbClient)
    client.install_gateway(AsyncMock(**methods))
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    [
        {"outcome": "native_receipt"},
        {
            "outcome": "native_receipt",
            "receipt": {
                "receipt_version": 1,
                "operation_id": "op-00000000-0000-4000-8000-000000000001",
                "operation_kind": "credit",
                "request_sha256": "a" * 64,
                "source_reference": "funding",
                "committed_at": "now",
                "event_id": 1,
                "escrow_id": None,
                "result": {
                    "account_id": "a-recipient",
                    "amount": True,
                    "balance_after": 10,
                    "reference": "funding",
                },
                "legs": [],
            },
            "current_escrow": None,
        },
    ],
)
async def test_credit_rejects_malformed_gateway_success(malformed: object) -> None:
    operation_id = "op-00000000-0000-4000-8000-000000000001"
    gateway_credit = AsyncMock(return_value=malformed)
    client = _client_with_gateway(credit_account=gateway_credit)

    with pytest.raises(RuntimeError, match="native financial"):
        await client.credit("a-recipient", 10, "funding", operation_id)

    gateway_credit.assert_awaited_once()
    call = gateway_credit.await_args
    assert call is not None
    assert call.kwargs["operation_id"] == operation_id


@pytest.mark.asyncio
async def test_credit_rejects_mismatched_gateway_operation_identity() -> None:
    operation_id = "op-00000000-0000-4000-8000-000000000001"
    gateway_credit = AsyncMock(
        return_value={
            "outcome": "native_receipt",
            "receipt": {
                "receipt_version": 1,
                "operation_id": "op-00000000-0000-4000-8000-000000000002",
                "operation_kind": "credit",
                "request_sha256": "a" * 64,
                "source_reference": "funding",
                "committed_at": "now",
                "event_id": 1,
                "escrow_id": None,
                "result": {
                    "account_id": "a-recipient",
                    "amount": 10,
                    "balance_after": 10,
                    "reference": "funding",
                },
                "legs": [
                    {
                        "role": "account",
                        "account_id": "a-recipient",
                        "direction": "credit",
                        "amount": 10,
                        "transaction_id": "tx-original",
                        "balance_after": 10,
                    }
                ],
            },
            "current_escrow": None,
        }
    )
    client = _client_with_gateway(credit_account=gateway_credit)

    with pytest.raises(RuntimeError, match="mismatched native financial receipt"):
        await client.credit("a-recipient", 10, "funding", operation_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "projection",
    [
        {"account_id": "a-zero", "balance": True, "created_at": "now"},
        {"account_id": "a-zero", "balance": 0.5, "created_at": "now"},
        {"account_id": "a-zero", "balance": "0", "created_at": "now"},
        {"account_id": 7, "balance": 0, "created_at": "now"},
        {"account_id": "a-zero", "balance": 0},
        {"account_id": "a-zero", "balance": 0, "created_at": "now", "extra": 1},
    ],
)
async def test_zero_account_rejects_malformed_authoritative_projection(
    projection: object,
) -> None:
    client = _client_with_gateway(
        create_account=AsyncMock(return_value={"account_id": "a-zero"}),
        get_account=AsyncMock(return_value=projection),
    )
    with pytest.raises(RuntimeError, match="invalid account projection"):
        await client.create_account("a-zero", 0, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_balance", "operation_id"),
    [
        (10, None),
        (0, "op-00000000-0000-4000-8000-000000000001"),
    ],
)
async def test_account_operation_identity_is_rejected_before_gateway_call(
    initial_balance: int,
    operation_id: str | None,
) -> None:
    gateway_create = AsyncMock()
    client = _client_with_gateway(create_account=gateway_create)

    with pytest.raises(ServiceError) as error:
        await client.create_account("a-preflight", initial_balance, operation_id)

    assert getattr(error.value, "error", None) == "invalid_operation_id"
    gateway_create.assert_not_awaited()


def _credit_envelope() -> dict[str, Any]:
    fields = {"account_id": "a-recipient", "amount": 10, "reference": "funding"}
    canonical = {
        "request_version": 1,
        "operation_kind": "credit",
        **fields,
    }
    request_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": "op-00000000-0000-4000-8000-000000000001",
            "operation_kind": "credit",
            "request_sha256": request_hash,
            "source_reference": "funding",
            "committed_at": "now",
            "event_id": 1,
            "escrow_id": None,
            "result": {**fields, "balance_after": 10},
            "legs": [
                {
                    "role": "account",
                    "account_id": "a-recipient",
                    "direction": "credit",
                    "amount": 10,
                    "transaction_id": "tx-original",
                    "balance_after": 10,
                }
            ],
        },
        "current_escrow": None,
    }


def test_expected_credit_receipt_is_accepted_before_malformed_vectors() -> None:
    parsed = parse_native_financial_envelope(
        _credit_envelope(),
        "op-00000000-0000-4000-8000-000000000001",
        "credit",
        {"account_id": "a-recipient", "amount": 10, "reference": "funding"},
    )
    assert parsed["outcome"] == "native_receipt"


@pytest.mark.parametrize(
    ("path", "bad_value"),
    [
        (("receipt", "request_sha256"), "b" * 64),
        (("receipt", "source_reference"), "other"),
        (("receipt", "legs", 0, "role"), "payer"),
        (("receipt", "legs", 0, "direction"), "debit"),
        (("receipt", "legs", 0, "account_id"), "a-other"),
        (("receipt", "legs", 0, "amount"), 11),
        (("receipt", "legs", 0, "balance_after"), -1),
    ],
)
def test_credit_receipt_rejects_cross_field_inconsistency(
    path: tuple[str | int, ...], bad_value: object
) -> None:
    envelope = copy.deepcopy(_credit_envelope())
    target: Any = envelope
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = bad_value
    with pytest.raises(RuntimeError):
        parse_native_financial_envelope(
            envelope,
            "op-00000000-0000-4000-8000-000000000001",
            "credit",
            {"account_id": "a-recipient", "amount": 10, "reference": "funding"},
        )


def test_credit_receipt_rejects_empty_or_extra_legs() -> None:
    for legs in (
        [],
        [
            *_credit_envelope()["receipt"]["legs"],
            {**_credit_envelope()["receipt"]["legs"][0], "extra": "forbidden"},
        ],
    ):
        envelope = _credit_envelope()
        envelope["receipt"]["legs"] = legs
        with pytest.raises(RuntimeError):
            parse_native_financial_envelope(
                envelope,
                "op-00000000-0000-4000-8000-000000000001",
                "credit",
                {"account_id": "a-recipient", "amount": 10, "reference": "funding"},
            )


def test_split_receipt_rejects_misordered_legs_and_release_rejects_wrong_escrow() -> None:
    fields = {
        "escrow_id": "esc-1",
        "worker_account_id": "a-worker",
        "poster_account_id": "a-poster",
        "worker_pct": 40,
        "source_reference": "settlement-1",
    }
    canonical = {"request_version": 1, "operation_kind": "escrow_split", **fields}
    envelope: dict[str, Any] = {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": "op-00000000-0000-4000-8000-000000000003",
            "operation_kind": "escrow_split",
            "request_sha256": hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "source_reference": "settlement-1",
            "committed_at": "now",
            "event_id": 3,
            "escrow_id": "esc-1",
            "result": {"escrow_status_at_commit": "split", "worker_pct": 40},
            "legs": [
                {
                    "role": "poster",
                    "account_id": "a-poster",
                    "direction": "credit",
                    "amount": 60,
                    "transaction_id": "tx-poster",
                    "balance_after": 60,
                },
                {
                    "role": "worker",
                    "account_id": "a-worker",
                    "direction": "credit",
                    "amount": 40,
                    "transaction_id": "tx-worker",
                    "balance_after": 40,
                },
            ],
        },
        "current_escrow": {
            "escrow_id": "esc-1",
            "payer_account_id": "a-poster",
            "task_id": "t-1",
            "amount": 100,
            "status": "split",
            "created_at": "before",
            "resolved_at": "now",
        },
    }
    with pytest.raises(RuntimeError):
        parse_native_financial_envelope(
            envelope,
            "op-00000000-0000-4000-8000-000000000003",
            "escrow_split",
            fields,
        )
    envelope["receipt"]["legs"].reverse()
    envelope["current_escrow"]["escrow_id"] = "esc-other"
    with pytest.raises(RuntimeError):
        parse_native_financial_envelope(
            envelope,
            "op-00000000-0000-4000-8000-000000000003",
            "escrow_split",
            fields,
        )


def _escrow_envelope(
    kind: str, status: str, amount: int, resolved_at: str | None
) -> dict[str, Any]:
    operation_id = "op-00000000-0000-4000-8000-000000000004"
    if kind == "escrow_lock":
        fields: dict[str, object] = {
            "payer_account_id": "a-poster",
            "task_id": "t-1",
            "amount": 100,
        }
        source_reference = "t-1"
        result = {"escrow_status_at_commit": "locked"}
        legs = [
            {
                "role": "payer",
                "account_id": "a-poster",
                "direction": "debit",
                "amount": 100,
                "transaction_id": "tx-lock",
                "balance_after": 0,
            }
        ]
    elif kind == "escrow_release":
        fields = {
            "escrow_id": "esc-1",
            "recipient_account_id": "a-worker",
            "source_reference": "settle-1",
        }
        source_reference = "settle-1"
        result = {"escrow_status_at_commit": "released"}
        legs = [
            {
                "role": "recipient",
                "account_id": "a-worker",
                "direction": "credit",
                "amount": amount,
                "transaction_id": None if amount == 0 else "tx-release",
                "balance_after": None if amount == 0 else amount,
            }
        ]
    else:
        fields = {
            "escrow_id": "esc-1",
            "worker_account_id": "a-worker",
            "poster_account_id": "a-poster",
            "worker_pct": 40,
            "source_reference": "settle-1",
        }
        source_reference = "settle-1"
        result = {"escrow_status_at_commit": "split", "worker_pct": 40}
        worker_amount = amount * 40 // 100
        poster_amount = amount - worker_amount
        legs = [
            {
                "role": "worker",
                "account_id": "a-worker",
                "direction": "credit",
                "amount": worker_amount,
                "transaction_id": None if worker_amount == 0 else "tx-worker",
                "balance_after": None if worker_amount == 0 else worker_amount,
            },
            {
                "role": "poster",
                "account_id": "a-poster",
                "direction": "credit",
                "amount": poster_amount,
                "transaction_id": None if poster_amount == 0 else "tx-poster",
                "balance_after": None if poster_amount == 0 else poster_amount,
            },
        ]
    canonical = {"request_version": 1, "operation_kind": kind, **fields}
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": kind,
            "request_sha256": hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "source_reference": source_reference,
            "committed_at": "now",
            "event_id": 4,
            "escrow_id": "esc-1",
            "result": result,
            "legs": legs,
        },
        "current_escrow": {
            "escrow_id": "esc-1",
            "payer_account_id": "a-poster",
            "task_id": "t-1",
            "amount": amount,
            "status": status,
            "created_at": "before",
            "resolved_at": resolved_at,
        },
    }


def _escrow_fields(kind: str) -> dict[str, object]:
    if kind == "escrow_lock":
        return {"payer_account_id": "a-poster", "task_id": "t-1", "amount": 100}
    if kind == "escrow_release":
        return {
            "escrow_id": "esc-1",
            "recipient_account_id": "a-worker",
            "source_reference": "settle-1",
        }
    return {
        "escrow_id": "esc-1",
        "worker_account_id": "a-worker",
        "poster_account_id": "a-poster",
        "worker_pct": 40,
        "source_reference": "settle-1",
    }


@pytest.mark.parametrize(
    ("kind", "status", "resolved_at"),
    [
        ("escrow_lock", "locked", None),
        ("escrow_lock", "released", "later"),
        ("escrow_release", "released", "now"),
        ("escrow_split", "split", "now"),
    ],
)
def test_clean_escrow_projection_terminal_semantics(
    kind: str, status: str, resolved_at: str | None
) -> None:
    parse_native_financial_envelope(
        _escrow_envelope(kind, status, 100, resolved_at),
        "op-00000000-0000-4000-8000-000000000004",
        kind,
        _escrow_fields(kind),
    )


@pytest.mark.parametrize(
    ("kind", "status", "amount", "resolved_at"),
    [
        ("escrow_release", "released", 0, "now"),
        ("escrow_release", "split", 100, "now"),
        ("escrow_release", "released", 100, None),
        ("escrow_split", "released", 100, "now"),
        ("escrow_split", "split", 100, None),
        ("escrow_lock", "locked", 100, "unexpected"),
        ("escrow_lock", "released", 100, None),
    ],
)
def test_escrow_projection_rejects_impossible_terminal_semantics(
    kind: str,
    status: str,
    amount: int,
    resolved_at: str | None,
) -> None:
    with pytest.raises(RuntimeError):
        parse_native_financial_envelope(
            _escrow_envelope(kind, status, amount, resolved_at),
            "op-00000000-0000-4000-8000-000000000004",
            kind,
            _escrow_fields(kind),
        )
