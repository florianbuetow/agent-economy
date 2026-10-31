"""Unit tests for TaskBoardMixin.list_assets / download_asset (signed reads)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from base_agent.agent import BaseAgent

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


@pytest.mark.unit
class TestListAssets:
    async def test_list_assets_signs_and_returns_asset_list(
        self, sample_config: AgentConfig
    ) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-poster"
        assets_response = {"task_id": "t-1", "assets": [{"asset_id": "asset-1"}]}
        agent._request = AsyncMock(return_value=assets_response)

        result = await agent.list_assets("t-1")

        assert result == assets_response["assets"]
        method, url = agent._request.await_args.args
        headers = agent._request.await_args.kwargs["headers"]
        assert (method, url) == ("GET", f"{sample_config.task_board_url}/tasks/t-1/assets")
        assert headers["Authorization"].startswith("Bearer ")
        await agent.close()

    async def test_list_assets_empty(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-poster"
        agent._request = AsyncMock(return_value={"task_id": "t-1", "assets": []})

        assert await agent.list_assets("t-1") == []
        await agent.close()


@pytest.mark.unit
class TestDownloadAsset:
    async def test_download_asset_signs_and_returns_raw_content(
        self, sample_config: AgentConfig
    ) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-worker"
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.content = b"42"
        agent._request_raw = AsyncMock(return_value=response)

        assert await agent.download_asset("t-1", "asset-1") == b"42"
        method, url = agent._request_raw.await_args.args
        headers = agent._request_raw.await_args.kwargs["headers"]
        assert (method, url) == (
            "GET",
            f"{sample_config.task_board_url}/tasks/t-1/assets/asset-1",
        )
        assert headers["Authorization"].startswith("Bearer ")
        await agent.close()

    async def test_download_asset_raises_on_error_status(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-worker"
        request = httpx.Request("GET", "http://board/tasks/t-1/assets/asset-missing")
        response = httpx.Response(404, json={"error": "not_found", "message": "x"}, request=request)
        agent._request_raw = AsyncMock(return_value=response)

        with pytest.raises(httpx.HTTPStatusError):
            await agent.download_asset("t-1", "asset-missing")
        await agent.close()
