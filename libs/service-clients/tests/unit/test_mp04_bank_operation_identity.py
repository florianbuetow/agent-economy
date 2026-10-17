"""Shared settlement requests preserve caller intent through response-loss retry."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from service_commons.exceptions import ServiceError

from service_clients.bank import BankClient

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_OPERATION_ID = "op-00000000-0000-4000-8000-000000000001"
_SOURCE_REFERENCE = "t-1:approval"
_NOW = "2026-10-16T10:00:00Z"


def _response_body(*, split: bool) -> dict[str, Any]:
    kind = "escrow_split" if split else "escrow_release"
    fields: dict[str, object] = {
        "escrow_id": "esc-1",
        "source_reference": _SOURCE_REFERENCE,
        "operation_kind": kind,
        "request_version": 1,
    }
    if split:
        fields.update(worker_account_id="a-worker", poster_account_id="a-poster", worker_pct=60)
    else:
        fields["recipient_account_id"] = "a-worker"
    result: dict[str, object] = {"escrow_status_at_commit": "split" if split else "released"}
    if split:
        result["worker_pct"] = 60
    legs = [
        {
            "role": "worker" if split else "recipient",
            "account_id": "a-worker",
            "direction": "credit",
            "amount": 12 if split else 20,
            "transaction_id": "tx-worker",
            "balance_after": 12 if split else 20,
        }
    ]
    if split:
        legs.append(
            {
                "role": "poster",
                "account_id": "a-poster",
                "direction": "credit",
                "amount": 8,
                "transaction_id": "tx-poster",
                "balance_after": 8,
            }
        )
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": _OPERATION_ID,
            "operation_kind": kind,
            "request_sha256": hashlib.sha256(
                json.dumps(
                    fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest(),
            "source_reference": _SOURCE_REFERENCE,
            "committed_at": _NOW,
            "event_id": 1,
            "escrow_id": "esc-1",
            "result": result,
            "legs": legs,
        },
        "current_escrow": {
            "escrow_id": "esc-1",
            "payer_account_id": "a-poster",
            "task_id": "t-1",
            "amount": 20,
            "status": "split" if split else "released",
            "created_at": _NOW,
            "resolved_at": _NOW,
        },
    }


async def _invoke(client: BankClient, *, method_name: str) -> dict[str, Any]:
    common = {
        "escrow_id": "esc-1",
        "operation_id": _OPERATION_ID,
        "source_reference": _SOURCE_REFERENCE,
    }
    if "split" in method_name:
        split_method = client.split_escrow if method_name == "split_escrow" else client.escrow_split
        return await split_method(
            **common, worker_account_id="a-worker", poster_account_id="a-poster", worker_pct=60
        )
    release_method = (
        client.release_escrow if method_name == "release_escrow" else client.escrow_release
    )
    return await release_method(**common, recipient_account_id="a-worker")


@pytest.mark.parametrize(
    "method_name", ["release_escrow", "escrow_release", "split_escrow", "escrow_split"]
)
async def test_settlement_retry_signs_the_same_caller_identity_and_returns_original_receipt(
    method_name: str,
) -> None:
    split = "split" in method_name
    body = _response_body(split=split)
    signer = MagicMock()
    signer.sign.return_value = "signed-caller-intent"
    requests = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert json.loads(request.content) == {"token": "signed-caller-intent"}
        if len(requests) == 1:
            raise httpx.ReadError("Response lost after server commit", request=request)
        return httpx.Response(200, json=body)

    client = BankClient(
        "http://bank.invalid",
        "/escrow/lock",
        "/escrow/{escrow_id}/release",
        "/escrow/{escrow_id}/split",
        1,
        signer,
    )
    await client.close()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://bank.invalid"
    )
    try:
        with pytest.raises(ServiceError) as failure:
            await _invoke(client, method_name=method_name)
        assert failure.value.error == "central_bank_unavailable"
        assert failure.value.status_code == 502
        assert await _invoke(client, method_name=method_name) == body
        assert len(requests) == 2
        signed = [call.args[0] for call in signer.sign.call_args_list]
        assert len(signed) == 2
        assert signed[0] == signed[1]
        assert signed[0]["operation_id"] == _OPERATION_ID
        assert signed[0]["source_reference"] == _SOURCE_REFERENCE
        assert signed[0]["action"] == ("escrow_split" if split else "escrow_release")
        assert signed[0]["escrow_id"] == "esc-1"
    finally:
        await client.close()


@pytest.mark.parametrize("method_name", ["release_escrow", "split_escrow"])
async def test_settlement_conflict_preserves_the_winning_operation_identity(
    method_name: str,
) -> None:
    winner = "op-00000000-0000-4000-8000-000000000002"
    signer = MagicMock()
    signer.sign.return_value = "signed-losing-intent"
    client = BankClient(
        "http://bank.invalid",
        "/escrow/lock",
        "/escrow/{escrow_id}/release",
        "/escrow/{escrow_id}/split",
        1,
        signer,
    )
    await client.close()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={
                    "error": "financial_operation_already_committed",
                    "message": "Another operation won",
                    "details": {"winning_operation_id": winner},
                },
            )
        ),
        base_url="http://bank.invalid",
    )
    try:
        with pytest.raises(ServiceError) as failure:
            await _invoke(client, method_name=method_name)
        assert failure.value.status_code == 409
        assert failure.value.error == "financial_operation_already_committed"
        assert failure.value.details == {"winning_operation_id": winner}
        assert signer.sign.call_count == 1
    finally:
        await client.close()


async def test_lock_preserves_the_bank_operation_identity_validation_error() -> None:
    client = BankClient(
        "http://bank.invalid",
        "/escrow/lock",
        "/escrow/{escrow_id}/release",
        "/escrow/{escrow_id}/split",
        1,
        MagicMock(),
    )
    await client.close()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                400,
                json={
                    "error": "invalid_operation_id",
                    "message": "Invalid operation identity",
                    "details": {"field": "operation_id"},
                },
            )
        ),
        base_url="http://bank.invalid",
    )
    try:
        with pytest.raises(ServiceError) as failure:
            await client.lock_escrow("signed-invalid-intent")
        assert (failure.value.status_code, failure.value.error, failure.value.details) == (
            400,
            "invalid_operation_id",
            {"field": "operation_id"},
        )
    finally:
        await client.close()
