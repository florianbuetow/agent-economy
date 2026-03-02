"""Integration tests: Reputation writes via DB Gateway to economy.db."""

from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from tests.integration.conftest import read_db, read_one
from tests.integration.gateway_helpers import create_gateway_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _identity_payload(agent_id: str, name: str) -> dict[str, object]:
    key_seed = agent_id.replace("a-", "").upper().ljust(43, "Z")
    timestamp = "2026-03-02T05:30:00Z"
    return {
        "agent_id": agent_id,
        "name": name,
        "public_key": f"ed25519:{key_seed}=",
        "registered_at": timestamp,
        "event": {
            "event_source": "identity",
            "event_type": "agent.registered",
            "timestamp": timestamp,
            "agent_id": agent_id,
            "summary": f"{name} registered",
            "payload": json.dumps({"agent_name": name}),
        },
    }


async def _setup_prerequisite_data(client: httpx.AsyncClient) -> str:
    for agent_id, name in [("a-poster", "Poster"), ("a-worker", "Worker")]:
        identity_response = await client.post("/identity/agents", json=_identity_payload(agent_id, name))
        assert identity_response.status_code == 201

        account_response = await client.post(
            "/bank/accounts",
            json={
                "account_id": agent_id,
                "balance": 5000,
                "created_at": "2026-03-02T05:30:01Z",
                "initial_credit": {
                    "tx_id": f"tx-init-{agent_id}",
                    "amount": 5000,
                    "reference": "initial_balance",
                    "timestamp": "2026-03-02T05:30:01Z",
                },
                "event": {
                    "event_source": "bank",
                    "event_type": "account.created",
                    "timestamp": "2026-03-02T05:30:01Z",
                    "agent_id": agent_id,
                    "summary": f"Account for {name}",
                    "payload": "{}",
                },
            },
        )
        assert account_response.status_code == 201

    task_id = "t-rep-001"

    lock_response = await client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": f"esc-{task_id}",
            "payer_account_id": "a-poster",
            "amount": 200,
            "task_id": task_id,
            "created_at": "2026-03-02T05:31:00Z",
            "tx_id": f"tx-esc-{task_id}",
            "event": {
                "event_source": "bank",
                "event_type": "escrow.locked",
                "timestamp": "2026-03-02T05:31:00Z",
                "agent_id": "a-poster",
                "task_id": task_id,
                "summary": "Escrow locked",
                "payload": "{}",
            },
        },
    )
    assert lock_response.status_code == 201

    task_response = await client.post(
        "/board/tasks",
        json={
            "task_id": task_id,
            "poster_id": "a-poster",
            "title": "Task for feedback",
            "spec": "Do something",
            "reward": 200,
            "status": "approved",
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 7200,
            "review_deadline_seconds": 3600,
            "bidding_deadline": "2026-03-02T06:30:00Z",
            "bid_count": 0,
            "escrow_pending": 0,
            "escrow_id": f"esc-{task_id}",
            "created_at": "2026-03-02T05:31:00Z",
            "event": {
                "event_source": "board",
                "event_type": "task.created",
                "timestamp": "2026-03-02T05:31:00Z",
                "agent_id": "a-poster",
                "task_id": task_id,
                "summary": "Task created",
                "payload": "{}",
            },
        },
    )
    assert task_response.status_code == 201
    return task_id


@pytest_asyncio.fixture
async def gw_client(initialized_db: str):
    client = create_gateway_client(initialized_db)
    async with client:
        yield client


class TestReputationGatewayWrites:
    async def test_submit_feedback_writes_sealed_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        task_id = await _setup_prerequisite_data(gw_client)

        response = await gw_client.post(
            "/reputation/feedback",
            json={
                "feedback_id": "fb-001",
                "task_id": task_id,
                "from_agent_id": "a-poster",
                "to_agent_id": "a-worker",
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Good work",
                "submitted_at": "2026-03-02T05:50:00Z",
                "event": {
                    "event_source": "reputation",
                    "event_type": "feedback.submitted",
                    "timestamp": "2026-03-02T05:50:00Z",
                    "agent_id": "a-poster",
                    "task_id": task_id,
                    "summary": "Feedback submitted",
                    "payload": json.dumps({"from_name": "Poster", "to_name": "Worker"}),
                },
            },
        )
        assert response.status_code == 201

        row = read_one(
            initialized_db,
            "SELECT feedback_id, role, category, rating, comment, visible "
            "FROM reputation_feedback WHERE feedback_id = ?",
            ("fb-001",),
        )
        assert row is not None
        assert row["role"] == "poster"
        assert row["category"] == "delivery_quality"
        assert row["rating"] == "satisfied"
        assert row["comment"] == "Good work"
        assert row["visible"] == 0

    async def test_second_feedback_reveals_both_rows(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        task_id = await _setup_prerequisite_data(gw_client)

        first_response = await gw_client.post(
            "/reputation/feedback",
            json={
                "feedback_id": "fb-r1",
                "task_id": task_id,
                "from_agent_id": "a-poster",
                "to_agent_id": "a-worker",
                "role": "poster",
                "category": "delivery_quality",
                "rating": "satisfied",
                "comment": "Solid output",
                "submitted_at": "2026-03-02T05:55:00Z",
                "event": {
                    "event_source": "reputation",
                    "event_type": "feedback.submitted",
                    "timestamp": "2026-03-02T05:55:00Z",
                    "agent_id": "a-poster",
                    "task_id": task_id,
                    "summary": "Feedback submitted",
                    "payload": "{}",
                },
            },
        )
        assert first_response.status_code == 201

        second_response = await gw_client.post(
            "/reputation/feedback",
            json={
                "feedback_id": "fb-r2",
                "task_id": task_id,
                "from_agent_id": "a-worker",
                "to_agent_id": "a-poster",
                "role": "worker",
                "category": "spec_quality",
                "rating": "satisfied",
                "comment": "Clear specification",
                "submitted_at": "2026-03-02T05:56:00Z",
                "reveal_reverse": True,
                "reverse_feedback_id": "fb-r1",
                "event": {
                    "event_source": "reputation",
                    "event_type": "feedback.submitted",
                    "timestamp": "2026-03-02T05:56:00Z",
                    "agent_id": "a-worker",
                    "task_id": task_id,
                    "summary": "Feedback submitted",
                    "payload": "{}",
                },
            },
        )
        assert second_response.status_code == 201

        rows = read_db(
            initialized_db,
            "SELECT feedback_id, visible FROM reputation_feedback "
            "WHERE task_id = ? ORDER BY feedback_id",
            (task_id,),
        )
        assert len(rows) == 2
        assert rows[0]["feedback_id"] == "fb-r1"
        assert rows[1]["feedback_id"] == "fb-r2"
        assert rows[0]["visible"] == 1
        assert rows[1]["visible"] == 1
