"""Adversarial concurrent escrow admission against the real DB Gateway."""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from typing import TYPE_CHECKING

import pytest

from tests.integration.conftest import make_jws_token
from tests.integration.test_endpoints import _create_account

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _rows(db_path: str, query: str, params: tuple[object, ...]) -> list[dict[str, object]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


async def test_two_individually_affordable_locks_cannot_overspend_one_account(
    client: AsyncClient,
    gateway_client: AsyncClient,
    gateway_db_path: str,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    """PUB-03: concurrent distinct tasks serialize against available funds."""
    payer_id = "a-adversarial-concurrent-poster"
    await _create_account(
        client,
        gateway_client,
        platform_keypair,
        payer_id,
        150,
        f"op-{uuid.uuid4()}",
    )

    requests: list[tuple[str, dict[str, str]]] = []
    for _index in range(2):
        task_id = f"t-{uuid.uuid4()}"
        operation_id = f"op-{uuid.uuid4()}"
        token = make_jws_token(
            agent_keypair[0],
            payer_id,
            {
                "action": "escrow_lock",
                "operation_id": operation_id,
                "agent_id": payer_id,
                "amount": 100,
                "task_id": task_id,
            },
        )
        requests.append((task_id, {"token": token}))

    responses = await asyncio.gather(
        *(client.post("/escrow/lock", json=body) for _task_id, body in requests)
    )

    assert sorted(response.status_code for response in responses) == [201, 402]
    rejected = next(response.json() for response in responses if response.status_code == 402)
    assert rejected["error"] == "insufficient_funds"
    assert _rows(
        gateway_db_path,
        "SELECT balance FROM bank_accounts WHERE account_id=?",
        (payer_id,),
    ) == [{"balance": 50}]
    escrow_rows = _rows(
        gateway_db_path,
        "SELECT payer_account_id,amount,task_id,status FROM bank_escrow WHERE payer_account_id=?",
        (payer_id,),
    )
    assert len(escrow_rows) == 1
    assert escrow_rows[0]["amount"] == 100
    assert escrow_rows[0]["status"] == "locked"
    assert escrow_rows[0]["task_id"] in {task_id for task_id, _body in requests}
    assert (
        len(
            _rows(
                gateway_db_path,
                "SELECT operation_id FROM bank_operation_receipts "
                "WHERE operation_kind='escrow_lock'",
                (),
            )
        )
        == 1
    )
