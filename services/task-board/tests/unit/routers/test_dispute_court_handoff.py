"""Dispute-to-Court handoff tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state
from tests.unit.routers.conftest import file_dispute, setup_task_in_review


@pytest.mark.unit
async def test_dispute_auto_files_court_claim(
    client,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
) -> None:
    task_id = await setup_task_in_review(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    task = await get_app_state().task_manager.get_task(task_id)
    state = get_app_state()
    state.platform_agent.file_claim = AsyncMock(
        return_value={"dispute_id": "disp-1", "status": "rebuttal_pending"}
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
    state.platform_agent.file_claim.assert_awaited_once_with(
        task_id=task_id,
        claimant_id=alice_agent_id,
        respondent_id=bob_agent_id,
        claim="The answer is not correct.",
        escrow_id=task["escrow_id"],
    )
