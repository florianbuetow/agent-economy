"""Acceptance tests for current_streak field in AgentStats.

Ticket: agent-economy-hdk
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
class TestAgentStreak:
    """GET /api/agents response should include current_streak field."""

    async def test_agent_list_has_current_streak(self, client: httpx.AsyncClient) -> None:
        """Each agent in the list must have a current_streak integer field."""
        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        for agent in data["agents"]:
            assert "current_streak" in agent["stats"], (
                f"Agent {agent['agent_id']} missing current_streak in stats"
            )
            assert isinstance(agent["stats"]["current_streak"], int)

    async def test_agent_profile_has_current_streak(self, client: httpx.AsyncClient) -> None:
        """Agent profile must include current_streak in stats."""
        list_resp = await client.get("/api/agents?limit=1")
        agents = list_resp.json()["agents"]
        assert agents, "Seed data must include at least one agent"
        agent_id = agents[0]["agent_id"]
        resp = await client.get(f"/api/agents/{agent_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_streak" in data["stats"]
        assert isinstance(data["stats"]["current_streak"], int)

    async def test_streak_is_non_negative(self, client: httpx.AsyncClient) -> None:
        """Streak must be >= 0."""
        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        for agent in resp.json()["agents"]:
            assert agent["stats"]["current_streak"] >= 0
