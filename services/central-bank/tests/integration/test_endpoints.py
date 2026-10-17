"""Integration tests — the real central-bank app driven end-to-end against a
real, in-process DB Gateway (the production `LedgerDbClient` path,
T-071/GAP-F1). Unlike tests/unit (InMemoryLedgerStore fake), every assertion
here is checked both through the central-bank API and by reading the
gateway's own SQLite file directly.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from tests.integration.conftest import PLATFORM_AGENT_ID, make_jws_token, register_identity_agent

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _read_one(
    db_path: str, query: str, params: tuple[object, ...] = ()
) -> dict[str, object] | None:
    """Read a single SQLite row as a dict, straight from the gateway's own file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


async def _create_account(
    client: AsyncClient,
    gateway_client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_id: str,
    initial_balance: int,
    operation_id: str | None,
) -> None:
    # bank_accounts.account_id FKs to identity_agents.agent_id in the real schema.
    await register_identity_agent(gateway_client, agent_id)
    payload: dict[str, object] = {
        "action": "create_account",
        "agent_id": agent_id,
        "initial_balance": initial_balance,
    }
    if operation_id is not None:
        payload["operation_id"] = operation_id
    token = make_jws_token(platform_keypair[0], PLATFORM_AGENT_ID, payload)
    response = await client.post("/accounts", json={"token": token})
    assert response.status_code == 201, response.text
    body = response.json()
    if operation_id is None:
        assert body["account_id"] == agent_id
        assert body["balance"] == 0
    else:
        receipt = body["receipt"]
        assert body["outcome"] == "native_receipt"
        assert body["current_escrow"] is None
        assert receipt["operation_id"] == operation_id
        assert receipt["operation_kind"] == "account_issuance"
        assert receipt["result"] == {
            "account_id": agent_id,
            "issued_amount": initial_balance,
            "balance_after": initial_balance,
        }


class TestAccountLifecycleGatewayBacked:
    async def test_create_account_persists_row_via_real_gateway(
        self,
        client: AsyncClient,
        gateway_client: AsyncClient,
        platform_keypair: tuple[Ed25519PrivateKey, str],
        gateway_db_path: str,
    ) -> None:
        await _create_account(
            client,
            gateway_client,
            platform_keypair,
            "a-payer",
            100,
            "op-00000000-0000-4000-8000-000000000301",
        )

        row = _read_one(
            gateway_db_path,
            "SELECT account_id, balance FROM bank_accounts WHERE account_id = ?",
            ("a-payer",),
        )
        assert row is not None
        assert row["balance"] == 100

    async def test_credit_account_updates_balance_via_real_gateway(
        self,
        client: AsyncClient,
        gateway_client: AsyncClient,
        platform_keypair: tuple[Ed25519PrivateKey, str],
        agent_keypair: tuple[Ed25519PrivateKey, str],
        gateway_db_path: str,
    ) -> None:
        await _create_account(client, gateway_client, platform_keypair, "a-payer", 0, None)

        credit_operation_id = "op-00000000-0000-4000-8000-000000000302"
        token = make_jws_token(
            platform_keypair[0],
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "operation_id": credit_operation_id,
                "account_id": "a-payer",
                "amount": 50,
                "reference": "seed",
            },
        )
        response = await client.post("/accounts/a-payer/credit", json={"token": token})
        assert response.status_code == 200, response.text
        receipt = response.json()["receipt"]
        assert receipt["operation_id"] == credit_operation_id
        assert receipt["operation_kind"] == "credit"
        assert receipt["result"] == {
            "account_id": "a-payer",
            "amount": 50,
            "balance_after": 50,
            "reference": "seed",
        }
        assert receipt["legs"][0]["amount"] == 50

        row = _read_one(
            gateway_db_path,
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            ("a-payer",),
        )
        assert row is not None
        assert row["balance"] == 50

        bearer_token = make_jws_token(
            agent_keypair[0], "a-payer", {"action": "get_balance", "account_id": "a-payer"}
        )
        balance_resp = await client.get(
            "/accounts/a-payer", headers={"authorization": f"Bearer {bearer_token}"}
        )
        assert balance_resp.status_code == 200
        assert balance_resp.json()["balance"] == 50


class TestEscrowLifecycleGatewayBacked:
    async def test_escrow_lock_and_release_moves_real_gateway_balances(
        self,
        client: AsyncClient,
        gateway_client: AsyncClient,
        platform_keypair: tuple[Ed25519PrivateKey, str],
        agent_keypair: tuple[Ed25519PrivateKey, str],
        gateway_db_path: str,
    ) -> None:
        """Escrow lock (agent-signed) then release (platform-signed) round-trips
        through the real gateway-backed ledger, moving real SQLite balances."""
        await _create_account(
            client,
            gateway_client,
            platform_keypair,
            "a-poster",
            100,
            "op-00000000-0000-4000-8000-000000000303",
        )
        await _create_account(client, gateway_client, platform_keypair, "a-worker", 0, None)

        lock_operation_id = "op-00000000-0000-4000-8000-000000000304"
        lock_token = make_jws_token(
            agent_keypair[0],
            "a-poster",
            {
                "action": "escrow_lock",
                "operation_id": lock_operation_id,
                "agent_id": "a-poster",
                "amount": 40,
                "task_id": "t-integration-1",
            },
        )
        lock_resp = await client.post("/escrow/lock", json={"token": lock_token})
        assert lock_resp.status_code == 201, lock_resp.text
        lock_body = lock_resp.json()
        lock_receipt = lock_body["receipt"]
        assert lock_body["outcome"] == "native_receipt"
        assert lock_receipt["operation_id"] == lock_operation_id
        assert lock_receipt["result"] == {"escrow_status_at_commit": "locked"}
        assert lock_body["current_escrow"]["status"] == "locked"
        escrow_id = lock_receipt["escrow_id"]

        poster_row = _read_one(
            gateway_db_path,
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            ("a-poster",),
        )
        assert poster_row is not None
        assert poster_row["balance"] == 60

        release_operation_id = "op-00000000-0000-4000-8000-000000000305"
        source_reference = "task:t-integration-1:approved"
        release_token = make_jws_token(
            platform_keypair[0],
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "operation_id": release_operation_id,
                "source_reference": source_reference,
                "escrow_id": escrow_id,
                "recipient_account_id": "a-worker",
            },
        )
        release_resp = await client.post(
            f"/escrow/{escrow_id}/release", json={"token": release_token}
        )
        assert release_resp.status_code == 200, release_resp.text
        release_body = release_resp.json()
        release_receipt = release_body["receipt"]
        assert release_body["outcome"] == "native_receipt"
        assert release_receipt["operation_id"] == release_operation_id
        assert release_receipt["source_reference"] == source_reference
        assert release_receipt["result"] == {"escrow_status_at_commit": "released"}
        assert release_body["current_escrow"]["status"] == "released"

        worker_row = _read_one(
            gateway_db_path,
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            ("a-worker",),
        )
        assert worker_row is not None
        assert worker_row["balance"] == 40

        tx_row = _read_one(
            gateway_db_path,
            "SELECT type, amount FROM bank_transactions "
            "WHERE account_id = ? AND reference = ? AND type = 'escrow_release'",
            ("a-worker", str(escrow_id)),
        )
        assert tx_row is not None
        assert tx_row["amount"] == 40
