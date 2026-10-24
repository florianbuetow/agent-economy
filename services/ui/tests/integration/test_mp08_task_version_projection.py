"""Task views retain the authoritative revision needed for safe browser commands."""

from typing import Any

import pytest
from test_mp08_authoritative_task_reads import _detail, _Reader

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration


async def test_task_detail_preserves_board_lifecycle_version(client: Any) -> None:
    task = _detail(status="open", accepted_bid_id=None)
    task["lifecycle_version"] = 17
    get_app_state().task_board_reader = _Reader(detail=task)
    response = await client.get("/api/tasks/t-task1")
    assert response.status_code == 200
    assert response.json()["lifecycle_version"] == 17


@pytest.mark.parametrize("version", [None, True, -1, "17", "missing"])
async def test_missing_or_invalid_revision_blocks_browser_command_snapshot(
    client: Any,
    version: object,
) -> None:
    task = _detail(status="open", accepted_bid_id=None)
    if version == "missing":
        task.pop("lifecycle_version", None)
    else:
        task["lifecycle_version"] = version
    get_app_state().task_board_reader = _Reader(detail=task)
    response = await client.get("/api/tasks/t-task1")
    assert response.status_code == 502
    assert response.json()["error"] == "task_board_unavailable"
