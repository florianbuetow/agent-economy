"""G01: Central Bank exact-amount settlement against a real in-process DB Gateway."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.integration.conftest import PLATFORM_AGENT_ID, make_jws_token, register_identity_agent

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = pytest.mark.integration


def _scalar(db_path: str, sql: str, params: tuple[object, ...] = ()) -> int:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(sql, params).fetchone()
    finally:
        connection.close()
    assert row is not None
    return int(row[0])


def _ledger_rows(db_path: str) -> int:
    return _scalar(
        db_path,
        "SELECT (SELECT COUNT(*) FROM bank_transactions)"
        " + (SELECT COUNT(*) FROM bank_operation_receipts)"
        " + (SELECT COUNT(*) FROM bank_operation_legs)",
    )


async def _account(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_key: Ed25519PrivateKey,
    agent_id: str,
    balance: int,
) -> None:
    await register_identity_agent(gateway_client, agent_id)
    payload: dict[str, Any] = {
        "action": "create_account",
        "agent_id": agent_id,
        "initial_balance": balance,
    }
    if balance:
        payload["operation_id"] = f"op-{uuid4()}"
    token = make_jws_token(platform_key, PLATFORM_AGENT_ID, payload)
    response = await client.post("/accounts", json={"token": token})
    assert response.status_code == 201, response.text


async def test_settle_budget_100_price_60_conserves_and_replays_without_new_rows(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    """One atomic receipt pays 60 to the worker and refunds 40; a retry adds nothing."""
    platform_key = platform_keypair[0]
    await _account(client, gateway_client, platform_key, "a-poster", 100)
    await _account(client, gateway_client, platform_key, "a-worker", 0)
    lock_token = make_jws_token(
        agent_keypair[0],
        "a-poster",
        {
            "action": "escrow_lock",
            "operation_id": f"op-{uuid4()}",
            "agent_id": "a-poster",
            "amount": 100,
            "task_id": "t-g01",
        },
    )
    lock = await client.post("/escrow/lock", json={"token": lock_token})
    assert lock.status_code == 201, lock.text
    escrow_id = lock.json()["receipt"]["escrow_id"]
    settle_body = {
        "token": make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_settle",
                "operation_id": f"op-{uuid4()}",
                "escrow_id": escrow_id,
                "worker_account_id": "a-worker",
                "poster_account_id": "a-poster",
                "worker_amount": 60,
                "source_reference": "task:t-g01:approved",
            },
        )
    }

    first = await client.post(f"/escrow/{escrow_id}/settle", json=settle_body)
    rows = _ledger_rows(gateway_db_path)
    retry = await client.post(f"/escrow/{escrow_id}/settle", json=settle_body)

    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text
    assert retry.json()["receipt"] == first.json()["receipt"]
    assert _ledger_rows(gateway_db_path) == rows
    balance_sql = "SELECT balance FROM bank_accounts WHERE account_id = ?"
    assert _scalar(gateway_db_path, balance_sql, ("a-worker",)) == 60
    assert _scalar(gateway_db_path, balance_sql, ("a-poster",)) == 40
    assert _scalar(gateway_db_path, "SELECT SUM(balance) FROM bank_accounts") == 100
    assert _scalar(gateway_db_path, "SELECT COUNT(*) FROM bank_escrow WHERE status = 'locked'") == 0
