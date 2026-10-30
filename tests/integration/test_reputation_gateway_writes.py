"""Integration tests: Reputation writes via DB Gateway to economy.db."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from tests.integration.conftest import read_db, read_one
from tests.integration.gateway_helpers import create_gateway_client
from tests.integration.native_task_fixtures import (
    advance_task_to_submitted,
    approve_with_bank_receipt,
    freeze_task_clock,
    publish_open_task,
)

if TYPE_CHECKING:
    import httpx

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
    accounts = [
        ("a-poster", "Poster", "op-00000000-0000-4000-8000-000000000301"),
        ("a-worker", "Worker", "op-00000000-0000-4000-8000-000000000302"),
    ]
    for agent_id, name, operation_id in accounts:
        identity_response = await client.post(
            "/identity/agents", json=_identity_payload(agent_id, name)
        )
        assert identity_response.status_code == 201

        account_response = await client.post(
            "/bank/accounts",
            json={
                "operation_id": operation_id,
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
        account_envelope = account_response.json()
        assert account_envelope["outcome"] == "native_receipt"
        assert account_envelope["receipt"]["operation_id"] == operation_id
        assert account_envelope["receipt"]["result"]["balance_after"] == 5000
        assert account_envelope["current_escrow"] is None

    task_id = "t-00000000-0000-4000-8000-000000000501"

    task_response = await publish_open_task(
        client,
        task_id=task_id,
        poster_id="a-poster",
        title="Task for feedback",
        spec="Do something",
        reward=200,
        escrow_id=f"esc-{task_id}",
        bidding_deadline_seconds=3600,
        acceptance_window_seconds=1800,
        execution_deadline_seconds=7200,
        review_deadline_seconds=3600,
    )
    assert task_response.status_code == 201
    await advance_task_to_submitted(
        client,
        task_id=task_id,
        poster_id="a-poster",
        worker_id="a-worker",
        escrow_id=f"esc-{task_id}",
        reward=200,
        operation_suffix="0000000303",
        timestamp="2026-03-02T05:40:00Z",
    )
    approval_claim, release_receipt, approval = await approve_with_bank_receipt(
        client,
        task_id=task_id,
        poster_id="a-poster",
        worker_id="a-worker",
        escrow_id=f"esc-{task_id}",
        reward=200,
        operation_id="op-00000000-0000-4000-8000-000000030304",
        timestamp="2026-03-02T05:40:00Z",
    )
    assert approval_claim.status_code == 202, approval_claim.text
    assert release_receipt.status_code == 200, release_receipt.text
    assert approval.status_code == 200, approval.text
    assert approval.json()["result"]["status"] == "approved"
    return task_id


@pytest_asyncio.fixture
async def gw_client(initialized_db: str, monkeypatch: pytest.MonkeyPatch):
    freeze_task_clock(monkeypatch, "2026-03-02T05:40:00Z")
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
                "source": "peer",
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
                "source": "peer",
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
                "source": "peer",
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
