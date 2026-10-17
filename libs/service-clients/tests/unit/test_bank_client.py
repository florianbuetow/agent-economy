"""Tests for BankClient — the async Central Bank escrow HTTP client (WP-11, B15).

Every request goes through `httpx.MockTransport`, so no real socket is ever
opened. Ports the coverage that previously lived in task-board's own
CentralBankClient test suite (now retired — task-board consumes this shared
client instead of a hand-rolled duplicate): connection-failure mapping (shared
by every method via BaseServiceClient), then each escrow operation's
status-code-to-ServiceError mapping.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import create_jws
from service_commons.exceptions import ServiceError

from service_clients.bank import BankClient

Handler = Callable[[httpx.Request], httpx.Response]

_LOCK_OPERATION_ID = "op-00000000-0000-4000-8000-000000000011"
_RELEASE_OPERATION_ID = "op-00000000-0000-4000-8000-000000000012"
_SPLIT_OPERATION_ID = "op-00000000-0000-4000-8000-000000000013"
_LOCK_SOURCE_REFERENCE = "task-1"
_RELEASE_SOURCE_REFERENCE = "task:task-1:approved"
_SPLIT_SOURCE_REFERENCE = "task:task-1:ruling:ruling-1"
_COMMITTED_AT = "2026-10-16T10:00:00Z"


def _signed_lock_token() -> str:
    return create_jws(
        {
            "action": "escrow_lock",
            "operation_id": _LOCK_OPERATION_ID,
            "agent_id": "a-poster",
            "amount": 100,
            "task_id": "task-1",
        },
        Ed25519PrivateKey.generate(),
        kid="a-poster",
        audience="central-bank",
        resource="POST /escrow/lock",
        token_ttl_seconds=60,
    )


def _client(handler: Handler) -> BankClient:
    signer = MagicMock()
    signer.sign.return_value = "mock-jws-token"
    client = BankClient(
        base_url="http://bank.invalid",
        escrow_lock_path="/escrow/lock",
        escrow_release_path="/escrow/{escrow_id}/release",
        escrow_split_path="/escrow/{escrow_id}/split",
        timeout_seconds=1,
        platform_signer=signer,
    )
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://bank.invalid",
    )
    return client


def _json_response(status_code: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status_code, json=body)


def _escrow_receipt(
    operation_id: str,
    operation_kind: str,
    source_reference: str,
    status: str,
    worker_pct: int | None,
) -> dict[str, Any]:
    canonical_request: dict[str, object] = {
        "request_version": 1,
        "operation_kind": operation_kind,
    }
    result: dict[str, object] = {"escrow_status_at_commit": status}
    if operation_kind == "escrow_lock":
        canonical_request.update(payer_account_id="a-poster", task_id="task-1", amount=100)
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
    elif operation_kind == "escrow_release":
        canonical_request.update(
            escrow_id="esc-1",
            recipient_account_id="a-recipient",
            source_reference=source_reference,
        )
        legs = [
            {
                "role": "recipient",
                "account_id": "a-recipient",
                "direction": "credit",
                "amount": 100,
                "transaction_id": "tx-release",
                "balance_after": 100,
            }
        ]
    else:
        assert worker_pct is not None
        canonical_request.update(
            escrow_id="esc-1",
            worker_account_id="a-worker",
            poster_account_id="a-poster",
            worker_pct=worker_pct,
            source_reference=source_reference,
        )
        result["worker_pct"] = worker_pct
        legs = [
            {
                "role": "worker",
                "account_id": "a-worker",
                "direction": "credit",
                "amount": 70,
                "transaction_id": "tx-worker",
                "balance_after": 70,
            },
            {
                "role": "poster",
                "account_id": "a-poster",
                "direction": "credit",
                "amount": 30,
                "transaction_id": "tx-poster",
                "balance_after": 30,
            },
        ]
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
            "escrow_id": "esc-1",
            "result": result,
            "legs": legs,
        },
        "current_escrow": {
            "escrow_id": "esc-1",
            "payer_account_id": "a-poster",
            "task_id": "task-1",
            "amount": 100,
            "status": status,
            "created_at": _COMMITTED_AT,
            "resolved_at": None if status == "locked" else _COMMITTED_AT,
        },
    }


# ---------------------------------------------------------------------------
# Connection failure mapping — shared by every method via BaseServiceClient
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnectionFailureMapping:
    async def test_connect_error_maps_to_502_central_bank_unavailable(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _client(handler)

        with pytest.raises(ServiceError) as excinfo:
            await client.lock_escrow("fake-token")

        assert excinfo.value.error == "central_bank_unavailable"
        assert excinfo.value.status_code == 502

    async def test_timeout_maps_to_502_central_bank_unavailable(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        client = _client(handler)

        with pytest.raises(ServiceError) as excinfo:
            await client.lock_escrow("fake-token")

        assert excinfo.value.error == "central_bank_unavailable"
        assert excinfo.value.status_code == 502


# ---------------------------------------------------------------------------
# lock_escrow / escrow_lock
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLockEscrow:
    async def test_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/escrow/lock"
            return _json_response(
                201,
                _escrow_receipt(
                    _LOCK_OPERATION_ID,
                    "escrow_lock",
                    _LOCK_SOURCE_REFERENCE,
                    "locked",
                    None,
                ),
            )

        client = _client(handler)
        result = await client.lock_escrow(_signed_lock_token())
        assert result["receipt"]["operation_id"] == _LOCK_OPERATION_ID
        assert result["current_escrow"]["escrow_id"] == "esc-1"

    async def test_alias_matches_lock_escrow(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                201,
                _escrow_receipt(
                    _LOCK_OPERATION_ID,
                    "escrow_lock",
                    _LOCK_SOURCE_REFERENCE,
                    "locked",
                    None,
                ),
            )

        client = _client(handler)
        result = await client.escrow_lock(_signed_lock_token())
        assert result["receipt"]["result"]["escrow_status_at_commit"] == "locked"
        assert result["current_escrow"]["status"] == "locked"

    async def test_402_raises_insufficient_funds(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(402, {"error": "insufficient_funds", "details": {}})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.lock_escrow("fake-token")
        assert excinfo.value.error == "insufficient_funds"
        assert excinfo.value.status_code == 402

    async def test_404_raises_account_not_found(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                404, {"error": "account_not_found", "message": "Account not found", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.lock_escrow("fake-token")
        assert excinfo.value.error == "account_not_found"
        assert excinfo.value.status_code == 404

    async def test_403_raises_forbidden(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(403, {"error": "forbidden", "message": "Not authorized"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.lock_escrow("fake-token")
        assert excinfo.value.error == "forbidden"
        assert excinfo.value.status_code == 403

    async def test_409_raises_conflict_from_body(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                409, {"error": "escrow_already_exists", "message": "Escrow exists", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.lock_escrow("fake-token")
        assert excinfo.value.error == "escrow_already_exists"
        assert excinfo.value.status_code == 409

    async def test_500_raises_502_central_bank_unavailable(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(500, {"error": "internal_error"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.lock_escrow("fake-token")
        assert excinfo.value.error == "central_bank_unavailable"
        assert excinfo.value.status_code == 502


# ---------------------------------------------------------------------------
# release_escrow / escrow_release
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReleaseEscrow:
    async def test_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/escrow/esc-1/release"
            return _json_response(
                200,
                _escrow_receipt(
                    _RELEASE_OPERATION_ID,
                    "escrow_release",
                    _RELEASE_SOURCE_REFERENCE,
                    "released",
                    None,
                ),
            )

        client = _client(handler)
        result = await client.release_escrow(
            "esc-1",
            "a-recipient",
            operation_id=_RELEASE_OPERATION_ID,
            source_reference=_RELEASE_SOURCE_REFERENCE,
        )
        assert result["receipt"]["result"]["escrow_status_at_commit"] == "released"
        assert result["current_escrow"]["status"] == "released"

    async def test_alias_matches_release_escrow(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                200,
                _escrow_receipt(
                    _RELEASE_OPERATION_ID,
                    "escrow_release",
                    _RELEASE_SOURCE_REFERENCE,
                    "released",
                    None,
                ),
            )

        client = _client(handler)
        result = await client.escrow_release(
            "esc-1",
            "a-recipient",
            operation_id=_RELEASE_OPERATION_ID,
            source_reference=_RELEASE_SOURCE_REFERENCE,
        )
        assert result["receipt"]["operation_id"] == _RELEASE_OPERATION_ID
        assert result["current_escrow"]["status"] == "released"

    async def test_404_raises_not_found(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                404, {"error": "escrow_not_found", "message": "Escrow not found", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.release_escrow(
                "esc-fake",
                "a-recipient",
                operation_id=_RELEASE_OPERATION_ID,
                source_reference=_RELEASE_SOURCE_REFERENCE,
            )
        assert excinfo.value.error == "escrow_not_found"
        assert excinfo.value.status_code == 404

    async def test_403_raises_forbidden(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(403, {"error": "forbidden", "message": "Not authorized"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.release_escrow(
                "esc-fake",
                "a-recipient",
                operation_id=_RELEASE_OPERATION_ID,
                source_reference=_RELEASE_SOURCE_REFERENCE,
            )
        assert excinfo.value.error == "forbidden"
        assert excinfo.value.status_code == 403

    async def test_409_raises_conflict(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(409, {"error": "conflict", "message": "Central Bank conflict"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.release_escrow(
                "esc-fake",
                "a-recipient",
                operation_id=_RELEASE_OPERATION_ID,
                source_reference=_RELEASE_SOURCE_REFERENCE,
            )
        assert excinfo.value.error == "conflict"
        assert excinfo.value.status_code == 409

    async def test_400_raises_bad_request(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(400, {"error": "bad_request", "message": "rejected"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.release_escrow(
                "esc-fake",
                "a-recipient",
                operation_id=_RELEASE_OPERATION_ID,
                source_reference=_RELEASE_SOURCE_REFERENCE,
            )
        assert excinfo.value.error == "bad_request"
        assert excinfo.value.status_code == 400


# ---------------------------------------------------------------------------
# split_escrow / escrow_split
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSplitEscrow:
    async def test_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/escrow/esc-1/split"
            return _json_response(
                200,
                _escrow_receipt(
                    _SPLIT_OPERATION_ID,
                    "escrow_split",
                    _SPLIT_SOURCE_REFERENCE,
                    "split",
                    70,
                ),
            )

        client = _client(handler)
        result = await client.split_escrow(
            "esc-1",
            "a-worker",
            "a-poster",
            70,
            operation_id=_SPLIT_OPERATION_ID,
            source_reference=_SPLIT_SOURCE_REFERENCE,
        )
        assert result["receipt"]["result"] == {
            "escrow_status_at_commit": "split",
            "worker_pct": 70,
        }
        assert result["current_escrow"]["status"] == "split"

    async def test_alias_matches_split_escrow(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                200,
                _escrow_receipt(
                    _SPLIT_OPERATION_ID,
                    "escrow_split",
                    _SPLIT_SOURCE_REFERENCE,
                    "split",
                    70,
                ),
            )

        client = _client(handler)
        result = await client.escrow_split(
            escrow_id="esc-1",
            worker_account_id="a-worker",
            poster_account_id="a-poster",
            worker_pct=70,
            operation_id=_SPLIT_OPERATION_ID,
            source_reference=_SPLIT_SOURCE_REFERENCE,
        )
        assert result["receipt"]["operation_id"] == _SPLIT_OPERATION_ID
        assert result["current_escrow"]["status"] == "split"

    async def test_404_raises_not_found(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                404, {"error": "account_not_found", "message": "Account not found", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.split_escrow(
                "esc-fake",
                "a-worker",
                "a-poster",
                70,
                operation_id=_SPLIT_OPERATION_ID,
                source_reference=_SPLIT_SOURCE_REFERENCE,
            )
        assert excinfo.value.error == "account_not_found"
        assert excinfo.value.status_code == 404

    async def test_403_raises_forbidden(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(403, {"error": "forbidden", "message": "Not authorized"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.split_escrow(
                "esc-fake",
                "a-worker",
                "a-poster",
                70,
                operation_id=_SPLIT_OPERATION_ID,
                source_reference=_SPLIT_SOURCE_REFERENCE,
            )
        assert excinfo.value.error == "forbidden"
        assert excinfo.value.status_code == 403

    async def test_no_split_path_configured_raises_502(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("split path should not be called when unconfigured")

        signer = MagicMock()
        signer.sign.return_value = "mock-jws-token"
        client = BankClient(
            base_url="http://bank.invalid",
            escrow_lock_path="/escrow/lock",
            escrow_release_path="/escrow/{escrow_id}/release",
            escrow_split_path=None,
            timeout_seconds=1,
            platform_signer=signer,
        )
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://bank.invalid",
        )

        with pytest.raises(ServiceError) as excinfo:
            await client.split_escrow(
                "esc-1",
                "a-worker",
                "a-poster",
                70,
                operation_id=_SPLIT_OPERATION_ID,
                source_reference=_SPLIT_SOURCE_REFERENCE,
            )
        assert excinfo.value.error == "central_bank_unavailable"
        assert excinfo.value.status_code == 502
