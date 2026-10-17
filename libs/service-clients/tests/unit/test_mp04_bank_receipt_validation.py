"""BankClient admits only receipts that prove the submitted financial command."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import create_jws
from service_commons.exceptions import ServiceError

from service_clients.bank import BankClient

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_LOCK_ID = "op-00000000-0000-4000-8000-000000000201"
_RELEASE_ID = "op-00000000-0000-4000-8000-000000000202"
_SPLIT_ID = "op-00000000-0000-4000-8000-000000000203"
_NOW = "2026-10-17T17:00:00Z"


def _hash(operation_kind: str, fields: dict[str, object]) -> str:
    canonical = {
        "request_version": 1,
        "operation_kind": operation_kind,
        **fields,
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _projection(status: str) -> dict[str, object]:
    return {
        "escrow_id": "esc-1",
        "payer_account_id": "a-poster",
        "task_id": "t-1",
        "amount": 100,
        "status": status,
        "created_at": "2026-10-17T16:59:00Z",
        "resolved_at": None if status == "locked" else _NOW,
    }


def _lock_response(status: str) -> dict[str, Any]:
    fields: dict[str, object] = {
        "payer_account_id": "a-poster",
        "task_id": "t-1",
        "amount": 100,
    }
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": _LOCK_ID,
            "operation_kind": "escrow_lock",
            "request_sha256": _hash("escrow_lock", fields),
            "source_reference": "t-1",
            "committed_at": _NOW,
            "event_id": 1,
            "escrow_id": "esc-1",
            "result": {"escrow_status_at_commit": "locked"},
            "legs": [
                {
                    "role": "payer",
                    "account_id": "a-poster",
                    "direction": "debit",
                    "amount": 100,
                    "transaction_id": "tx-lock",
                    "balance_after": 0,
                }
            ],
        },
        "current_escrow": _projection(status),
    }


def _release_response() -> dict[str, Any]:
    fields: dict[str, object] = {
        "escrow_id": "esc-1",
        "recipient_account_id": "a-worker",
        "source_reference": "task:t-1:approved",
    }
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": _RELEASE_ID,
            "operation_kind": "escrow_release",
            "request_sha256": _hash("escrow_release", fields),
            "source_reference": "task:t-1:approved",
            "committed_at": _NOW,
            "event_id": 2,
            "escrow_id": "esc-1",
            "result": {"escrow_status_at_commit": "released"},
            "legs": [
                {
                    "role": "recipient",
                    "account_id": "a-worker",
                    "direction": "credit",
                    "amount": 100,
                    "transaction_id": "tx-release",
                    "balance_after": 100,
                }
            ],
        },
        "current_escrow": _projection("released"),
    }


def _split_response(worker_pct: int) -> dict[str, Any]:
    worker_amount = worker_pct
    poster_amount = 100 - worker_pct
    fields = {
        "escrow_id": "esc-1",
        "worker_account_id": "a-worker",
        "poster_account_id": "a-poster",
        "worker_pct": worker_pct,
        "source_reference": "task:t-1:ruling:r-one",
    }
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": _SPLIT_ID,
            "operation_kind": "escrow_split",
            "request_sha256": _hash("escrow_split", fields),
            "source_reference": "task:t-1:ruling:r-one",
            "committed_at": _NOW,
            "event_id": 3,
            "escrow_id": "esc-1",
            "result": {"escrow_status_at_commit": "split", "worker_pct": worker_pct},
            "legs": [
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
            ],
        },
        "current_escrow": _projection("split"),
    }


def _signed_lock_token() -> str:
    return create_jws(
        {
            "action": "escrow_lock",
            "operation_id": _LOCK_ID,
            "agent_id": "a-poster",
            "amount": 100,
            "task_id": "t-1",
        },
        Ed25519PrivateKey.generate(),
        kid="a-poster",
        audience="central-bank",
        resource="POST /escrow/lock",
        token_ttl_seconds=60,
    )


def _client(body: dict[str, Any], status_code: int) -> BankClient:
    signer = MagicMock()
    signer.sign.return_value = "signed-platform-token"
    client = BankClient(
        "http://bank.invalid",
        "/escrow/lock",
        "/escrow/{escrow_id}/release",
        "/escrow/{escrow_id}/split",
        1,
        signer,
    )
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status_code, json=copy.deepcopy(body))
        ),
        base_url="http://bank.invalid",
    )
    return client


async def _invoke(client: BankClient, method_name: str) -> dict[str, Any]:
    if method_name == "lock_escrow":
        return await client.lock_escrow(_signed_lock_token())
    if method_name == "escrow_lock":
        return await client.escrow_lock(_signed_lock_token())
    if method_name == "release_escrow":
        return await client.release_escrow(
            "esc-1",
            "a-worker",
            operation_id=_RELEASE_ID,
            source_reference="task:t-1:approved",
        )
    if method_name == "escrow_release":
        return await client.escrow_release(
            "esc-1",
            "a-worker",
            operation_id=_RELEASE_ID,
            source_reference="task:t-1:approved",
        )
    if method_name == "split_escrow":
        return await client.split_escrow(
            "esc-1",
            "a-worker",
            "a-poster",
            0,
            operation_id=_SPLIT_ID,
            source_reference="task:t-1:ruling:r-one",
        )
    return await client.escrow_split(
        "esc-1",
        "a-worker",
        "a-poster",
        0,
        operation_id=_SPLIT_ID,
        source_reference="task:t-1:ruling:r-one",
    )


@pytest.mark.parametrize(
    ("method_name", "body", "status_code"),
    [
        ("lock_escrow", _lock_response("released"), 201),
        ("release_escrow", _release_response(), 200),
        ("split_escrow", _split_response(0), 200),
    ],
)
async def test_bank_client_accepts_truthful_receipts(
    method_name: str,
    body: dict[str, Any],
    status_code: int,
) -> None:
    client = _client(body, status_code)
    try:
        assert await _invoke(client, method_name) == body
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("method_name", "body", "status_code"),
    [
        ("lock_escrow", _lock_response("locked"), 201),
        ("escrow_lock", _lock_response("locked"), 201),
        ("release_escrow", _release_response(), 200),
        ("escrow_release", _release_response(), 200),
        ("split_escrow", _split_response(0), 200),
        ("escrow_split", _split_response(0), 200),
    ],
)
async def test_bank_client_rejects_corrupt_receipts_for_primary_methods_and_aliases(
    method_name: str,
    body: dict[str, Any],
    status_code: int,
) -> None:
    body["receipt"]["request_sha256"] = "f" * 64
    client = _client(body, status_code)
    try:
        with pytest.raises(ServiceError) as failure:
            await _invoke(client, method_name)
        assert (failure.value.error, failure.value.status_code, failure.value.details) == (
            "central_bank_unavailable",
            502,
            {},
        )
    finally:
        await client.close()


async def test_success_status_with_unparseable_lock_token_is_dependency_error() -> None:
    client = _client(_lock_response("locked"), 201)
    try:
        with pytest.raises(ServiceError) as failure:
            await client.lock_escrow("not-a-jws")
        assert (failure.value.error, failure.value.status_code) == (
            "central_bank_unavailable",
            502,
        )
    finally:
        await client.close()
