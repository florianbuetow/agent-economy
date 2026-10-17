"""Real demo replay tokens carry strict receiver-owned context."""

from __future__ import annotations

import json

import httpx
import pytest
from service_auth.financial_identity import operation_id_for

from demo_replay.clients import create_account, post_task
from demo_replay.wallet import DemoAgent
from service_auth.jws import parse_jws


@pytest.mark.unit
@pytest.mark.asyncio
async def test_demo_bank_and_two_token_task_post_contexts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/accounts":
            return httpx.Response(
                201,
                json={
                    "account_id": "a-worker",
                    "balance": 0,
                    "created_at": "2026-10-17T14:00:00Z",
                },
            )
        return httpx.Response(201, json={"task_id": "task"})

    platform = DemoAgent.create("platform", "Platform", 45)
    platform.agent_id = "a-platform"
    poster = DemoAgent.create("poster", "Poster", 45)
    poster.agent_id = "a-poster"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await create_account(client, platform, "a-worker", "https://bank")
        await post_task(client, poster, "Title", "Spec", 10, "https://board")

    account = parse_jws(json.loads(requests[0].content)["token"])
    assert account.header.aud == "central-bank"
    assert account.header.resource == "POST /accounts"
    assert account.header.exp - account.header.iat == 45
    task_body = json.loads(requests[1].content)
    task = parse_jws(task_body["task_token"])
    escrow = parse_jws(task_body["escrow_token"])
    assert (task.header.aud, task.header.resource) == ("task-board", "POST /tasks")
    assert (escrow.header.aud, escrow.header.resource) == (
        "central-bank",
        "POST /escrow/lock",
    )
    assert task.payload["action"] == "create_task"
    assert escrow.payload["action"] == "escrow_lock"
    assert escrow.payload["operation_id"] == operation_id_for(
        "task-lock",
        (str(task.payload["task_id"]), "a-poster"),
    )


@pytest.mark.unit
def test_demo_agent_rejects_nonpositive_or_boolean_ttl() -> None:
    for invalid in (True, 0, -1):
        with pytest.raises(ValueError):
            DemoAgent.create("demo", "Demo", invalid)  # type: ignore[arg-type]
