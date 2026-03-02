"""Integration tests: Court writes via DB Gateway to economy.db."""

from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from tests.integration.conftest import read_one
from tests.integration.gateway_helpers import create_gateway_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _identity_payload(agent_id: str, name: str) -> dict[str, object]:
    key_seed = agent_id.replace("a-", "").upper().ljust(43, "Q")
    timestamp = "2026-03-02T00:00:00Z"
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
    for agent_id, name in [("a-claimant", "Claimant"), ("a-respondent", "Respondent")]:
        identity_response = await client.post("/identity/agents", json=_identity_payload(agent_id, name))
        assert identity_response.status_code == 201

        account_response = await client.post(
            "/bank/accounts",
            json={
                "account_id": agent_id,
                "balance": 5000,
                "created_at": "2026-03-02T00:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-init-{agent_id}",
                    "amount": 5000,
                    "reference": "initial_balance",
                    "timestamp": "2026-03-02T00:00:00Z",
                },
                "event": {
                    "event_source": "bank",
                    "event_type": "account.created",
                    "timestamp": "2026-03-02T00:00:00Z",
                    "agent_id": agent_id,
                    "summary": f"Account for {name}",
                    "payload": "{}",
                },
            },
        )
        assert account_response.status_code == 201

    task_id = "t-court-001"

    lock_response = await client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": f"esc-{task_id}",
            "payer_account_id": "a-claimant",
            "amount": 200,
            "task_id": task_id,
            "created_at": "2026-03-02T00:00:16Z",
            "tx_id": f"tx-esc-{task_id}",
            "event": {
                "event_source": "bank",
                "event_type": "escrow.locked",
                "timestamp": "2026-03-02T00:00:16Z",
                "agent_id": "a-claimant",
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
            "poster_id": "a-claimant",
            "title": "Disputed Task",
            "spec": "Build something",
            "reward": 200,
            "status": "disputed",
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 7200,
            "review_deadline_seconds": 3600,
            "bidding_deadline": "2026-03-02T00:16:15Z",
            "bid_count": 1,
            "escrow_pending": 0,
            "escrow_id": f"esc-{task_id}",
            "worker_id": "a-respondent",
            "created_at": "2026-03-02T00:00:16Z",
            "event": {
                "event_source": "board",
                "event_type": "task.created",
                "timestamp": "2026-03-02T00:00:16Z",
                "agent_id": "a-claimant",
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


class TestCourtGatewayWrites:
    async def test_file_claim_writes_claim_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        task_id = await _setup_prerequisite_data(gw_client)

        response = await gw_client.post(
            "/court/claims",
            json={
                "claim_id": "clm-001",
                "task_id": task_id,
                "claimant_id": "a-claimant",
                "respondent_id": "a-respondent",
                "reason": "Work does not meet specification",
                "status": "filed",
                "rebuttal_deadline": "2026-03-02T06:30:00Z",
                "filed_at": "2026-03-02T00:08:07Z",
                "event": {
                    "event_source": "court",
                    "event_type": "claim.filed",
                    "timestamp": "2026-03-02T00:08:07Z",
                    "agent_id": "a-claimant",
                    "task_id": task_id,
                    "summary": "Claim filed",
                    "payload": json.dumps({"claim_id": "clm-001"}),
                },
            },
        )
        assert response.status_code == 201

        row = read_one(
            initialized_db,
            "SELECT task_id, claimant_id, respondent_id, reason, status "
            "FROM court_claims WHERE claim_id = ?",
            ("clm-001",),
        )
        assert row is not None
        assert row["task_id"] == task_id
        assert row["claimant_id"] == "a-claimant"
        assert row["respondent_id"] == "a-respondent"
        assert row["status"] == "filed"

    async def test_submit_rebuttal_writes_rebuttal_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        task_id = await _setup_prerequisite_data(gw_client)

        claim_response = await gw_client.post(
            "/court/claims",
            json={
                "claim_id": "clm-002",
                "task_id": task_id,
                "claimant_id": "a-claimant",
                "respondent_id": "a-respondent",
                "reason": "Incomplete work",
                "status": "filed",
                "rebuttal_deadline": "2026-03-02T06:30:00Z",
                "filed_at": "2026-03-02T00:08:23Z",
                "event": {
                    "event_source": "court",
                    "event_type": "claim.filed",
                    "timestamp": "2026-03-02T00:08:23Z",
                    "agent_id": "a-claimant",
                    "task_id": task_id,
                    "summary": "Claim filed",
                    "payload": "{}",
                },
            },
        )
        assert claim_response.status_code == 201

        rebuttal_response = await gw_client.post(
            "/court/rebuttals",
            json={
                "rebuttal_id": "reb-001",
                "claim_id": "clm-002",
                "agent_id": "a-respondent",
                "content": "I followed the specification exactly",
                "submitted_at": "2026-03-02T00:16:15Z",
                "event": {
                    "event_source": "court",
                    "event_type": "rebuttal.submitted",
                    "timestamp": "2026-03-02T00:16:15Z",
                    "agent_id": "a-respondent",
                    "task_id": task_id,
                    "summary": "Rebuttal submitted",
                    "payload": json.dumps({"claim_id": "clm-002"}),
                },
            },
        )
        assert rebuttal_response.status_code == 201

        row = read_one(
            initialized_db,
            "SELECT claim_id, agent_id, content FROM court_rebuttals WHERE rebuttal_id = ?",
            ("reb-001",),
        )
        assert row is not None
        assert row["claim_id"] == "clm-002"
        assert row["agent_id"] == "a-respondent"
        assert row["content"] == "I followed the specification exactly"

    async def test_record_ruling_writes_ruling_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        task_id = await _setup_prerequisite_data(gw_client)

        claim_response = await gw_client.post(
            "/court/claims",
            json={
                "claim_id": "clm-003",
                "task_id": task_id,
                "claimant_id": "a-claimant",
                "respondent_id": "a-respondent",
                "reason": "Missing features",
                "status": "filed",
                "rebuttal_deadline": "2026-03-02T06:30:00Z",
                "filed_at": "2026-03-02T00:08:40Z",
                "event": {
                    "event_source": "court",
                    "event_type": "claim.filed",
                    "timestamp": "2026-03-02T00:08:40Z",
                    "agent_id": "a-claimant",
                    "task_id": task_id,
                    "summary": "Claim filed",
                    "payload": "{}",
                },
            },
        )
        assert claim_response.status_code == 201

        ruling_response = await gw_client.post(
            "/court/rulings",
            json={
                "ruling_id": "rul-001",
                "claim_id": "clm-003",
                "task_id": task_id,
                "worker_pct": 70,
                "summary": "Worker completed most requirements but missed error handling",
                "judge_votes": json.dumps(
                    [
                        {"judge": "judge-1", "worker_pct": 70},
                        {"judge": "judge-2", "worker_pct": 65},
                        {"judge": "judge-3", "worker_pct": 75},
                    ]
                ),
                "ruled_at": "2026-03-02T00:32:30Z",
                "event": {
                    "event_source": "court",
                    "event_type": "ruling.delivered",
                    "timestamp": "2026-03-02T00:32:30Z",
                    "task_id": task_id,
                    "summary": "Ruling delivered",
                    "payload": json.dumps({"ruling_id": "rul-001", "worker_pct": 70}),
                },
            },
        )
        assert ruling_response.status_code == 201

        row = read_one(
            initialized_db,
            "SELECT claim_id, task_id, worker_pct, summary, judge_votes "
            "FROM court_rulings WHERE ruling_id = ?",
            ("rul-001",),
        )
        assert row is not None
        assert row["claim_id"] == "clm-003"
        assert row["task_id"] == task_id
        assert row["worker_pct"] == 70
        assert "most requirements" in row["summary"]
        assert json.loads(row["judge_votes"])[0]["judge"] == "judge-1"
