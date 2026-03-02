from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from service_commons.exceptions import ServiceError

from task_board_service.clients.central_bank_client import CentralBankClient


def _make_client(mock_response: httpx.Response) -> CentralBankClient:
    """Create a CentralBankClient with a mock HTTP transport."""
    mock_signer = MagicMock()
    mock_signer.sign.return_value = "mock-jws-token"

    client = CentralBankClient(
        base_url="http://mock-bank:8002",
        escrow_lock_path="/escrow/lock",
        escrow_release_path="/escrow/{escrow_id}/release",
        escrow_split_path="/escrow/{escrow_id}/split",
        timeout_seconds=5,
        platform_signer=mock_signer,
    )

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(return_value=mock_response)
    client._client = mock_http
    return client


def _mock_response(status_code: int, json_body: dict[str, Any]) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        request=httpx.Request("POST", "http://mock-bank:8002/escrow/lock"),
    )


@pytest.mark.unit
async def test_lock_escrow_404_raises_account_not_found() -> None:
    response = _mock_response(
        404,
        {"error": "account_not_found", "message": "Account not found", "details": {}},
    )
    client = _make_client(response)

    with pytest.raises(ServiceError) as exc_info:
        await client.lock_escrow("fake-token")

    assert exc_info.value.status_code == 404
    assert exc_info.value.error == "account_not_found"


@pytest.mark.unit
async def test_lock_escrow_403_raises_forbidden() -> None:
    response = _mock_response(
        403,
        {"error": "forbidden", "message": "Not authorized", "details": {}},
    )
    client = _make_client(response)

    with pytest.raises(ServiceError) as exc_info:
        await client.lock_escrow("fake-token")

    assert exc_info.value.status_code == 403
    assert exc_info.value.error == "forbidden"


@pytest.mark.unit
async def test_lock_escrow_409_raises_conflict() -> None:
    response = _mock_response(
        409,
        {"error": "escrow_already_exists", "message": "Escrow exists", "details": {}},
    )
    client = _make_client(response)

    with pytest.raises(ServiceError) as exc_info:
        await client.lock_escrow("fake-token")

    assert exc_info.value.status_code == 409
    assert exc_info.value.error == "escrow_already_exists"


@pytest.mark.unit
async def test_lock_escrow_500_still_raises_502() -> None:
    response = _mock_response(
        500,
        {"error": "internal_error", "message": "Unexpected error", "details": {}},
    )
    client = _make_client(response)

    with pytest.raises(ServiceError) as exc_info:
        await client.lock_escrow("fake-token")

    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "central_bank_unavailable"


@pytest.mark.unit
async def test_release_escrow_404_raises_not_found() -> None:
    response = _mock_response(
        404,
        {"error": "escrow_not_found", "message": "Escrow not found", "details": {}},
    )
    client = _make_client(response)

    with pytest.raises(ServiceError) as exc_info:
        await client.release_escrow("esc-fake", "a-recipient")

    assert exc_info.value.status_code == 404
    assert exc_info.value.error == "escrow_not_found"


@pytest.mark.unit
async def test_release_escrow_403_raises_forbidden() -> None:
    response = _mock_response(
        403,
        {"error": "forbidden", "message": "Not authorized", "details": {}},
    )
    client = _make_client(response)

    with pytest.raises(ServiceError) as exc_info:
        await client.release_escrow("esc-fake", "a-recipient")

    assert exc_info.value.status_code == 403
    assert exc_info.value.error == "forbidden"


@pytest.mark.unit
async def test_split_escrow_404_raises_not_found() -> None:
    response = _mock_response(
        404,
        {"error": "account_not_found", "message": "Account not found", "details": {}},
    )
    client = _make_client(response)

    with pytest.raises(ServiceError) as exc_info:
        await client.split_escrow("esc-fake", "a-worker", "a-poster", 70)

    assert exc_info.value.status_code == 404
    assert exc_info.value.error == "account_not_found"


@pytest.mark.unit
async def test_split_escrow_403_raises_forbidden() -> None:
    response = _mock_response(
        403,
        {"error": "forbidden", "message": "Not authorized", "details": {}},
    )
    client = _make_client(response)

    with pytest.raises(ServiceError) as exc_info:
        await client.split_escrow("esc-fake", "a-worker", "a-poster", 70)

    assert exc_info.value.status_code == 403
    assert exc_info.value.error == "forbidden"
