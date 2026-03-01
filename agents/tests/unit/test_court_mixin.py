"""Unit tests for CourtMixin methods."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest

from base_agent.agent import BaseAgent

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


@pytest.mark.unit
class TestFileClaim:
    """Tests for file_claim."""

    async def test_file_claim_success(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-claimant"
        claim_response = {
            "dispute_id": "disp-1",
            "task_id": "t-1",
            "status": "filed",
        }
        agent._sign_jws = Mock(return_value="claim-jws")
        agent._request = AsyncMock(return_value=claim_response)

        result = await agent.file_claim("t-1", reason="Incomplete delivery")

        assert result == claim_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "file_dispute",
                "task_id": "t-1",
                "claimant_id": "a-claimant",
                "claim": "Incomplete delivery",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.court_url}/disputes/file",
            json={"token": "claim-jws"},
        )
        await agent.close()
