"""Unit tests for BaseAgent initialization and internals."""

from __future__ import annotations

import pytest

from base_agent.agent import BaseAgent
from base_agent.config import Settings


@pytest.mark.unit
class TestBaseAgentInit:
    """Tests for BaseAgent construction."""

    def test_creates_agent(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        assert agent.handle == "testbot"
        assert agent.name == "Test Bot"
        assert agent.agent_type == "worker"
        assert agent.agent_id is None

    def test_generates_keys_if_missing(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        keys_dir = Path(sample_settings.data.keys_dir)
        assert (keys_dir / "testbot.key").exists()
        assert (keys_dir / "testbot.pub").exists()

    def test_loads_existing_keys(self, sample_settings: Settings) -> None:
        agent1 = BaseAgent(handle="testbot", config=sample_settings)
        pub1 = agent1.get_public_key_b64()
        agent2 = BaseAgent(handle="testbot", config=sample_settings)
        pub2 = agent2.get_public_key_b64()
        assert pub1 == pub2

    def test_unknown_handle_raises(self, sample_settings: Settings) -> None:
        with pytest.raises(KeyError):
            BaseAgent(handle="nonexistent", config=sample_settings)

    def test_repr(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        assert "testbot" in repr(agent)
        assert "Test Bot" in repr(agent)


@pytest.mark.unit
class TestBaseAgentSigning:
    """Tests for JWS signing internals."""

    def test_sign_jws_produces_token(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        token = agent._sign_jws({"action": "test"})
        assert token.count(".") == 2

    def test_auth_header_format(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        header = agent._auth_header({"action": "test"})
        assert "Authorization" in header
        assert header["Authorization"].startswith("Bearer ")

    def test_public_key_b64_is_stable(self, sample_settings: Settings) -> None:
        agent = BaseAgent(handle="testbot", config=sample_settings)
        assert agent.get_public_key_b64() == agent.get_public_key_b64()
