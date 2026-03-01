"""Unit tests for ReputationMixin methods."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest

from base_agent.agent import BaseAgent

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


@pytest.mark.unit
class TestSubmitFeedback:
    """Tests for submit_feedback."""

    async def test_submit_feedback_with_comment(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-from"
        feedback_response = {
            "feedback_id": "fb-1",
            "task_id": "t-1",
            "from_agent_id": "a-from",
            "to_agent_id": "a-to",
            "category": "spec_quality",
            "rating": "satisfied",
            "comment": "Good spec",
        }
        agent._sign_jws = Mock(return_value="feedback-jws")
        agent._request = AsyncMock(return_value=feedback_response)

        result = await agent.submit_feedback(
            task_id="t-1",
            to_agent_id="a-to",
            category="spec_quality",
            rating="satisfied",
            comment="Good spec",
        )

        assert result == feedback_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "submit_feedback",
                "from_agent_id": "a-from",
                "to_agent_id": "a-to",
                "task_id": "t-1",
                "category": "spec_quality",
                "rating": "satisfied",
                "comment": "Good spec",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.reputation_url}/feedback",
            json={"token": "feedback-jws"},
        )
        await agent.close()

    async def test_submit_feedback_without_comment(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-from"
        feedback_response = {"feedback_id": "fb-2"}
        agent._sign_jws = Mock(return_value="feedback-jws")
        agent._request = AsyncMock(return_value=feedback_response)

        result = await agent.submit_feedback(
            task_id="t-1",
            to_agent_id="a-to",
            category="delivery_quality",
            rating="dissatisfied",
        )

        assert result == feedback_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "submit_feedback",
                "from_agent_id": "a-from",
                "to_agent_id": "a-to",
                "task_id": "t-1",
                "category": "delivery_quality",
                "rating": "dissatisfied",
            }
        )
        await agent.close()


@pytest.mark.unit
class TestGetTaskFeedback:
    """Tests for get_task_feedback."""

    async def test_get_task_feedback_returns_list(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-123"
        feedback_response = {
            "task_id": "t-1",
            "feedback": [{"feedback_id": "fb-1", "rating": "satisfied"}],
        }
        agent._request = AsyncMock(return_value=feedback_response)

        result = await agent.get_task_feedback("t-1")

        assert result == feedback_response["feedback"]
        agent._request.assert_awaited_once_with(
            "GET", f"{sample_config.reputation_url}/feedback/task/t-1"
        )
        await agent.close()


@pytest.mark.unit
class TestGetAgentFeedback:
    """Tests for get_agent_feedback."""

    async def test_get_agent_feedback_returns_list(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-123"
        feedback_response = {
            "agent_id": "a-target",
            "feedback": [{"feedback_id": "fb-1", "rating": "satisfied"}],
        }
        agent._request = AsyncMock(return_value=feedback_response)

        result = await agent.get_agent_feedback("a-target")

        assert result == feedback_response["feedback"]
        agent._request.assert_awaited_once_with(
            "GET", f"{sample_config.reputation_url}/feedback/agent/a-target"
        )
        await agent.close()
