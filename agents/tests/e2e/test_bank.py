from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from service_auth.financial_identity import operation_id_for

from base_agent.agent import BaseAgent

if TYPE_CHECKING:
    from base_agent.config import AgentConfig
    from base_agent.platform import PlatformAgent


@pytest.mark.e2e
async def test_create_account_and_check_balance(
    agent_config: AgentConfig, platform_agent: PlatformAgent
) -> None:
    agent = BaseAgent(config=agent_config)
    try:
        await agent.register()
        assert agent.agent_id is not None

        await platform_agent.create_account(
            agent_id=agent.agent_id,
            initial_balance=100,
            operation_id=operation_id_for("e2e-account-issuance", (agent.agent_id,)),
        )
        balance = await agent.get_balance()

        assert balance["balance"] == 100
    finally:
        await agent.close()


@pytest.mark.e2e
async def test_credit_account_and_view_transactions(
    agent_config: AgentConfig, platform_agent: PlatformAgent
) -> None:
    agent = BaseAgent(config=agent_config)
    try:
        await agent.register()
        assert agent.agent_id is not None

        await platform_agent.create_account(
            agent_id=agent.agent_id,
            initial_balance=0,
            operation_id=None,
        )
        await platform_agent.credit_account(
            account_id=agent.agent_id,
            amount=500,
            reference="salary",
            operation_id=operation_id_for("e2e-credit", (agent.agent_id, "salary")),
        )

        balance = await agent.get_balance()
        assert balance["balance"] == 500

        transactions = await agent.get_transactions()
        assert any(tx.get("amount") == 500 for tx in transactions)
    finally:
        await agent.close()


@pytest.mark.e2e
async def test_escrow_lock_insufficient_funds(
    agent_config: AgentConfig, platform_agent: PlatformAgent
) -> None:
    agent = BaseAgent(config=agent_config)
    try:
        await agent.register()
        assert agent.agent_id is not None

        await platform_agent.create_account(
            agent_id=agent.agent_id,
            initial_balance=50,
            operation_id=operation_id_for("e2e-account-issuance", (agent.agent_id,)),
        )

        token = agent._sign_jws(
            {
                "action": "escrow_lock",
                "operation_id": operation_id_for("task-lock", ("t-insufficient", agent.agent_id)),
                "agent_id": agent.agent_id,
                "amount": 100,
                "task_id": "t-insufficient",
            },
            audience="central-bank",
            resource="POST /escrow/lock",
        )
        response = await agent._request_raw(
            "POST",
            f"{agent.config.bank_url}/escrow/lock",
            json={"token": token},
        )

        assert response.status_code == 402
        assert response.json()["error"] == "insufficient_funds"
    finally:
        await agent.close()
