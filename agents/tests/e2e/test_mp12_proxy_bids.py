"""Real signed proxy authorization and bid disclosure across the open boundary."""

from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from .native_task_contract import lifecycle_version, new_operation_id

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("_require_ui_service")]


@pytest.fixture(autouse=True)
def _require_disposable_runtime() -> None:
    """Refuse accidental direct runs against ordinary default service URLs."""
    assert os.environ.get("ISOLATED_RUNTIME_ID"), "Run through isolated_test_runtime.py"
    assert os.environ.get("ISOLATED_RUNTIME_DIR"), "An isolated runtime directory is required"


@pytest.fixture
async def bid_proxy_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=os.environ["UI_URL"], timeout=10.0) as client:
        configuration = await client.get("/api/proxy/browser-config")
        assert configuration.status_code == 200, configuration.text
        marker = configuration.json()
        client.headers["Origin"] = str(client.base_url).rstrip("/")
        client.headers[marker["csrf_header_name"]] = marker["csrf_header_value"]
        yield client


def _task_input(title: str) -> dict[str, object]:
    return {
        "title": title,
        "spec": "MP-12 signed bid authorization and public projection proof.",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "acceptance_window_seconds": 3600,
        "execution_deadline_seconds": 7200,
        "review_deadline_seconds": 3600,
    }


async def test_real_operator_bids_stay_sealed_then_reveal_losing_bidder_history(
    bid_proxy_client: httpx.AsyncClient,
    make_funded_agent: Any,
) -> None:
    async with AsyncExitStack() as resources:
        winner = await make_funded_agent(name="MP12 chosen worker", balance=0)
        resources.push_async_callback(winner.close)
        loser = await make_funded_agent(name="MP12 unchosen worker", balance=0)
        resources.push_async_callback(loser.close)
        created = await bid_proxy_client.post(
            "/api/proxy/tasks", json=_task_input("MP12 operator-owned task")
        )
        assert created.status_code == 200, created.text
        task = created.json()
        task_id = task["task_id"]
        winning_bid_response = await winner.submit_bid(
            task_id=task_id,
            amount=73,
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(task),
        )
        assert winning_bid_response.status_code == 201
        winning_bid = winning_bid_response.body
        losing_bid_response = await loser.submit_bid(
            task_id=task_id,
            amount=65,
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(task),
        )
        assert losing_bid_response.status_code == 201
        losing_bid = losing_bid_response.body

        public = await bid_proxy_client.get(f"/api/tasks/{task_id}")
        assert public.status_code == 200, public.text
        assert public.json()["bid_count"] == 2
        assert public.json()["bids"] == []
        assert winner.agent_id not in json.dumps(public.json())
        assert loser.agent_id not in json.dumps(public.json())

        authorized = await bid_proxy_client.get(f"/api/proxy/tasks/{task_id}/bids")
        assert authorized.status_code == 200, authorized.text
        assert "private" in authorized.headers["cache-control"]
        assert "no-store" in authorized.headers["cache-control"]
        bids = {bid["bid_id"]: bid for bid in authorized.json()["bids"]}
        assert bids[winning_bid["bid_id"]]["amount"] == 73
        assert bids[winning_bid["bid_id"]]["bidder"]["name"] == "MP12 chosen worker"
        assert bids[losing_bid["bid_id"]]["amount"] == 65
        assert bids[losing_bid["bid_id"]]["bidder"]["name"] == "MP12 unchosen worker"

        actor_filter = {
            "task_id": task_id,
            "type": "bid.submitted",
            "agent_id": loser.agent_id,
        }
        sealed_history = await bid_proxy_client.get("/api/events", params=actor_filter)
        assert sealed_history.status_code == 200, sealed_history.text
        assert sealed_history.json()["events"] == []

        accepted = await bid_proxy_client.post(
            f"/api/proxy/tasks/{task_id}/bids/{winning_bid['bid_id']}/accept",
            json={
                "operation_id": new_operation_id(),
                "expected_lifecycle_version": lifecycle_version(task),
            },
        )
        assert accepted.status_code == 200, accepted.text
        revealed = await bid_proxy_client.get(f"/api/tasks/{task_id}")
        assert revealed.status_code == 200, revealed.text
        assert {bid["bid_id"] for bid in revealed.json()["bids"]} == set(bids)
        history = await bid_proxy_client.get("/api/events", params=actor_filter)
        assert history.status_code == 200, history.text
        events = history.json()["events"]
        assert len(events) == 1
        assert events[0]["agent_id"] == loser.agent_id
        feed = await bid_proxy_client.get(f"/api/agents/{loser.agent_id}/feed?type=BID")
        assert feed.status_code == 200, feed.text
        assert events[0]["event_id"] in {event["event_id"] for event in feed.json()["events"]}


async def test_real_operator_cannot_list_another_posters_open_bids(
    bid_proxy_client: httpx.AsyncClient,
    make_funded_agent: Any,
) -> None:
    async with AsyncExitStack() as resources:
        poster = await make_funded_agent(name="MP12 independent poster", balance=200)
        resources.push_async_callback(poster.close)
        worker = await make_funded_agent(name="MP12 private bidder", balance=0)
        resources.push_async_callback(worker.close)
        task = await poster.post_task(**_task_input("MP12 independent task"))
        bid_response = await worker.submit_bid(
            task_id=task["task_id"],
            amount=31,
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(task),
        )
        assert bid_response.status_code == 201
        bid = bid_response.body

        denied = await bid_proxy_client.get(f"/api/proxy/tasks/{task['task_id']}/bids")

        assert denied.status_code == 403, denied.text
        assert "bids" not in denied.json()
        assert bid["bid_id"] not in denied.text
        assert worker.agent_id not in denied.text
