"""Unit tests for base_agent package exports."""

from __future__ import annotations

import pytest


@pytest.mark.unit
class TestPackageExports:
    """Tests for top-level imports."""

    def test_import_agent_factory(self) -> None:
        from base_agent import AgentFactory

        assert AgentFactory is not None

    def test_import_platform_agent(self) -> None:
        from base_agent import PlatformAgent

        assert PlatformAgent is not None

    def test_import_base_agent(self) -> None:
        from base_agent import BaseAgent

        assert BaseAgent is not None
