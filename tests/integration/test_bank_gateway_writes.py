"""Integration tests: Bank writes via DB Gateway to economy.db."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from tests.integration.conftest import read_db, read_one
from tests.integration.gateway_helpers import create_gateway_client

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _identity_payload(agent_id: str, name: str, timestamp: str) -> dict[str, object]:
    key_seed = agent_id.replace("a-", "").upper().ljust(43, "X")
    return {
        "agent_id": agent_id,
        "name": name,
        "public_key": f"ed25519:{key_seed}=",
        "registered_at": timestamp,
        "event": {
            "event_source": "identity",
            "event_type": "agent.registered",
            "timestamp": timestamp,
            "agent_id": agent_id,
            "summary": f"{name} registered",
            "payload": json.dumps({"agent_name": name}),
        },
    }


@pytest_asyncio.fixture
async def gw_client(initialized_db: str):
    client = create_gateway_client(initialized_db)
    async with client:
        response = await client.post(
            "/identity/agents",
            json=_identity_payload("a-alice", "Alice", "2026-03-02T06:22:00Z"),
        )
        assert response.status_code == 201
        response = await client.post(
            "/identity/agents",
            json=_identity_payload("a-bob", "Bob", "2026-03-02T06:22:01Z"),
        )
        assert response.status_code == 201
        yield client


class TestBankGatewayWrites:
    async def test_create_account_and_initial_credit_write_rows(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        payload = {
            "operation_id": "op-00000000-0000-4000-8000-000000000001",
            "account_id": "a-alice",
            "balance": 1000,
            "created_at": "2026-03-02T06:23:00Z",
            "initial_credit": {
                "tx_id": "tx-init-001",
                "amount": 1000,
                "reference": "initial_balance",
                "timestamp": "2026-03-02T06:23:00Z",
            },
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": "2026-03-02T06:23:00Z",
                "agent_id": "a-alice",
                "summary": "Account created for Alice",
                "payload": json.dumps({"agent_name": "Alice"}),
            },
        }

        response = await gw_client.post("/bank/accounts", json=payload)
        assert response.status_code == 201
        envelope = response.json()
        assert envelope["outcome"] == "native_receipt"
        assert envelope["receipt"]["operation_id"] == payload["operation_id"]
        assert envelope["receipt"]["operation_kind"] == "account_issuance"
        assert envelope["receipt"]["source_reference"] == "initial_balance"
        assert envelope["receipt"]["result"] == {
            "account_id": "a-alice",
            "issued_amount": 1000,
            "balance_after": 1000,
        }
        assert envelope["receipt"]["legs"] == [
            {
                "role": "account",
                "account_id": "a-alice",
                "direction": "credit",
                "amount": 1000,
                "transaction_id": "tx-init-001",
                "balance_after": 1000,
            }
        ]
        assert envelope["current_escrow"] is None

        account = read_one(
            initialized_db,
            "SELECT account_id, balance, created_at FROM bank_accounts WHERE account_id = ?",
            ("a-alice",),
        )
        assert account is not None
        assert account["balance"] == 1000
        assert account["created_at"] == "2026-03-02T06:23:00Z"

        initial_credit = read_one(
            initialized_db,
            "SELECT tx_id, type, amount, balance_after, reference "
            "FROM bank_transactions WHERE tx_id = ?",
            ("tx-init-001",),
        )
        assert initial_credit is not None
        assert initial_credit["type"] == "credit"
        assert initial_credit["amount"] == 1000
        assert initial_credit["balance_after"] == 1000
        assert initial_credit["reference"] == "initial_balance"

    async def test_credit_updates_balance_and_logs_transaction(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        create_response = await gw_client.post(
            "/bank/accounts",
            json={
                "account_id": "a-alice",
                "balance": 0,
                "created_at": "2026-03-02T06:24:00Z",
                "event": {
                    "event_source": "bank",
                    "event_type": "account.created",
                    "timestamp": "2026-03-02T06:24:00Z",
                    "agent_id": "a-alice",
                    "summary": "Account created",
                    "payload": "{}",
                },
            },
        )
        assert create_response.status_code == 201

        credit_response = await gw_client.post(
            "/bank/credit",
            json={
                "operation_id": "op-00000000-0000-4000-8000-000000000002",
                "tx_id": "tx-credit-001",
                "account_id": "a-alice",
                "amount": 500,
                "reference": "salary_round_1",
                "timestamp": "2026-03-02T06:25:00Z",
                "event": {
                    "event_source": "bank",
                    "event_type": "salary.paid",
                    "timestamp": "2026-03-02T06:25:00Z",
                    "agent_id": "a-alice",
                    "summary": "Credited 500 to Alice",
                    "payload": json.dumps({"amount": 500}),
                },
            },
        )
        assert credit_response.status_code == 200
        credit_envelope = credit_response.json()
        assert credit_envelope["outcome"] == "native_receipt"
        assert (
            credit_envelope["receipt"]["operation_id"] == "op-00000000-0000-4000-8000-000000000002"
        )
        assert credit_envelope["receipt"]["operation_kind"] == "credit"
        assert credit_envelope["receipt"]["source_reference"] == "salary_round_1"
        assert credit_envelope["receipt"]["result"] == {
            "account_id": "a-alice",
            "amount": 500,
            "balance_after": 500,
            "reference": "salary_round_1",
        }
        assert credit_envelope["receipt"]["legs"][0]["transaction_id"] == "tx-credit-001"
        assert credit_envelope["current_escrow"] is None

        tx = read_one(
            initialized_db,
            "SELECT tx_id, type, amount, balance_after, reference "
            "FROM bank_transactions WHERE tx_id = ?",
            ("tx-credit-001",),
        )
        assert tx is not None
        assert tx["type"] == "credit"
        assert tx["amount"] == 500
        assert tx["balance_after"] == 500

        account = read_one(
            initialized_db,
            "SELECT balance FROM bank_accounts WHERE account_id = ?",
            ("a-alice",),
        )
        assert account is not None
        assert account["balance"] == 500

    async def test_escrow_lock_release_and_split_write_rows(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        for account_id, balance, tx_id in [
            ("a-alice", 1000, "tx-init-alice"),
            ("a-bob", 0, None),
        ]:
            payload: dict[str, object] = {
                "account_id": account_id,
                "balance": balance,
                "created_at": "2026-03-02T06:26:00Z",
                "event": {
                    "event_source": "bank",
                    "event_type": "account.created",
                    "timestamp": "2026-03-02T06:26:00Z",
                    "agent_id": account_id,
                    "summary": f"Account created for {account_id}",
                    "payload": "{}",
                },
            }
            if tx_id is not None:
                payload["operation_id"] = "op-00000000-0000-4000-8000-000000000003"
                payload["initial_credit"] = {
                    "tx_id": tx_id,
                    "amount": balance,
                    "reference": "initial_balance",
                    "timestamp": "2026-03-02T06:26:00Z",
                }
            response = await gw_client.post("/bank/accounts", json=payload)
            assert response.status_code == 201

        lock_response = await gw_client.post(
            "/bank/escrow/lock",
            json={
                "operation_id": "op-00000000-0000-4000-8000-000000000004",
                "escrow_id": "esc-001",
                "payer_account_id": "a-alice",
                "amount": 200,
                "task_id": "t-task-001",
                "created_at": "2026-03-02T06:27:00Z",
                "tx_id": "tx-escrow-lock-001",
                "event": {
                    "event_source": "bank",
                    "event_type": "escrow.locked",
                    "timestamp": "2026-03-02T06:27:00Z",
                    "agent_id": "a-alice",
                    "task_id": "t-task-001",
                    "summary": "Escrow locked",
                    "payload": json.dumps({"escrow_id": "esc-001", "amount": 200}),
                },
            },
        )
        assert lock_response.status_code == 201
        lock_envelope = lock_response.json()
        assert lock_envelope["outcome"] == "native_receipt"
        assert lock_envelope["receipt"]["operation_kind"] == "escrow_lock"
        assert lock_envelope["receipt"]["source_reference"] == "t-task-001"
        assert lock_envelope["receipt"]["result"] == {"escrow_status_at_commit": "locked"}
        assert lock_envelope["receipt"]["legs"][0]["amount"] == 200
        assert lock_envelope["current_escrow"]["status"] == "locked"

        release_response = await gw_client.post(
            "/bank/escrow/release",
            json={
                "operation_id": "op-00000000-0000-4000-8000-000000000005",
                "escrow_id": "esc-001",
                "recipient_account_id": "a-bob",
                "source_reference": "task:t-task-001:approved",
                "tx_id": "tx-escrow-release-001",
                "resolved_at": "2026-03-02T06:28:00Z",
                "event": {
                    "event_source": "bank",
                    "event_type": "escrow.released",
                    "timestamp": "2026-03-02T06:28:00Z",
                    "summary": "Escrow released",
                    "payload": json.dumps({"escrow_id": "esc-001"}),
                },
            },
        )
        assert release_response.status_code == 200
        release_envelope = release_response.json()
        assert release_envelope["outcome"] == "native_receipt"
        assert release_envelope["receipt"]["operation_kind"] == "escrow_release"
        assert release_envelope["receipt"]["source_reference"] == "task:t-task-001:approved"
        assert release_envelope["receipt"]["result"] == {"escrow_status_at_commit": "released"}
        assert release_envelope["receipt"]["legs"][0]["amount"] == 200
        assert release_envelope["current_escrow"]["status"] == "released"

        release_escrow = read_one(
            initialized_db,
            "SELECT status, resolved_at FROM bank_escrow WHERE escrow_id = ?",
            ("esc-001",),
        )
        assert release_escrow is not None
        assert release_escrow["status"] == "released"
        assert release_escrow["resolved_at"] == "2026-03-02T06:28:00Z"

        # New escrow to verify split behavior.
        lock_response_2 = await gw_client.post(
            "/bank/escrow/lock",
            json={
                "operation_id": "op-00000000-0000-4000-8000-000000000006",
                "escrow_id": "esc-002",
                "payer_account_id": "a-alice",
                "amount": 100,
                "task_id": "t-task-002",
                "created_at": "2026-03-02T06:29:00Z",
                "tx_id": "tx-escrow-lock-002",
                "event": {
                    "event_source": "bank",
                    "event_type": "escrow.locked",
                    "timestamp": "2026-03-02T06:29:00Z",
                    "agent_id": "a-alice",
                    "task_id": "t-task-002",
                    "summary": "Escrow locked",
                    "payload": json.dumps({"escrow_id": "esc-002", "amount": 100}),
                },
            },
        )
        assert lock_response_2.status_code == 201

        split_response = await gw_client.post(
            "/bank/escrow/split",
            json={
                "operation_id": "op-00000000-0000-4000-8000-000000000007",
                "escrow_id": "esc-002",
                "worker_account_id": "a-bob",
                "poster_account_id": "a-alice",
                "worker_pct": 70,
                "source_reference": "task:t-task-002:ruling:ruling-002",
                "worker_tx_id": "tx-split-worker-002",
                "poster_tx_id": "tx-split-poster-002",
                "resolved_at": "2026-03-02T06:30:00Z",
                "event": {
                    "event_source": "bank",
                    "event_type": "escrow.split",
                    "timestamp": "2026-03-02T06:30:00Z",
                    "summary": "Escrow split",
                    "payload": json.dumps(
                        {"escrow_id": "esc-002", "worker_amount": 70, "poster_amount": 30}
                    ),
                },
            },
        )
        assert split_response.status_code == 200
        split_envelope = split_response.json()
        assert split_envelope["outcome"] == "native_receipt"
        assert split_envelope["receipt"]["operation_kind"] == "escrow_split"
        assert split_envelope["receipt"]["source_reference"] == "task:t-task-002:ruling:ruling-002"
        assert split_envelope["receipt"]["result"] == {
            "escrow_status_at_commit": "split",
            "worker_pct": 70,
        }
        assert [leg["amount"] for leg in split_envelope["receipt"]["legs"]] == [70, 30]
        assert split_envelope["current_escrow"]["status"] == "split"

        split_escrow = read_one(
            initialized_db,
            "SELECT status FROM bank_escrow WHERE escrow_id = ?",
            ("esc-002",),
        )
        assert split_escrow is not None
        assert split_escrow["status"] == "split"

        split_transactions = read_db(
            initialized_db,
            "SELECT tx_id, account_id, type, amount FROM bank_transactions "
            "WHERE tx_id IN (?, ?) ORDER BY tx_id",
            ("tx-split-poster-002", "tx-split-worker-002"),
        )
        assert len(split_transactions) == 2
        assert split_transactions[0]["type"] == "escrow_release"
        assert split_transactions[1]["type"] == "escrow_release"
