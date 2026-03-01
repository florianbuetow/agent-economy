"""Unit tests for BankMixin methods."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest

from base_agent.agent import BaseAgent

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


@pytest.mark.unit
class TestGetBalance:
    """Tests for get_balance."""

    async def test_get_balance_returns_account(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-123"
        balance_response = {
            "account_id": "a-123",
            "balance": 100,
            "created_at": "2026-01-01T00:00:00Z",
        }
        expected_headers = {"Authorization": "Bearer test-token"}
        agent._auth_header = Mock(return_value=expected_headers)
        agent._request = AsyncMock(return_value=balance_response)

        result = await agent.get_balance()

        assert result == balance_response
        agent._auth_header.assert_called_once_with({"action": "get_balance", "account_id": "a-123"})
        agent._request.assert_awaited_once_with(
            "GET",
            f"{sample_config.bank_url}/accounts/a-123",
            headers=expected_headers,
        )
        await agent.close()


@pytest.mark.unit
class TestGetTransactions:
    """Tests for get_transactions."""

    async def test_get_transactions_returns_list(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-123"
        transactions_response = {
            "transactions": [
                {
                    "tx_id": "tx-1",
                    "type": "credit",
                    "amount": 50,
                    "balance_after": 50,
                    "reference": "initial_balance",
                    "timestamp": "2026-01-01T00:00:00Z",
                }
            ]
        }
        expected_headers = {"Authorization": "Bearer test-token"}
        agent._auth_header = Mock(return_value=expected_headers)
        agent._request = AsyncMock(return_value=transactions_response)

        result = await agent.get_transactions()

        assert result == transactions_response["transactions"]
        agent._auth_header.assert_called_once_with(
            {"action": "get_transactions", "account_id": "a-123"}
        )
        agent._request.assert_awaited_once_with(
            "GET",
            f"{sample_config.bank_url}/accounts/a-123/transactions",
            headers=expected_headers,
        )
        await agent.close()


@pytest.mark.unit
class TestLockEscrow:
    """Tests for lock_escrow."""

    async def test_lock_escrow_success(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-123"
        escrow_response = {
            "escrow_id": "esc-1",
            "amount": 10,
            "task_id": "T-123",
            "status": "locked",
        }
        agent._sign_jws = Mock(return_value="test-jws-token")
        agent._request = AsyncMock(return_value=escrow_response)

        result = await agent.lock_escrow(amount=10, task_id="T-123")

        assert result == escrow_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "escrow_lock",
                "agent_id": "a-123",
                "amount": 10,
                "task_id": "T-123",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.bank_url}/escrow/lock",
            json={"token": "test-jws-token"},
        )
        await agent.close()
