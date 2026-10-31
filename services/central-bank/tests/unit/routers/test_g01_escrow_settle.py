"""G01: exact-amount escrow settlement through the public Central Bank API."""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import PLATFORM_AGENT_ID, make_jws_token, make_operation_id

pytestmark = pytest.mark.unit


async def _account(client: Any, platform_keypair: Any, account_id: str, balance: int) -> None:
    payload: dict[str, object] = {
        "action": "create_account",
        "agent_id": account_id,
        "initial_balance": balance,
    }
    if balance > 0:
        payload["operation_id"] = make_operation_id()
    token = make_jws_token(platform_keypair[0], PLATFORM_AGENT_ID, payload)
    response = await client.post("/accounts", json={"token": token})
    assert response.status_code == 201


async def _lock(client: Any, agent_keypair: Any, poster: str, amount: int, task_id: str) -> str:
    token = make_jws_token(
        agent_keypair[0],
        poster,
        {
            "action": "escrow_lock",
            "agent_id": poster,
            "amount": amount,
            "task_id": task_id,
            "operation_id": make_operation_id(),
        },
    )
    response = await client.post("/escrow/lock", json={"token": token})
    assert response.status_code == 201
    return str(response.json()["receipt"]["escrow_id"])


def _settle_token(
    platform_keypair: Any, escrow_id: str, worker_amount: object, operation_id: str
) -> str:
    return make_jws_token(
        platform_keypair[0],
        PLATFORM_AGENT_ID,
        {
            "action": "escrow_settle",
            "escrow_id": escrow_id,
            "worker_account_id": "a-worker",
            "poster_account_id": "a-poster",
            "worker_amount": worker_amount,
            "operation_id": operation_id,
            "source_reference": "task:T-G01:approved",
        },
    )


async def _balance(client: Any, agent_keypair: Any, account_id: str) -> int:
    token = make_jws_token(
        agent_keypair[0],
        account_id,
        {"action": "get_balance", "account_id": account_id},
    )
    response = await client.get(
        f"/accounts/{account_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    return int(response.json()["balance"])


async def test_settle_pays_price_and_refunds_unused_budget_once(
    client: Any, platform_keypair: Any, agent_keypair: Any
) -> None:
    """Budget 100 with price 60 pays 60 and refunds 40; an exact retry is a replay."""
    await _account(client, platform_keypair, "a-poster", 100)
    await _account(client, platform_keypair, "a-worker", 0)
    escrow_id = await _lock(client, agent_keypair, "a-poster", 100, "T-G01")
    operation_id = make_operation_id()

    first = await client.post(
        f"/escrow/{escrow_id}/settle",
        json={"token": _settle_token(platform_keypair, escrow_id, 60, operation_id)},
    )
    retry = await client.post(
        f"/escrow/{escrow_id}/settle",
        json={"token": _settle_token(platform_keypair, escrow_id, 60, operation_id)},
    )

    assert first.status_code == 200
    body = first.json()
    assert body["receipt"]["operation_kind"] == "escrow_split"
    assert body["receipt"]["result"] == {"escrow_status_at_commit": "split", "worker_amount": 60}
    assert [(leg["role"], leg["amount"]) for leg in body["receipt"]["legs"]] == [
        ("worker", 60),
        ("poster", 40),
    ]
    assert retry.status_code == 200
    assert retry.json()["receipt"] == body["receipt"]
    assert await _balance(client, agent_keypair, "a-worker") == 60
    assert await _balance(client, agent_keypair, "a-poster") == 40


@pytest.mark.parametrize("worker_amount", [101, -1, "60", True])
async def test_settle_rejects_amounts_outside_the_escrow(
    client: Any, platform_keypair: Any, agent_keypair: Any, worker_amount: object
) -> None:
    """The worker amount is a non-negative integer no larger than the escrow."""
    await _account(client, platform_keypair, "a-poster", 100)
    await _account(client, platform_keypair, "a-worker", 0)
    escrow_id = await _lock(client, agent_keypair, "a-poster", 100, "T-G01")

    response = await client.post(
        f"/escrow/{escrow_id}/settle",
        json={
            "token": _settle_token(platform_keypair, escrow_id, worker_amount, make_operation_id())
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_amount"
    assert await _balance(client, agent_keypair, "a-poster") == 0
