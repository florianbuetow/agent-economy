"""Dispute-to-Court handoff tests."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from tests.unit.routers.conftest import file_dispute, setup_task_in_review
from tests.unit.routers.test_disputes import fixture_platform, stored_task

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi import FastAPI
    from httpx import AsyncClient


@pytest.mark.unit
async def test_dispute_auto_files_court_claim(
    app: FastAPI,
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    task_id = await setup_task_in_review(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    task = stored_task(task_id)
    fixture_platform().file_claim = AsyncMock(
        side_effect=partial(app.state.gateway_effects.persist_claim, claim_id="disp-1")
    )

    response = await file_dispute(
        client,
        alice_keypair,
        alice_agent_id,
        task_id,
        reason="The answer is not correct.",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "disputed"
    assert response.json()["dispute_id"] == "disp-1"
    fixture_platform().file_claim.assert_awaited_once_with(
        task_id=task_id,
        claimant_id=alice_agent_id,
        respondent_id=bob_agent_id,
        claim="The answer is not correct.",
        escrow_id=task["escrow_id"],
    )
