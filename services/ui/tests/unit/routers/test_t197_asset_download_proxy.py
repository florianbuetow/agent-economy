"""T-197 acceptance tests for the same-origin operator asset proxy."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ui_service.core.state import get_app_state

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = pytest.mark.unit


def _configure_agent(response: httpx.Response) -> Any:
    agent = get_app_state().user_agent
    assert agent is not None
    agent.config = SimpleNamespace(task_board_url="https://board.test")  # type: ignore[attr-defined]
    agent._auth_header = MagicMock(  # type: ignore[attr-defined]
        return_value={"Authorization": "Bearer signed-operator-token"}
    )
    agent._request_raw = AsyncMock(  # type: ignore[attr-defined]
        return_value=response
    )
    # The operator transport binds the signer too; a download must never sign.
    agent._sign_jws = MagicMock(  # type: ignore[attr-defined]
        side_effect=AssertionError("asset download must not sign a token")
    )
    return agent


async def test_operator_asset_proxy_returns_exact_bytes_and_safe_headers(
    client: AsyncClient,
) -> None:
    request = httpx.Request("GET", "https://board.test/tasks/t-one/assets/asset-one")
    agent = _configure_agent(
        httpx.Response(
            200,
            request=request,
            content=b"exact submission\n",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": (
                    "attachment; filename=\"answer.txt\"; filename*=UTF-8''answer.txt"
                ),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    )

    response = await client.get("/api/proxy/tasks/t-one/assets/asset-one")

    assert response.status_code == 200
    assert response.content == b"exact submission\n"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"].startswith(
        "attachment; filename=\"answer.txt\"; filename*=UTF-8''answer.txt"
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    agent._auth_header.assert_called_once_with(  # type: ignore[attr-defined]
        {"action": "download_asset", "task_id": "t-one", "asset_id": "asset-one"},
        audience="task-board",
        resource="GET /tasks/t-one/assets/asset-one",
    )
    agent._request_raw.assert_awaited_once_with(  # type: ignore[attr-defined]
        "GET",
        "https://board.test/tasks/t-one/assets/asset-one",
        headers={"Authorization": "Bearer signed-operator-token"},
    )


async def test_operator_asset_proxy_rejects_unsafe_upstream_filename_header(
    client: AsyncClient,
) -> None:
    request = httpx.Request("GET", "https://board.test/tasks/t-one/assets/asset-one")
    _configure_agent(
        httpx.Response(
            200,
            request=request,
            content=b"exact submission\n",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": "inline; filename=answer.txt",
            },
        )
    )

    response = await client.get("/api/proxy/tasks/t-one/assets/asset-one")

    assert response.status_code == 502
    assert response.json()["error"] == "asset_download_failed"
