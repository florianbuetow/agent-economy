"""Unit tests for TaskBoardMixin.list_assets / download_asset.

These are new public, unauthenticated Task Board endpoints
(``GET /tasks/{task_id}/assets`` and ``GET /tasks/{task_id}/assets/{asset_id}``)
that the mixin previously had no client methods for — ReviewLoop needs them
to read a worker's actual submitted answer (T-102 headline e2e).
"""

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
    async def test_list_assets_returns_asset_list(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent.agent_id = "a-poster"
        assets_response = {
            "task_id": "t-1",
            "assets": [
                {
                    "asset_id": "asset-1",
                    "uploader_id": "a-worker",
                    "filename": "t-1_solution.txt",
                    "content_type": "application/octet-stream",
                    "size_bytes": 2,
                    "content_hash": "abc",
                    "uploaded_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        agent._request = AsyncMock(return_value=assets_response)

        result = await agent.list_assets("t-1")

        assert result == assets_response["assets"]
        agent._request.assert_awaited_once_with(
            "GET", f"{sample_config.task_board_url}/tasks/t-1/assets"
        )
        await agent.close()

    async def test_list_assets_empty(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        agent._request = AsyncMock(return_value={"task_id": "t-1", "assets": []})

        result = await agent.list_assets("t-1")

        assert result == []
        await agent.close()


@pytest.mark.unit
class TestDownloadAsset:
    async def test_download_asset_returns_raw_content(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        fake_response = MagicMock()
        fake_response.content = b"42"
        fake_response.raise_for_status = MagicMock()
        agent._request_raw = AsyncMock(return_value=fake_response)

        result = await agent.download_asset("t-1", "asset-1")

        assert result == b"42"
        agent._request_raw.assert_awaited_once_with(
            "GET", f"{sample_config.task_board_url}/tasks/t-1/assets/asset-1"
        )
        fake_response.raise_for_status.assert_called_once()
        await agent.close()

    async def test_download_asset_raises_on_error_status(self, sample_config: AgentConfig) -> None:
        agent = BaseAgent(config=sample_config)
        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(404, request=httpx.Request("GET", "http://x")),
            )
        )
        agent._request_raw = AsyncMock(return_value=fake_response)

        with pytest.raises(httpx.HTTPStatusError):
            await agent.download_asset("t-1", "asset-missing")

        await agent.close()
