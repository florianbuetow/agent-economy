"""Signed Task Board asset reads.

Task Board requires ``Authorization: Bearer <JWS>`` (audience ``task-board``) on
``GET /tasks/{task_id}/assets`` and ``GET /tasks/{task_id}/assets/{asset_id}``.
The SDK's ``list_assets``/``download_asset`` send no token, so these helpers sign
the request with the agent's own signing primitives and issue the raw GET.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def signed_list_assets(agent: BaseAgent, task_id: str) -> list[dict[str, Any]]:
    """List a task's assets with a signed ``list_assets`` request."""
    auth_header = agent.__getattribute__("_auth_header")
    request = agent.__getattribute__("_request")
    url = f"{agent.config.task_board_url}/tasks/{quote(task_id, safe='')}/assets"
    headers = auth_header(
        {"action": "list_assets", "task_id": task_id},
        audience="task-board",
        resource=f"GET /tasks/{task_id}/assets",
    )
    response = await request("GET", url, headers=headers)
    return cast("list[dict[str, Any]]", response["assets"])


async def signed_download_asset(agent: BaseAgent, task_id: str, asset_id: str) -> bytes:
    """Download an asset's bytes with a signed ``download_asset`` request."""
    auth_header = agent.__getattribute__("_auth_header")
    request_raw = agent.__getattribute__("_request_raw")
    url = (
        f"{agent.config.task_board_url}/tasks/{quote(task_id, safe='')}"
        f"/assets/{quote(asset_id, safe='')}"
    )
    headers = auth_header(
        {"action": "download_asset", "task_id": task_id, "asset_id": asset_id},
        audience="task-board",
        resource=f"GET /tasks/{task_id}/assets/{asset_id}",
    )
    response = await request_raw("GET", url, headers=headers)
    response.raise_for_status()
    return cast("bytes", response.content)
