from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_download_uploaded_asset(make_funded_agent) -> None:
    """Confirm uploaded asset content can be downloaded and matches original."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster AS1", balance=5000)
        worker = await make_funded_agent(name="Worker AS1", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Asset download task",
            spec="Upload a file",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])

        original_content = b"Hello World from asset store test"
        asset = await worker.upload_asset(task["task_id"], "result.txt", original_content)
        assert isinstance(asset.get("asset_id"), str)

        # List assets to get the asset_id
        assets_response = await worker._request(
            "GET",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/assets",
        )
        assets = assets_response["assets"]
        assert len(assets) == 1
        asset_id = assets[0]["asset_id"]

        # Download the asset
        download_response = await worker._request_raw(
            "GET",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/assets/{asset_id}",
        )
        assert download_response.status_code == 200
        assert download_response.content == original_content
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_multiple_asset_uploads(make_funded_agent) -> None:
    """Confirm multiple files can be uploaded to a single task."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster AS2", balance=5000)
        worker = await make_funded_agent(name="Worker AS2", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Multi asset task",
            spec="Upload multiple files",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])

        await worker.upload_asset(task["task_id"], "code.py", b"print(1)")
        await worker.upload_asset(task["task_id"], "readme.md", b"# Hello")
        await worker.upload_asset(task["task_id"], "data.json", b'{"key": 1}')

        assets_response = await worker._request(
            "GET",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/assets",
        )
        assets = assets_response["assets"]
        assert len(assets) == 3

        filenames = {a["filename"] for a in assets}
        assert filenames == {"code.py", "readme.md", "data.json"}

        for asset in assets:
            assert isinstance(asset["asset_id"], str)
            assert asset["size_bytes"] > 0
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_submit_without_assets_rejected(make_funded_agent) -> None:
    """Adversarial: worker cannot submit deliverable with no uploaded assets."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster AS3", balance=5000)
        worker = await make_funded_agent(name="Worker AS3", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="No assets task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])

        # Try to submit without uploading any assets
        response = await worker._request_raw(
            "POST",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/submit",
            json={
                "token": worker._sign_jws(
                    {
                        "action": "submit_deliverable",
                        "task_id": task["task_id"],
                        "worker_id": worker.agent_id,
                    }
                )
            },
        )

        # Spec says "requires at least 1 asset" — expect rejection
        assert response.status_code in {400, 409}, (
            f"Expected 400 or 409 for submit without assets, got {response.status_code}"
        )

        # Task should remain in accepted state
        task_after = await poster.get_task(task["task_id"])
        assert task_after["status"] == "accepted", "Task should remain accepted after failed submit"
    finally:
        await _close_agents(agents_to_close)
