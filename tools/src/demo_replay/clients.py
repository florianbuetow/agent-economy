"""Async HTTP clients for each platform service.

Thin wrappers matching the API contracts in agents/src/base_agent/mixins/.
Each function takes a DemoAgent and the relevant parameters, signs the
request, and returns the parsed JSON response.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from demo_replay.wallet import DemoAgent

# Default service URLs (same as agents/config.yaml)
IDENTITY_URL = "http://localhost:8001"
BANK_URL = "http://localhost:8002"
TASK_BOARD_URL = "http://localhost:8003"


async def register_agent(
    client: httpx.AsyncClient,
    agent: DemoAgent,
    identity_url: str = IDENTITY_URL,
) -> dict[str, Any]:
    """Register an agent with the Identity service. Sets agent.agent_id on success.

    Handles 409 Conflict by looking up the existing agent by public key.
    """
    url = f"{identity_url}/agents/register"
    payload = {"name": agent.name, "public_key": agent.public_key_string()}
    resp = await client.post(url, json=payload)

    if resp.status_code == 201:
        data: dict[str, Any] = resp.json()
        agent.agent_id = data["agent_id"]
        return data

    if resp.status_code == 409:
        # Key already registered — find the existing agent_id
        agents_resp = await client.get(f"{identity_url}/agents")
        agents_resp.raise_for_status()
        my_key = agent.public_key_string()
        for entry in agents_resp.json()["agents"]:
            agent_id = entry.get("agent_id")
            if isinstance(agent_id, str):
                detail = await client.get(f"{identity_url}/agents/{agent_id}")
                detail.raise_for_status()
                if detail.json().get("public_key") == my_key:
                    agent.agent_id = agent_id
                    return detail.json()  # type: ignore[no-any-return]
        msg = "Could not find existing agent after 409 conflict"
        raise RuntimeError(msg)

    resp.raise_for_status()
    msg = f"Unexpected status: {resp.status_code}"
    raise RuntimeError(msg)


async def create_account(
    client: httpx.AsyncClient,
    platform: DemoAgent,
    agent_id: str,
    bank_url: str = BANK_URL,
) -> dict[str, Any]:
    """Create a bank account for an agent (platform-signed)."""
    url = f"{bank_url}/accounts"
    token = platform.sign_jws(
        {"action": "create_account", "agent_id": agent_id, "initial_balance": 0}
    )
    resp = await client.post(url, json={"token": token})
    if resp.status_code == 409:
        return {"status": "already_exists"}
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def credit_account(
    client: httpx.AsyncClient,
    platform: DemoAgent,
    account_id: str,
    amount: int,
    bank_url: str = BANK_URL,
) -> dict[str, Any]:
    """Credit funds to an agent's account (platform-signed)."""
    url = f"{bank_url}/accounts/{account_id}/credit"
    reference = f"demo_fund_{uuid.uuid4().hex[:8]}"
    token = platform.sign_jws(
        {
            "action": "credit",
            "account_id": account_id,
            "amount": amount,
            "reference": reference,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def post_task(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    title: str,
    spec: str,
    reward: int,
    task_board_url: str = TASK_BOARD_URL,
    bidding_deadline_seconds: int = 3600,
    execution_deadline_seconds: int = 7200,
    review_deadline_seconds: int = 3600,
) -> dict[str, Any]:
    """Post a new task to the Task Board. Returns response including task_id."""
    url = f"{task_board_url}/tasks"
    task_id = f"t-{uuid.uuid4()}"
    task_token = poster.sign_jws(
        {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": poster.agent_id,
            "title": title,
            "spec": spec,
            "reward": reward,
            "bidding_deadline_seconds": bidding_deadline_seconds,
            "execution_deadline_seconds": execution_deadline_seconds,
            "review_deadline_seconds": review_deadline_seconds,
        }
    )
    escrow_token = poster.sign_jws(
        {
            "action": "escrow_lock",
            "task_id": task_id,
            "amount": reward,
            "agent_id": poster.agent_id,
        }
    )
    resp = await client.post(
        url, json={"task_token": task_token, "escrow_token": escrow_token}
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def submit_bid(
    client: httpx.AsyncClient,
    bidder: DemoAgent,
    task_id: str,
    amount: int,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Submit a bid on a task."""
    url = f"{task_board_url}/tasks/{task_id}/bids"
    token = bidder.sign_jws(
        {
            "action": "submit_bid",
            "task_id": task_id,
            "bidder_id": bidder.agent_id,
            "amount": amount,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def list_bids(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    task_id: str,
    task_board_url: str = TASK_BOARD_URL,
) -> list[dict[str, Any]]:
    """List bids for a task (poster-signed auth header)."""
    url = f"{task_board_url}/tasks/{task_id}/bids"
    headers = poster.auth_header(
        {
            "action": "list_bids",
            "task_id": task_id,
            "poster_id": poster.agent_id,
        }
    )
    resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    bids: list[dict[str, Any]] = data["bids"]
    return bids


async def accept_bid(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    task_id: str,
    bid_id: str,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Accept a bid on a task."""
    url = f"{task_board_url}/tasks/{task_id}/bids/{bid_id}/accept"
    token = poster.sign_jws(
        {
            "action": "accept_bid",
            "task_id": task_id,
            "bid_id": bid_id,
            "poster_id": poster.agent_id,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def upload_asset(
    client: httpx.AsyncClient,
    worker: DemoAgent,
    task_id: str,
    filename: str,
    content: bytes,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Upload a file asset for a task."""
    url = f"{task_board_url}/tasks/{task_id}/assets"
    headers = worker.auth_header({"action": "upload_asset", "task_id": task_id})
    resp = await client.post(
        url, headers=headers, files={"file": (filename, content)}
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def submit_deliverable(
    client: httpx.AsyncClient,
    worker: DemoAgent,
    task_id: str,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Submit deliverables for review."""
    url = f"{task_board_url}/tasks/{task_id}/submit"
    token = worker.sign_jws(
        {
            "action": "submit_deliverable",
            "task_id": task_id,
            "worker_id": worker.agent_id,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def approve_task(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    task_id: str,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Approve a submitted task."""
    url = f"{task_board_url}/tasks/{task_id}/approve"
    token = poster.sign_jws(
        {
            "action": "approve_task",
            "task_id": task_id,
            "poster_id": poster.agent_id,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def dispute_task(
    client: httpx.AsyncClient,
    poster: DemoAgent,
    task_id: str,
    reason: str,
    task_board_url: str = TASK_BOARD_URL,
) -> dict[str, Any]:
    """Dispute a submitted task."""
    url = f"{task_board_url}/tasks/{task_id}/dispute"
    token = poster.sign_jws(
        {
            "action": "dispute_task",
            "task_id": task_id,
            "poster_id": poster.agent_id,
            "reason": reason,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def submit_feedback(
    client: httpx.AsyncClient,
    agent: DemoAgent,
    task_id: str,
    to_agent_id: str,
    role: str,
    category: str,
    rating: str,
    comment: str,
    reputation_url: str = "http://localhost:8004",
) -> dict[str, Any]:
    """Submit sealed feedback for a task."""
    url = f"{reputation_url}/feedback"
    token = agent.sign_jws(
        {
            "action": "submit_feedback",
            "task_id": task_id,
            "to_agent_id": to_agent_id,
            "role": role,
            "category": category,
            "rating": rating,
            "comment": comment,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def reveal_feedback(
    client: httpx.AsyncClient,
    agent: DemoAgent,
    task_id: str,
    reputation_url: str = "http://localhost:8004",
) -> dict[str, Any]:
    """Reveal feedback for a task (both parties have submitted)."""
    url = f"{reputation_url}/tasks/{task_id}/feedback/reveal"
    token = agent.sign_jws(
        {
            "action": "reveal_feedback",
            "task_id": task_id,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]
