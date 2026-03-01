"""Unit tests for IdentityMixin methods."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest

from base_agent.agent import BaseAgent

if TYPE_CHECKING:
    from base_agent.config import Settings


@pytest.mark.unit
class TestRegister:
    """Tests for agent registration behavior."""

    async def test_register_success(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        response_data = {
            "agent_id": "a-123",
            "name": "Test Bot",
            "public_key": f"ed25519:{agent.get_public_key_b64()}",
            "registered_at": "2026-02-20T10:30:00Z",
        }
        response = httpx.Response(
            status_code=201,
            json=response_data,
            request=httpx.Request(
                "POST", f"{sample_settings.platform.identity_url}/agents/register"
            ),
        )
        agent._request_raw = AsyncMock(return_value=response)

        result = await agent.register()

        assert result == response_data
        assert agent.agent_id == "a-123"
        agent._request_raw.assert_awaited_once_with(
            "POST",
            f"{sample_settings.platform.identity_url}/agents/register",
            json={
                "name": "Test Bot",
                "public_key": f"ed25519:{agent.get_public_key_b64()}",
            },
        )
        await agent.close()

    async def test_register_idempotent_409(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        conflict_response = httpx.Response(
            status_code=409,
            json={"error": "PUBLIC_KEY_EXISTS", "message": "Public key already registered"},
            request=httpx.Request(
                "POST", f"{sample_settings.platform.identity_url}/agents/register"
            ),
        )
        list_agents_response = {
            "agents": [
                {
                    "agent_id": "a-existing",
                    "name": "Test Bot",
                    "registered_at": "2026-02-20T10:30:00Z",
                },
            ],
        }
        get_agent_response = {
            "agent_id": "a-existing",
            "name": "Test Bot",
            "public_key": f"ed25519:{agent.get_public_key_b64()}",
            "registered_at": "2026-02-20T10:30:00Z",
        }

        agent._request_raw = AsyncMock(return_value=conflict_response)
        agent._request = AsyncMock(side_effect=[list_agents_response, get_agent_response])

        result = await agent.register()

        assert result == get_agent_response
        assert agent.agent_id == "a-existing"
        agent._request_raw.assert_awaited_once()
        assert agent._request.await_args_list == [
            (
                ("GET", f"{sample_settings.platform.identity_url}/agents"),
                {},
            ),
            (
                ("GET", f"{sample_settings.platform.identity_url}/agents/a-existing"),
                {},
            ),
        ]
        await agent.close()


@pytest.mark.unit
class TestGetAgentInfo:
    """Tests for get_agent_info."""

    async def test_get_agent_info_returns_record(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        agent_record = {
            "agent_id": "a-123",
            "name": "Test Bot",
            "public_key": "ed25519:abc",
            "registered_at": "2026-02-20T10:30:00Z",
        }
        agent._request = AsyncMock(return_value=agent_record)

        result = await agent.get_agent_info("a-123")

        assert result == agent_record
        agent._request.assert_awaited_once_with(
            "GET",
            f"{sample_settings.platform.identity_url}/agents/a-123",
        )
        await agent.close()


@pytest.mark.unit
class TestListAgents:
    """Tests for list_agents."""

    async def test_list_agents_returns_list(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        list_response = {
            "agents": [
                {
                    "agent_id": "a-123",
                    "name": "Test Bot",
                    "registered_at": "2026-02-20T10:30:00Z",
                },
            ],
        }
        agent._request = AsyncMock(return_value=list_response)

        result = await agent.list_agents()

        assert result == list_response["agents"]
        agent._request.assert_awaited_once_with(
            "GET",
            f"{sample_settings.platform.identity_url}/agents",
        )
        await agent.close()


@pytest.mark.unit
class TestVerifyJws:
    """Tests for verify_jws."""

    async def test_verify_jws_valid(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        verify_response = {"valid": True, "agent_id": "a-123"}
        agent._request = AsyncMock(return_value=verify_response)

        result = await agent.verify_jws("token-123")

        assert result == verify_response
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_settings.platform.identity_url}/agents/verify-jws",
            json={"token": "token-123"},
        )
        await agent.close()

    async def test_verify_jws_invalid(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        verify_response = {"valid": False, "reason": "signature mismatch"}
        agent._request = AsyncMock(return_value=verify_response)

        result = await agent.verify_jws("bad-token")

        assert result == verify_response
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_settings.platform.identity_url}/agents/verify-jws",
            json={"token": "bad-token"},
        )
        await agent.close()
