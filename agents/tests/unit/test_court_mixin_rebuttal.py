"""Regression tests for Court dispute and rebuttal client helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest

from base_agent.agent import BaseAgent

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


@pytest.mark.unit
class TestCourtMixinRebuttals:
    async def test_file_claim_uses_full_court_payload(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-platform"
        claim_response = {
            "dispute_id": "disp-1",
            "task_id": "t-1",
            "status": "rebuttal_pending",
        }
        agent._sign_jws = Mock(return_value="claim-jws")
        agent._request = AsyncMock(return_value=claim_response)

        result = await agent.file_claim(
            task_id="t-1",
            claimant_id="a-poster",
            respondent_id="a-worker",
            claim="Incomplete delivery",
            escrow_id="esc-1",
        )

        assert result == claim_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "file_dispute",
                "task_id": "t-1",
                "claimant_id": "a-poster",
                "respondent_id": "a-worker",
                "claim": "Incomplete delivery",
                "escrow_id": "esc-1",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.court_url}/disputes/file",
            json={"token": "claim-jws"},
        )
        await agent.close()

    async def test_submit_rebuttal_uses_court_rebuttal_endpoint(
        self,
        sample_config: AgentConfig,
    ) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-platform"
        rebuttal_response = {
            "dispute_id": "disp-1",
            "status": "rebuttal_pending",
            "rebuttal": "The work meets the spec.",
        }
        agent._sign_jws = Mock(return_value="rebuttal-jws")
        agent._request = AsyncMock(return_value=rebuttal_response)

        result = await agent.submit_rebuttal("disp-1", "The work meets the spec.")

        assert result == rebuttal_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "submit_rebuttal",
                "dispute_id": "disp-1",
                "rebuttal": "The work meets the spec.",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.court_url}/disputes/disp-1/rebuttal",
            json={"token": "rebuttal-jws"},
        )
        await agent.close()

    async def test_list_disputes_filters_by_task_id(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        disputes_response = {"disputes": [{"dispute_id": "disp-1", "task_id": "t-1"}]}
        agent._request = AsyncMock(return_value=disputes_response)

        result = await agent.list_disputes(task_id="t-1")

        assert result == disputes_response["disputes"]
        agent._request.assert_awaited_once_with(
            "GET",
            f"{sample_config.court_url}/disputes",
            params={"task_id": "t-1"},
        )
        await agent.close()

    async def test_trigger_ruling_uses_court_ruling_endpoint(
        self,
        sample_config: AgentConfig,
    ) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-platform"
        ruling_response = {"dispute_id": "disp-1", "status": "ruled"}
        agent._sign_jws = Mock(return_value="ruling-jws")
        agent._request = AsyncMock(return_value=ruling_response)

        result = await agent.trigger_ruling("disp-1")

        assert result == ruling_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "trigger_ruling",
                "dispute_id": "disp-1",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.court_url}/disputes/disp-1/rule",
            json={"token": "ruling-jws"},
        )
        await agent.close()
