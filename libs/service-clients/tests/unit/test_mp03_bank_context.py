"""BankClient stamps strict context through the shared platform signer."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from service_auth.jws import TokenPolicy, verify_jws
from service_auth.signing import PlatformSigner, generate_keypair

from service_clients.bank import BankClient

if TYPE_CHECKING:
    from pathlib import Path

_RELEASE_OPERATION_ID = "op-00000000-0000-4000-8000-000000000031"
_SPLIT_OPERATION_ID = "op-00000000-0000-4000-8000-000000000032"
_RELEASE_SOURCE_REFERENCE = "task:t-1:approved"
_SPLIT_SOURCE_REFERENCE = "task:t-2:ruling:ruling-1"
_COMMITTED_AT = "2026-10-16T10:00:00Z"


def _settlement_response(
    operation_id: str,
    operation_kind: str,
    escrow_id: str,
    source_reference: str,
    worker_pct: int | None,
) -> dict[str, Any]:
    canonical_request: dict[str, object] = {
        "request_version": 1,
        "operation_kind": operation_kind,
        "escrow_id": escrow_id,
        "source_reference": source_reference,
    }
    if operation_kind == "escrow_release":
        canonical_request["recipient_account_id"] = "acct-1"
        result: dict[str, object] = {"escrow_status_at_commit": "released"}
        legs = [
            {
                "role": "recipient",
                "account_id": "acct-1",
                "direction": "credit",
                "amount": 20,
                "transaction_id": "tx-release",
                "balance_after": 20,
            }
        ]
        status = "released"
    else:
        assert worker_pct is not None
        canonical_request.update(
            worker_account_id="worker",
            poster_account_id="poster",
            worker_pct=worker_pct,
        )
        result = {"escrow_status_at_commit": "split", "worker_pct": worker_pct}
        legs = [
            {
                "role": "worker",
                "account_id": "worker",
                "direction": "credit",
                "amount": 14,
                "transaction_id": "tx-worker",
                "balance_after": 14,
            },
            {
                "role": "poster",
                "account_id": "poster",
                "direction": "credit",
                "amount": 6,
                "transaction_id": "tx-poster",
                "balance_after": 6,
            },
        ]
        status = "split"
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
            "operation_kind": operation_kind,
            "request_sha256": request_sha256,
            "source_reference": source_reference,
            "committed_at": _COMMITTED_AT,
            "event_id": 1,
            "escrow_id": escrow_id,
            "result": result,
            "legs": legs,
        },
        "current_escrow": {
            "escrow_id": escrow_id,
            "payer_account_id": "poster",
            "task_id": "t-1" if escrow_id == "esc-1" else "t-2",
            "amount": 20,
            "status": status,
            "created_at": _COMMITTED_AT,
            "resolved_at": _COMMITTED_AT,
        },
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_and_split_use_concrete_bank_resources(tmp_path: Path) -> None:
    _, public_key = generate_keypair("platform", tmp_path)
    signer = PlatformSigner("a-platform", str(tmp_path / "platform.key"), 50)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/escrow/esc-1/release":
            response_body = _settlement_response(
                _RELEASE_OPERATION_ID,
                "escrow_release",
                "esc-1",
                _RELEASE_SOURCE_REFERENCE,
                None,
            )
        else:
            response_body = _settlement_response(
                _SPLIT_OPERATION_ID,
                "escrow_split",
                "esc-2",
                _SPLIT_SOURCE_REFERENCE,
                70,
            )
        return httpx.Response(200, json=response_body)

    client = BankClient(
        base_url="https://bank",
        escrow_lock_path="/escrow/lock",
        escrow_release_path="/escrow/{escrow_id}/release",
        escrow_split_path="/escrow/{escrow_id}/split",
        timeout_seconds=5,
        platform_signer=signer,
    )
    await client.connection.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://bank", transport=httpx.MockTransport(handler)
    )
    try:
        await client.release_escrow(
            "esc-1",
            "acct-1",
            operation_id=_RELEASE_OPERATION_ID,
            source_reference=_RELEASE_SOURCE_REFERENCE,
        )
        await client.split_escrow(
            "esc-2",
            "worker",
            "poster",
            70,
            operation_id=_SPLIT_OPERATION_ID,
            source_reference=_SPLIT_SOURCE_REFERENCE,
        )
    finally:
        await client.close()

    policy = TokenPolicy(max_lifetime_seconds=50, clock_skew_seconds=0)
    release = verify_jws(json.loads(requests[0].content)["token"], public_key, policy=policy)
    split = verify_jws(json.loads(requests[1].content)["token"], public_key, policy=policy)
    assert (release.header.aud, release.header.resource) == (
        "central-bank",
        "POST /escrow/esc-1/release",
    )
    assert (split.header.aud, split.header.resource) == (
        "central-bank",
        "POST /escrow/esc-2/split",
    )
    assert release.payload["action"] == "escrow_release"
    assert release.payload["operation_id"] == _RELEASE_OPERATION_ID
    assert release.payload["source_reference"] == _RELEASE_SOURCE_REFERENCE
    assert release.payload["recipient_account_id"] == "acct-1"
    assert split.payload["action"] == "escrow_split"
    assert split.payload["operation_id"] == _SPLIT_OPERATION_ID
    assert split.payload["source_reference"] == _SPLIT_SOURCE_REFERENCE
    assert split.payload["worker_pct"] == 70
