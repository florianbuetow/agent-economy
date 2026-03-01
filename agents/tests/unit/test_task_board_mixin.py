"""Unit tests for TaskBoardMixin methods."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock, patch

import pytest

from base_agent.agent import BaseAgent

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


@pytest.mark.unit
class TestPostTask:
    """Tests for post_task."""

    @patch("base_agent.mixins.task_board.uuid.uuid4", return_value="fake-uuid")
    async def test_post_task_sends_both_tokens(
        self, _mock_uuid: Mock, sample_config: AgentConfig
    ) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-poster"
        task_response = {"task_id": "t-fake-uuid", "status": "open"}
        agent._sign_jws = Mock(side_effect=["task-jws", "escrow-jws"])
        agent._request = AsyncMock(return_value=task_response)

        result = await agent.post_task(
            title="Build login",
            spec="Create a login page",
            reward=5000,
            bidding_deadline_seconds=86400,
            execution_deadline_seconds=259200,
            review_deadline_seconds=172800,
        )

        assert result == task_response
        assert agent._sign_jws.call_count == 2
        task_call = agent._sign_jws.call_args_list[0]
        assert task_call[0][0]["action"] == "create_task"
        assert task_call[0][0]["task_id"] == "t-fake-uuid"
        assert task_call[0][0]["poster_id"] == "a-poster"
        assert task_call[0][0]["title"] == "Build login"
        assert task_call[0][0]["reward"] == 5000
        escrow_call = agent._sign_jws.call_args_list[1]
        assert escrow_call[0][0]["action"] == "lock_escrow"
        assert escrow_call[0][0]["task_id"] == "t-fake-uuid"
        assert escrow_call[0][0]["amount"] == 5000
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.task_board_url}/tasks",
            json={"task_token": "task-jws", "escrow_token": "escrow-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestListTasks:
    """Tests for list_tasks."""

    async def test_list_tasks_no_filters(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-123"
        tasks_response = {"tasks": [{"task_id": "t-1", "status": "open"}]}
        agent._request = AsyncMock(return_value=tasks_response)

        result = await agent.list_tasks()

        assert result == tasks_response["tasks"]
        agent._request.assert_awaited_once_with(
            "GET", f"{sample_config.task_board_url}/tasks", params={}
        )
        await agent.close()

    async def test_list_tasks_with_filters(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-123"
        tasks_response = {"tasks": []}
        agent._request = AsyncMock(return_value=tasks_response)

        result = await agent.list_tasks(status="open", poster_id="a-poster")

        assert result == []
        agent._request.assert_awaited_once_with(
            "GET",
            f"{sample_config.task_board_url}/tasks",
            params={"status": "open", "poster_id": "a-poster"},
        )
        await agent.close()


@pytest.mark.unit
class TestGetTask:
    """Tests for get_task."""

    async def test_get_task_returns_details(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-123"
        task_response = {"task_id": "t-1", "status": "open", "title": "Test"}
        agent._request = AsyncMock(return_value=task_response)

        result = await agent.get_task("t-1")

        assert result == task_response
        agent._request.assert_awaited_once_with("GET", f"{sample_config.task_board_url}/tasks/t-1")
        await agent.close()


@pytest.mark.unit
class TestCancelTask:
    """Tests for cancel_task."""

    async def test_cancel_task_success(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-poster"
        cancel_response = {"task_id": "t-1", "status": "cancelled"}
        agent._sign_jws = Mock(return_value="cancel-jws")
        agent._request = AsyncMock(return_value=cancel_response)

        result = await agent.cancel_task("t-1")

        assert result == cancel_response
        agent._sign_jws.assert_called_once_with(
            {"action": "cancel_task", "task_id": "t-1", "poster_id": "a-poster"}
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.task_board_url}/tasks/t-1/cancel",
            json={"token": "cancel-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestSubmitBid:
    """Tests for submit_bid."""

    async def test_submit_bid_success(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-bidder"
        bid_response = {"bid_id": "bid-1", "task_id": "t-1", "amount": 4500}
        agent._sign_jws = Mock(return_value="bid-jws")
        agent._request = AsyncMock(return_value=bid_response)

        result = await agent.submit_bid("t-1", amount=4500)

        assert result == bid_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "submit_bid",
                "task_id": "t-1",
                "bidder_id": "a-bidder",
                "amount": 4500,
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.task_board_url}/tasks/t-1/bids",
            json={"token": "bid-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestListBids:
    """Tests for list_bids."""

    async def test_list_bids_returns_list(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-poster"
        bids_response = {
            "task_id": "t-1",
            "bids": [{"bid_id": "bid-1", "amount": 4500}],
        }
        expected_headers = {"Authorization": "Bearer test-token"}
        agent._auth_header = Mock(return_value=expected_headers)
        agent._request = AsyncMock(return_value=bids_response)

        result = await agent.list_bids("t-1")

        assert result == bids_response["bids"]
        agent._auth_header.assert_called_once_with(
            {"action": "list_bids", "task_id": "t-1", "poster_id": "a-poster"}
        )
        agent._request.assert_awaited_once_with(
            "GET",
            f"{sample_config.task_board_url}/tasks/t-1/bids",
            headers=expected_headers,
        )
        await agent.close()


@pytest.mark.unit
class TestAcceptBid:
    """Tests for accept_bid."""

    async def test_accept_bid_success(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-poster"
        accept_response = {"task_id": "t-1", "status": "accepted", "worker_id": "a-w"}
        agent._sign_jws = Mock(return_value="accept-jws")
        agent._request = AsyncMock(return_value=accept_response)

        result = await agent.accept_bid("t-1", "bid-1")

        assert result == accept_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "accept_bid",
                "task_id": "t-1",
                "bid_id": "bid-1",
                "poster_id": "a-poster",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.task_board_url}/tasks/t-1/bids/bid-1/accept",
            json={"token": "accept-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestUploadAsset:
    """Tests for upload_asset."""

    async def test_upload_asset_success(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-worker"
        asset_response = {"asset_id": "asset-1", "filename": "out.txt"}
        expected_headers = {"Authorization": "Bearer test-token"}
        agent._auth_header = Mock(return_value=expected_headers)
        agent._request = AsyncMock(return_value=asset_response)

        result = await agent.upload_asset("t-1", "out.txt", b"file content")

        assert result == asset_response
        agent._auth_header.assert_called_once_with({"action": "upload_asset", "task_id": "t-1"})
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.task_board_url}/tasks/t-1/assets",
            headers=expected_headers,
            files={"file": ("out.txt", b"file content")},
        )
        await agent.close()


@pytest.mark.unit
class TestSubmitDeliverable:
    """Tests for submit_deliverable."""

    async def test_submit_deliverable_success(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-worker"
        submit_response = {"task_id": "t-1", "status": "submitted"}
        agent._sign_jws = Mock(return_value="submit-jws")
        agent._request = AsyncMock(return_value=submit_response)

        result = await agent.submit_deliverable("t-1")

        assert result == submit_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "submit_deliverable",
                "task_id": "t-1",
                "worker_id": "a-worker",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.task_board_url}/tasks/t-1/submit",
            json={"token": "submit-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestApproveTask:
    """Tests for approve_task."""

    async def test_approve_task_success(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-poster"
        approve_response = {"task_id": "t-1", "status": "approved"}
        agent._sign_jws = Mock(return_value="approve-jws")
        agent._request = AsyncMock(return_value=approve_response)

        result = await agent.approve_task("t-1")

        assert result == approve_response
        agent._sign_jws.assert_called_once_with(
            {"action": "approve_task", "task_id": "t-1", "poster_id": "a-poster"}
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.task_board_url}/tasks/t-1/approve",
            json={"token": "approve-jws"},
        )
        await agent.close()


@pytest.mark.unit
class TestDisputeTask:
    """Tests for dispute_task."""

    async def test_dispute_task_success(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-poster"
        dispute_response = {"task_id": "t-1", "status": "disputed"}
        agent._sign_jws = Mock(return_value="dispute-jws")
        agent._request = AsyncMock(return_value=dispute_response)

        result = await agent.dispute_task("t-1", reason="Incomplete work")

        assert result == dispute_response
        agent._sign_jws.assert_called_once_with(
            {
                "action": "dispute_task",
                "task_id": "t-1",
                "poster_id": "a-poster",
                "reason": "Incomplete work",
            }
        )
        agent._request.assert_awaited_once_with(
            "POST",
            f"{sample_config.task_board_url}/tasks/t-1/dispute",
            json={"token": "dispute-jws"},
        )
        await agent.close()
