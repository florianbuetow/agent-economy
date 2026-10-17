"""MP-06 Central Bank HTTP monetary-boundary proofs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import jws as core_jws
from service_auth.signing import create_jws

from central_bank_service.core.state import get_app_state
from central_bank_service.monetary import SQLITE_MAX_INTEGER

from .conftest import PLATFORM_AGENT_ID, make_jws_token
from .test_two_tier_auth import PLATFORM_ID, identity_down

__all__ = ["identity_down"]


def _ledger_result(payload: dict[str, object]) -> AsyncMock:
    ledger = AsyncMock()
    ledger.create_account.return_value = {
        "account_id": "a-test",
        "balance": payload.get("initial_balance"),
        "created_at": "2026-10-16T00:00:00Z",
    }
    ledger.credit.return_value = {"tx_id": "tx-test", "balance_after": 1}
    ledger.escrow_lock.return_value = {
        "escrow_id": "esc-test",
        "amount": payload.get("amount"),
        "task_id": payload.get("task_id"),
        "status": "locked",
    }
    ledger.escrow_split.return_value = {
        "escrow_id": "esc-test",
        "status": "split",
        "worker_amount": 0,
        "poster_amount": 1,
    }
    return ledger


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "payload", "expected_error"),
    [
        (
            "/accounts",
            {"action": "create_account", "agent_id": "a-test", "initial_balance": True},
            "invalid_amount",
        ),
        (
            "/accounts/a-test/credit",
            {"action": "credit", "account_id": "a-test", "amount": True, "reference": "r"},
            "invalid_amount",
        ),
        (
            "/escrow/lock",
            {
                "action": "escrow_lock",
                "agent_id": "a-test",
                "amount": True,
                "task_id": "t-bool",
            },
            "invalid_amount",
        ),
        (
            "/escrow/esc-test/split",
            {
                "action": "escrow_split",
                "escrow_id": "esc-test",
                "worker_account_id": "a-worker",
                "poster_account_id": "a-poster",
                "worker_pct": True,
            },
            "invalid_payload",
        ),
    ],
)
async def test_routes_reject_boolean_money_before_ledger_call(
    client: Any,
    platform_keypair: Any,
    agent_keypair: Any,
    path: str,
    payload: dict[str, object],
    expected_error: str,
) -> None:
    state = get_app_state()
    state.ledger = _ledger_result(payload)
    platform_key, _ = platform_keypair
    agent_key, _ = agent_keypair
    is_agent_operation = payload["action"] == "escrow_lock"
    token = make_jws_token(
        agent_key if is_agent_operation else platform_key,
        "a-test" if is_agent_operation else PLATFORM_AGENT_ID,
        payload,
    )

    response = await client.post(path, json={"token": token})

    assert response.status_code == 400
    assert response.json()["error"] == expected_error
    state.ledger.create_account.assert_not_awaited()
    state.ledger.credit.assert_not_awaited()
    state.ledger.escrow_lock.assert_not_awaited()
    state.ledger.escrow_split.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/accounts",
            {
                "action": "create_account",
                "agent_id": "a-test",
                "initial_balance": SQLITE_MAX_INTEGER + 1,
            },
        ),
        (
            "/accounts/a-test/credit",
            {
                "action": "credit",
                "account_id": "a-test",
                "amount": SQLITE_MAX_INTEGER + 1,
                "reference": "r",
            },
        ),
        (
            "/escrow/lock",
            {
                "action": "escrow_lock",
                "agent_id": "a-test",
                "amount": SQLITE_MAX_INTEGER + 1,
                "task_id": "t-range",
            },
        ),
    ],
)
async def test_routes_reject_money_above_sqlite_integer_range(
    client: Any,
    platform_keypair: Any,
    agent_keypair: Any,
    path: str,
    payload: dict[str, object],
) -> None:
    state = get_app_state()
    state.ledger = _ledger_result(payload)
    platform_key, _ = platform_keypair
    agent_key, _ = agent_keypair
    is_agent_operation = payload["action"] == "escrow_lock"
    token = make_jws_token(
        agent_key if is_agent_operation else platform_key,
        "a-test" if is_agent_operation else PLATFORM_AGENT_ID,
        payload,
    )

    response = await client.post(path, json={"token": token})

    assert response.status_code == 400
    assert response.json()["error"] == "amount_out_of_range"


def _split_token(
    private_key: Ed25519PrivateKey,
    *,
    kid: str,
    escrow_id: str,
    worker_pct: object,
) -> str:
    return create_jws(
        {
            "action": "escrow_split",
            "escrow_id": escrow_id,
            "worker_account_id": "a-worker",
            "worker_pct": worker_pct,
            "poster_account_id": "a-poster",
        },
        private_key,
        kid=kid,
        audience="central-bank",
        resource=f"POST /escrow/{escrow_id}/split",
        token_ttl_seconds=60,
    )


@pytest.mark.unit
async def test_split_wrong_signature_precedes_percentage_range(identity_down: Any) -> None:
    client, _state, _platform_private_key = identity_down
    token = _split_token(
        Ed25519PrivateKey.generate(),
        kid="a-not-platform",
        escrow_id="esc-auth-range",
        worker_pct=101,
    )

    response = await client.post("/escrow/esc-auth-range/split", json={"token": token})

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.unit
async def test_split_expired_signature_precedes_percentage_range(
    identity_down: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _state, private_key = identity_down
    issued_at = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(core_jws, "_clock", lambda: issued_at)
    token = _split_token(
        private_key,
        kid=PLATFORM_ID,
        escrow_id="esc-expired-range",
        worker_pct=101,
    )
    monkeypatch.setattr(
        core_jws,
        "_clock",
        lambda: datetime.fromtimestamp(int(issued_at.timestamp()) + 60, tz=UTC),
    )

    response = await client.post("/escrow/esc-expired-range/split", json={"token": token})

    assert response.status_code == 401
    assert response.json()["error"] == "token_expired"


@pytest.mark.unit
async def test_split_valid_signature_reaches_percentage_range(identity_down: Any) -> None:
    client, _state, private_key = identity_down
    token = _split_token(
        private_key,
        kid=PLATFORM_ID,
        escrow_id="esc-valid-range",
        worker_pct=101,
    )

    response = await client.post("/escrow/esc-valid-range/split", json={"token": token})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"


@pytest.mark.unit
async def test_split_wrong_signature_precedes_boolean_percentage_type(
    identity_down: Any,
) -> None:
    client, _state, _platform_private_key = identity_down
    token = _split_token(
        Ed25519PrivateKey.generate(),
        kid="a-not-platform",
        escrow_id="esc-auth-type",
        worker_pct=True,
    )

    response = await client.post("/escrow/esc-auth-type/split", json={"token": token})

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.unit
async def test_split_valid_signature_rejects_boolean_percentage(identity_down: Any) -> None:
    client, _state, private_key = identity_down
    token = _split_token(
        private_key,
        kid=PLATFORM_ID,
        escrow_id="esc-valid-type",
        worker_pct=True,
    )

    response = await client.post("/escrow/esc-valid-type/split", json={"token": token})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"
