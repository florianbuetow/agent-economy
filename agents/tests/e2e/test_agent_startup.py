"""Tests for agent startup flow with self-service account creation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig

if TYPE_CHECKING:
    from base_agent.platform import PlatformAgent

IDENTITY_URL = "http://localhost:8001"
BANK_URL = "http://localhost:8002"
TASK_BOARD_URL = "http://localhost:8003"
REPUTATION_URL = "http://localhost:8004"
COURT_URL = "http://localhost:8005"


def _make_agent_config(name: str) -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name=name,
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url=IDENTITY_URL,
        bank_url=BANK_URL,
        task_board_url=TASK_BOARD_URL,
        reputation_url=REPUTATION_URL,
        court_url=COURT_URL,
    )


@pytest.mark.e2e
async def test_registered_agent_can_create_own_account() -> None:
    """A registered agent can self-provision a zero-balance bank account."""
    config = _make_agent_config("Self-Service Account Agent")
    agent = BaseAgent(config=config)

    try:
        await agent.register()
        assert agent.agent_id is not None

        await agent.create_account()

        balance = await agent.get_balance()
        assert balance["balance"] == 0
    finally:
        await agent.close()


@pytest.mark.e2e
async def test_registered_agent_can_post_task_after_account_creation(
    platform_agent: PlatformAgent,
) -> None:
    """A funded, self-provisioned agent can post tasks successfully."""
    config = _make_agent_config("Posting Agent")
    agent = BaseAgent(config=config)

    try:
        await agent.register()
        assert agent.agent_id is not None

        await agent.create_account()
        await platform_agent.credit_account(
            account_id=agent.agent_id,
            amount=1000,
            reference="startup_funding",
        )

        task = await agent.post_task(
            title="Startup flow task",
            spec="Verify startup flow creates bank account",
            reward=100,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        assert task["status"] == "open"
    finally:
        await agent.close()


@pytest.mark.e2e
async def test_create_account_is_idempotent() -> None:
    """Creating an account twice returns 409 and leaves the account usable."""
    config = _make_agent_config("Idempotent Account Agent")
    agent = BaseAgent(config=config)

    try:
        await agent.register()
        assert agent.agent_id is not None

        await agent.create_account()

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await agent.create_account()

        assert exc_info.value.response.status_code == 409

        balance = await agent.get_balance()
        assert balance["balance"] == 0
    finally:
        await agent.close()


@pytest.mark.e2e
async def test_unregistered_agent_cannot_create_account() -> None:
    """An unregistered agent cannot create a bank account."""
    config = _make_agent_config("Unregistered Account Agent")
    agent = BaseAgent(config=config)

    try:
        agent.agent_id = "a-fake-unregistered"

        with pytest.raises(httpx.HTTPStatusError):
            await agent.create_account()
    finally:
        await agent.close()
