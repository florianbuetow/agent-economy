"""Equal display names never merge cryptographic identities or economic history."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.financial_identity import operation_id_for

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig, TokenPolicy

if TYPE_CHECKING:
    from pathlib import Path


def _agent(name: str, private_key: Ed25519PrivateKey) -> BaseAgent:
    return BaseAgent(
        AgentConfig(
            name=name,
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url=os.environ["IDENTITY_URL"],
            bank_url=os.environ["BANK_URL"],
            task_board_url=os.environ["TASK_BOARD_URL"],
            reputation_url=os.environ["REPUTATION_URL"],
            court_url=os.environ["COURT_URL"],
            token_ttl_seconds=300,
            token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
        )
    )


def _version(task: dict[str, object]) -> int:
    value = task["lifecycle_version"]
    assert type(value) is int
    return value


@pytest.mark.e2e
async def test_same_display_name_keeps_balances_bids_histories_and_permissions_separate(
    platform_agent,
    tmp_path: Path,
) -> None:
    """IDN-03: only key-backed agent IDs authorize and aggregate activity."""
    del tmp_path
    poster = _agent("Same Display Name", Ed25519PrivateKey.generate())
    worker = _agent("Same Display Name", Ed25519PrivateKey.generate())
    try:
        poster_id = (await poster.register())["agent_id"]
        worker_id = (await worker.register())["agent_id"]
        assert poster_id != worker_id
        for agent in (poster, worker):
            await agent.create_account()
        for agent_id, amount in ((poster_id, 200), (worker_id, 20)):
            reference = f"same-name-{agent_id}"
            await platform_agent.credit_account(
                account_id=agent_id,
                amount=amount,
                reference=reference,
                operation_id=operation_id_for("e2e-credit", (agent_id, reference)),
            )
        intent = poster.prepare_task_posting(
            title="Same-name identity separation",
            spec="Accept only the independently keyed worker.",
            reward=100,
            bidding_deadline_seconds=3_600,
            acceptance_window_seconds=3_600,
            execution_deadline_seconds=7_200,
            review_deadline_seconds=3_600,
        )
        assert (await poster.submit_task_posting(intent)).status_code == 201
        opened = await worker.get_task(intent.task_id)
        bid = await worker.submit_bid(
            intent.task_id,
            60,
            operation_id=f"op-{uuid.uuid4()}",
            expected_lifecycle_version=_version(opened),
        )
        assert bid.status_code == 201
        with pytest.raises(httpx.HTTPStatusError) as sealed:
            await worker.list_bids(intent.task_id)
        assert sealed.value.response.status_code == 403
        poster_bids = await poster.list_bids(intent.task_id)
        assert [(item["bidder_id"], item["amount"]) for item in poster_bids] == [(worker_id, 60)]
        with pytest.raises(httpx.HTTPStatusError) as forbidden:
            await worker.cancel_task(
                intent.task_id,
                operation_id=f"op-{uuid.uuid4()}",
                expected_lifecycle_version=0,
            )
        assert forbidden.value.response.status_code == 403
        accepted = await poster.accept_bid(
            intent.task_id,
            bid.body["bid_id"],
            operation_id=f"op-{uuid.uuid4()}",
            expected_lifecycle_version=_version(opened),
        )
        assert accepted.status_code == 200

        assert int((await poster.get_balance())["balance"]) == 100
        assert int((await worker.get_balance())["balance"]) == 20
        async with httpx.AsyncClient(base_url=os.environ["UI_URL"]) as ui:
            poster_profile = (await ui.get(f"/api/agents/{poster_id}")).json()
            worker_profile = (await ui.get(f"/api/agents/{worker_id}")).json()
        assert poster_profile["name"] == worker_profile["name"] == "Same Display Name"
        assert poster_profile["agent_id"] == poster_id
        assert worker_profile["agent_id"] == worker_id
        assert poster_profile["balance"] == 100
        assert worker_profile["balance"] == 20
        assert poster_profile["stats"]["tasks_posted"] == 1
        assert worker_profile["stats"]["tasks_posted"] == 0
        assert any(
            task["task_id"] == intent.task_id and task["role"] == "poster"
            for task in poster_profile["recent_tasks"]
        )
        assert any(
            task["task_id"] == intent.task_id and task["role"] == "worker"
            for task in worker_profile["recent_tasks"]
        )
    finally:
        await worker.close()
        await poster.close()
