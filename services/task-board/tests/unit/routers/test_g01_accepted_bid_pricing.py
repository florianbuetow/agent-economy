"""G01 accepted-bid pricing through the public Task Board API.

The posted reward is the escrowed budget; the accepted bid is the price. Approval pays
the price and refunds the unused budget in one Bank settlement; a Court ruling pays
floor(price * pct / 100) and refunds the rest of the original escrow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    accept_bid,
    create_task,
    file_dispute,
    make_task_id,
    new_operation_fields,
    submit_bid,
    submit_deliverable,
    submit_ruling,
    upload_asset,
)

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = pytest.mark.unit


def _balance(app: Any, account_id: str) -> int:
    row = app.state.gateway_store.writer.connection.execute(
        "SELECT balance FROM bank_accounts WHERE account_id=?", (account_id,)
    ).fetchone()
    assert row is not None
    return int(row[0])


def _ledger_rows(app: Any) -> int:
    row = app.state.gateway_store.writer.connection.execute(
        "SELECT (SELECT COUNT(*) FROM bank_transactions)"
        " + (SELECT COUNT(*) FROM bank_operation_receipts)"
        " + (SELECT COUNT(*) FROM bank_operation_legs)"
    ).fetchone()
    return int(row[0])


async def _submitted(
    client: AsyncClient,
    poster: tuple[Any, str],
    poster_id: str,
    worker: tuple[Any, str],
    worker_id: str,
    bid_amount: int,
) -> str:
    task_id = make_task_id()
    created = await create_task(client, poster, poster_id, task_id=task_id, reward=100)
    assert created.status_code == 201, created.text
    bid = await submit_bid(client, worker, worker_id, task_id, amount=bid_amount)
    assert bid.status_code == 201, bid.text
    accepted = await accept_bid(client, poster, poster_id, task_id, bid.json()["bid_id"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted_price"] == bid_amount
    assert accepted.json()["reward"] == 100
    uploaded = await upload_asset(client, worker, worker_id, task_id)
    assert uploaded.status_code == 201, uploaded.text
    submitted = await submit_deliverable(client, worker, worker_id, task_id)
    assert submitted.status_code == 200, submitted.text
    return task_id


async def test_approve_budget_100_bid_60_pays_60_refunds_40_and_retry_adds_nothing(
    app: Any,
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    bob_keypair: Any,
    bob_agent_id: str,
) -> None:
    """Approval pays the accepted price; an exact retry of the request is a pure replay."""
    poster_before = _balance(app, alice_agent_id)
    worker_before = _balance(app, bob_agent_id)
    task_id = await _submitted(client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id, 60)
    payload = {
        "action": "approve_task",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "poster_id": alice_agent_id,
    }
    token = make_jws_token(alice_keypair[0], alice_agent_id, payload)

    approved = await client.post(f"/tasks/{task_id}/approve", json={"token": token})
    rows = _ledger_rows(app)
    replay = await client.post(f"/tasks/{task_id}/approve", json={"token": token})

    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert (body["reward"], body["accepted_price"]) == (100, 60)
    assert (body["worker_payment"], body["poster_refund"]) == (60, 40)
    assert body["operation"]["result"]["worker_payment"] == 60
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation"] == body["operation"]
    assert _ledger_rows(app) == rows
    assert _balance(app, bob_agent_id) - worker_before == 60
    assert poster_before - _balance(app, alice_agent_id) == 60


async def test_ruling_50_percent_on_price_61_pays_30_and_refunds_70(
    app: Any,
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    bob_keypair: Any,
    bob_agent_id: str,
    platform_keypair: Any,
    platform_agent_id: str,
) -> None:
    """The Court percentage applies to the accepted price, rounded down."""
    poster_before = _balance(app, alice_agent_id)
    worker_before = _balance(app, bob_agent_id)
    task_id = await _submitted(client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id, 61)
    disputed = await file_dispute(client, alice_keypair, alice_agent_id, task_id)
    assert disputed.status_code == 200, disputed.text

    ruled = await submit_ruling(client, platform_keypair, platform_agent_id, task_id, worker_pct=50)

    assert ruled.status_code == 200, ruled.text
    body = ruled.json()
    assert (body["accepted_price"], body["worker_payment"], body["poster_refund"]) == (61, 30, 70)
    assert _balance(app, bob_agent_id) - worker_before == 30
    assert poster_before - _balance(app, alice_agent_id) == 30


async def test_bid_above_the_posted_budget_is_rejected(
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    bob_keypair: Any,
    bob_agent_id: str,
) -> None:
    """Bids must fit the posted budget."""
    task_id = make_task_id()
    created = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id, reward=100)
    assert created.status_code == 201, created.text

    bid = await submit_bid(client, bob_keypair, bob_agent_id, task_id, amount=101)

    assert bid.status_code == 400, bid.text
    assert bid.json()["error"] == "bid_exceeds_budget"
