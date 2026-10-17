"""Unit tests for PlatformAgent privileged operations."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.config import AgentConfig
from base_agent.platform import PlatformAgent
from base_agent.signing import TokenPolicy, create_jws


@pytest.fixture()
def platform_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return AgentConfig(
        name="Platform",
        private_key=private_key,
        public_key=public_key,
        identity_url="http://localhost:8001",
        bank_url="http://localhost:8002",
        task_board_url="http://localhost:8003",
        reputation_url="http://localhost:8004",
        court_url="http://localhost:8005",
        token_ttl_seconds=300,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    )


@pytest.mark.unit
class TestPlatformAgentInit:
    """Tests for PlatformAgent construction."""

    def test_creates_platform_agent(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        assert agent.name == "Platform"
        assert agent.agent_id is None

    def test_repr(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        assert "PlatformAgent" in repr(agent)


@pytest.mark.unit
class TestCreateAccount:
    """Tests for create_account."""

    async def test_create_account_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = {"account_id": "a-alice", "balance": 100, "created_at": "2026-01-01T00:00:00Z"}
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.create_account(agent_id="a-alice", initial_balance=100)

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {"action": "create_account", "agent_id": "a-alice", "initial_balance": 100},
            audience="central-bank",
            resource="POST /accounts",
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/accounts",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestCreditAccount:
    """Tests for credit_account."""

    async def test_credit_account_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = {"tx_id": "tx-1", "balance_after": 200}
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.credit_account(account_id="a-alice", amount=100, reference="salary")

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "credit",
                "account_id": "a-alice",
                "amount": 100,
                "reference": "salary",
            },
            audience="central-bank",
            resource="POST /accounts/a-alice/credit",
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/accounts/a-alice/credit",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestReleaseEscrow:
    """Tests for release_escrow."""

    async def test_release_escrow_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = {"escrow_id": "esc-1", "amount": 50, "status": "released"}
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.release_escrow(escrow_id="esc-1", recipient_account_id="a-alice")

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "escrow_release",
                "escrow_id": "esc-1",
                "recipient_account_id": "a-alice",
            },
            audience="central-bank",
            resource="POST /escrow/esc-1/release",
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/escrow/esc-1/release",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestSplitEscrow:
    """Tests for split_escrow."""

    async def test_split_escrow_success(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"
        response = {"escrow_id": "esc-1", "worker_amount": 70, "poster_amount": 30}
        agent._sign_jws = Mock(return_value="test-jws")
        agent._request = AsyncMock(return_value=response)

        result = await agent.split_escrow(
            escrow_id="esc-1",
            worker_account_id="a-alice",
            poster_account_id="a-bob",
            worker_pct=70,
        )

        assert result == response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "escrow_split",
                "escrow_id": "esc-1",
                "worker_account_id": "a-alice",
                "poster_account_id": "a-bob",
                "worker_pct": 70,
            },
            audience="central-bank",
            resource="POST /escrow/esc-1/split",
        )
        agent._request.assert_awaited_once_with(
            "POST",
            "http://localhost:8002/escrow/esc-1/split",
            json={"token": "test-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestVerifyPlatformJws:
    """Tests for verify_platform_jws."""

    def test_verify_valid_platform_token(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"

        token = agent._sign_jws(
            {"action": "create_account", "agent_id": "a-alice"},
            audience="central-bank",
            resource="POST /accounts",
        )
        result = agent.verify_platform_jws(token)

        assert result.payload["action"] == "create_account"
        assert result.payload["agent_id"] == "a-alice"

    def test_verify_rejects_non_platform_token(self, platform_config: AgentConfig) -> None:
        agent = PlatformAgent(config=platform_config)
        agent.agent_id = "a-platform"

        other_key = Ed25519PrivateKey.generate()
        token = create_jws(
            {"action": "create_account"},
            other_key,
            kid="a-imposter",
            audience="central-bank",
            resource="POST /accounts",
            token_ttl_seconds=300,
        )

        with pytest.raises(InvalidSignature):
            agent.verify_platform_jws(token)
