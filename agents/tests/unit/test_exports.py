"""Unit tests for base_agent package exports."""

from __future__ import annotations

import pytest

from base_agent import AgentFactory, BaseAgent, PlatformAgent


@pytest.mark.unit
class TestPackageExports:
    """Tests for top-level imports."""

    def test_import_agent_factory(self) -> None:
        assert AgentFactory is not None

    def test_import_platform_agent(self) -> None:
        assert PlatformAgent is not None

    def test_import_base_agent(self) -> None:
        assert BaseAgent is not None
