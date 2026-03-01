"""Unit tests for AgentFactory."""

from __future__ import annotations

from pathlib import Path

import pytest

from base_agent.agent import BaseAgent
from base_agent.factory import AgentFactory
from base_agent.platform import PlatformAgent


@pytest.fixture()
def config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config.yaml"


@pytest.mark.unit
class TestAgentFactory:
    """Tests for AgentFactory."""

    def test_create_agent_returns_base_agent(self, config_path: Path, tmp_path: Path) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        agent = factory.create_agent("alice")
        assert isinstance(agent, BaseAgent)
        assert agent.name == "Alice"

    def test_create_agent_not_platform_agent(self, config_path: Path, tmp_path: Path) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        agent = factory.create_agent("alice")
        assert not isinstance(agent, PlatformAgent)

    def test_platform_agent_returns_platform_agent(
        self, config_path: Path, tmp_path: Path
    ) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        agent = factory.platform_agent()
        assert isinstance(agent, PlatformAgent)
        assert agent.name == "Platform"

    def test_platform_agent_has_privileged_methods(
        self, config_path: Path, tmp_path: Path
    ) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        agent = factory.platform_agent()
        assert hasattr(agent, "create_account")
        assert hasattr(agent, "credit_account")
        assert hasattr(agent, "release_escrow")
        assert hasattr(agent, "split_escrow")
        assert hasattr(agent, "verify_platform_jws")

    def test_unknown_handle_raises(self, config_path: Path, tmp_path: Path) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        with pytest.raises(KeyError):
            factory.create_agent("nonexistent")

    def test_same_handle_same_keys(self, config_path: Path, tmp_path: Path) -> None:
        factory = AgentFactory(config_path=config_path, keys_dir=tmp_path)
        a1 = factory.create_agent("alice")
        a2 = factory.create_agent("alice")
        assert a1.get_public_key_b64() == a2.get_public_key_b64()
