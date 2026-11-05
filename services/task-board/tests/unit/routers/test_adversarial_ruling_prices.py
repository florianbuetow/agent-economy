"""Adversarial accepted-price ruling payout matrix."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.unit.routers.conftest import file_dispute, submit_ruling
from tests.unit.routers.test_g01_accepted_bid_pricing import _balance, _submitted

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("bid_amount", "worker_pct", "worker_payment", "poster_refund"),
    [
        (60, 0, 0, 100),
        (60, 50, 30, 70),
        (60, 100, 60, 40),
        (61, 33, 20, 80),
        (61, 67, 40, 60),
    ],
)
async def test_ruling_percentage_uses_accepted_price_and_floors_exactly(
    app: Any,
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    bob_keypair: Any,
    bob_agent_id: str,
    platform_keypair: Any,
    platform_agent_id: str,
    bid_amount: int,
    worker_pct: int,
    worker_payment: int,
    poster_refund: int,
) -> None:
    """Court percentages apply to price while unused budget returns to poster."""
    poster_before = _balance(app, alice_agent_id)
    worker_before = _balance(app, bob_agent_id)
    task_id = await _submitted(
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        bid_amount,
    )
    disputed = await file_dispute(client, alice_keypair, alice_agent_id, task_id)
    assert disputed.status_code == 200, disputed.text

    ruled = await submit_ruling(
        client,
        platform_keypair,
        platform_agent_id,
        task_id,
        worker_pct=worker_pct,
    )

    assert ruled.status_code == 200, ruled.text
    body = ruled.json()
    assert (body["accepted_price"], body["worker_payment"], body["poster_refund"]) == (
        bid_amount,
        worker_payment,
        poster_refund,
    )
    assert _balance(app, bob_agent_id) - worker_before == worker_payment
    assert poster_before - _balance(app, alice_agent_id) == worker_payment
    assert worker_payment + poster_refund == 100
