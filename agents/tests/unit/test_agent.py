"""Unit tests for BaseAgent initialization and internals."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from base_agent.agent import BaseAgent

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


@pytest.mark.unit
class TestBaseAgentInit:
    """Tests for BaseAgent construction."""

    def test_creates_agent(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        assert agent.name == "Test Bot"
        assert agent.agent_id is None

    def test_repr(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        assert "Test Bot" in repr(agent)


@pytest.mark.unit
class TestBaseAgentSigning:
    """Tests for JWS signing internals."""

    def test_sign_jws_produces_token(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        token = agent._sign_jws({"action": "test"})
        assert token.count(".") == 2

    def test_auth_header_format(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        header = agent._auth_header({"action": "test"})
        assert "Authorization" in header
        assert header["Authorization"].startswith("Bearer ")

    def test_public_key_b64_is_stable(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        assert agent.get_public_key_b64() == agent.get_public_key_b64()
