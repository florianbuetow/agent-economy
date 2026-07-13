"""Tests for BankClient — the async Central Bank escrow HTTP client (WP-11, B15).

Every request goes through `httpx.MockTransport`, so no real socket is ever
opened. Ports the coverage that previously lived in task-board's own
CentralBankClient test suite (now retired — task-board consumes this shared
client instead of a hand-rolled duplicate): connection-failure mapping (shared
by every method via BaseServiceClient), then each escrow operation's
status-code-to-ServiceError mapping.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from service_commons.exceptions import ServiceError

from service_clients.bank import BankClient

Handler = Callable[[httpx.Request], httpx.Response]


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
                201, {"escrow_id": "esc-1", "amount": 100, "task_id": "task-1", "status": "locked"}
            )

        client = _client(handler)
        result = await client.lock_escrow("fake-token")
        assert result["escrow_id"] == "esc-1"

    async def test_alias_matches_lock_escrow(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(201, {"escrow_id": "esc-1"})

        client = _client(handler)
        result = await client.escrow_lock("fake-token")
        assert result["escrow_id"] == "esc-1"

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
            return _json_response(200, {"status": "released"})

        client = _client(handler)
        result = await client.release_escrow("esc-1", "a-recipient")
        assert result["status"] == "released"

    async def test_alias_matches_release_escrow(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"status": "released"})

        client = _client(handler)
        result = await client.escrow_release("esc-1", "a-recipient")
        assert result["status"] == "released"

    async def test_404_raises_not_found(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                404, {"error": "escrow_not_found", "message": "Escrow not found", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.release_escrow("esc-fake", "a-recipient")
        assert excinfo.value.error == "escrow_not_found"
        assert excinfo.value.status_code == 404

    async def test_403_raises_forbidden(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(403, {"error": "forbidden", "message": "Not authorized"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.release_escrow("esc-fake", "a-recipient")
        assert excinfo.value.error == "forbidden"
        assert excinfo.value.status_code == 403

    async def test_409_raises_conflict(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(409, {"error": "conflict", "message": "Central Bank conflict"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.release_escrow("esc-fake", "a-recipient")
        assert excinfo.value.error == "conflict"
        assert excinfo.value.status_code == 409

    async def test_400_raises_bad_request(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(400, {"error": "bad_request", "message": "rejected"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.release_escrow("esc-fake", "a-recipient")
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
            return _json_response(200, {"status": "split"})

        client = _client(handler)
        result = await client.split_escrow("esc-1", "a-worker", "a-poster", 70)
        assert result["status"] == "split"

    async def test_alias_matches_split_escrow(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(200, {"status": "split"})

        client = _client(handler)
        result = await client.escrow_split(
            escrow_id="esc-1",
            worker_account_id="a-worker",
            poster_account_id="a-poster",
            worker_pct=70,
        )
        assert result["status"] == "split"

    async def test_404_raises_not_found(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(
                404, {"error": "account_not_found", "message": "Account not found", "details": {}}
            )

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.split_escrow("esc-fake", "a-worker", "a-poster", 70)
        assert excinfo.value.error == "account_not_found"
        assert excinfo.value.status_code == 404

    async def test_403_raises_forbidden(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return _json_response(403, {"error": "forbidden", "message": "Not authorized"})

        client = _client(handler)
        with pytest.raises(ServiceError) as excinfo:
            await client.split_escrow("esc-fake", "a-worker", "a-poster", 70)
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
            await client.split_escrow("esc-1", "a-worker", "a-poster", 70)
        assert excinfo.value.error == "central_bank_unavailable"
        assert excinfo.value.status_code == 502
