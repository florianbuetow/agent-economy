"""Shared BankClient validates historical lock observations at its HTTP boundary."""

from __future__ import annotations

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

_OPERATION_ID = "op-00000000-0000-4000-8000-000000000301"


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _lock_token(operation_id: str) -> str:
    return create_jws(
        {
            "action": "escrow_lock",
            "operation_id": operation_id,
            "agent_id": "a-poster",
            "amount": 40,
            "task_id": "t-historical",
        },
        Ed25519PrivateKey.generate(),
        kid="a-poster",
        audience="central-bank",
        resource="POST /escrow/lock",
        token_ttl_seconds=60,
    )


def _legacy_lock(operation_id: str) -> dict[str, Any]:
    transaction = {
        "tx_id": "tx-historical-lock",
        "account_id": "a-poster",
        "type": "escrow_lock",
        "amount": 40,
        "balance_after": 60,
        "reference": "t-historical",
        "timestamp": "",
        "event_id": 0,
    }
    lock_fact = {
        "escrow_id": "esc-historical",
        "payer_account_id": "a-poster",
        "amount": 40,
        "task_id": "t-historical",
        "created_at": "",
        "event_id": -1,
    }
    request = {
        "request_version": 1,
        "operation_kind": "escrow_lock",
        "payer_account_id": "a-poster",
        "task_id": "t-historical",
        "amount": 40,
    }
    return {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": operation_id,
            "operation_kind": "escrow_lock",
            "request_sha256": _hash(request),
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_escrow_lock",
            "transaction": transaction,
            "observed_fact_sha256": _hash(transaction),
            "escrow_lock": lock_fact,
            "observed_status": "locked",
            "lock_fact_sha256": _hash(lock_fact),
            "resolution_fact": None,
            "resolution_fact_sha256": None,
        },
        "current_escrow": {
            "escrow_id": "esc-historical",
            "payer_account_id": "a-poster",
            "task_id": "t-historical",
            "amount": 40,
            "status": "released",
            "created_at": "",
            "resolved_at": "2026-10-17T18:00:00Z",
        },
    }


async def _call(body: dict[str, Any], status_code: int) -> dict[str, Any]:
    token = _lock_token(_OPERATION_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/escrow/lock"
        assert json.loads(request.content) == {"token": token}
        return httpx.Response(status_code, json=body)

    client = BankClient(
        base_url="https://bank",
        escrow_lock_path="/escrow/lock",
        escrow_release_path="/escrow/{escrow_id}/release",
        escrow_split_path="/escrow/{escrow_id}/split",
        timeout_seconds=1,
        platform_signer=MagicMock(),
    )
    await client.close()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://bank",
    )
    try:
        return await client.lock_escrow(token)
    finally:
        await client.close()


async def test_claimed_valid_legacy_lock_returns_exact_observation() -> None:
    expected = _legacy_lock(_OPERATION_ID)

    assert await _call(expected, 201) == expected


@pytest.mark.parametrize("mutation", ["attempt_id", "hash", "payer", "task", "amount"])
async def test_mismatched_legacy_lock_is_a_typed_dependency_failure(mutation: str) -> None:
    body = _legacy_lock(_OPERATION_ID)
    attempt = body["attempt"]
    observation = body["observation"]
    current = body["current_escrow"]
    assert isinstance(attempt, dict)
    assert isinstance(observation, dict)
    assert isinstance(current, dict)
    transaction = observation["transaction"]
    lock_fact = observation["escrow_lock"]
    assert isinstance(transaction, dict)
    assert isinstance(lock_fact, dict)
    if mutation == "attempt_id":
        attempt["operation_id"] = "op-00000000-0000-4000-8000-000000000302"
    elif mutation == "hash":
        observation["lock_fact_sha256"] = "0" * 64
    else:
        field = {"payer": "payer_account_id", "task": "task_id", "amount": "amount"}[mutation]
        value: object = {"payer": "a-other", "task": "t-other", "amount": 41}[mutation]
        lock_fact[field] = value
        current[field] = value
        transaction[{"payer": "account_id", "task": "reference", "amount": "amount"}[mutation]] = (
            value
        )
        observation["observed_fact_sha256"] = _hash(transaction)
        observation["lock_fact_sha256"] = _hash(lock_fact)

    with pytest.raises(ServiceError) as failure:
        await _call(body, 201)
    assert (failure.value.error, failure.value.status_code, failure.value.details) == (
        "central_bank_unavailable",
        502,
        {},
    )


async def test_malformed_success_is_a_typed_dependency_failure() -> None:
    with pytest.raises(ServiceError) as failure:
        await _call({"outcome": "legacy_observation"}, 201)
    assert (failure.value.error, failure.value.status_code) == ("central_bank_unavailable", 502)


async def test_non_success_mapping_precedes_financial_response_parsing() -> None:
    with pytest.raises(ServiceError) as failure:
        await _call(
            {
                "error": "financial_operation_already_committed",
                "message": "another operation won",
                "details": {"winning_operation_id": None},
            },
            409,
        )
    assert (failure.value.error, failure.value.status_code, failure.value.details) == (
        "financial_operation_already_committed",
        409,
        {"winning_operation_id": None},
    )
