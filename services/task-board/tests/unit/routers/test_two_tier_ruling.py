"""Two-tier auth rollout (WP-03) — task-board ``record_ruling``.

``record_ruling`` is a platform operation (R9): it must verify the ruling token locally
via the platform agent and therefore keep working while the Identity service is down.
Agent operations still route verification through Identity and fail cleanly when it is
unreachable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state
from tests.unit.routers.conftest import create_task, setup_task_in_dispute, submit_ruling

if TYPE_CHECKING:
    from httpx import AsyncClient


def _take_identity_down() -> None:
    """Make the Identity client raise a mapped 502, as it would when unreachable."""
    state = get_app_state()
    state.identity_client.verify_jws = AsyncMock(
        side_effect=ServiceError(
            "identity_service_unavailable",
            "Cannot reach Identity service",
            502,
            {},
        )
    )
    if state.token_validator is not None:
        state.token_validator._identity_client = state.identity_client


@pytest.mark.unit
async def test_record_ruling_succeeds_when_identity_down(
    client: AsyncClient,
    alice_keypair: object,
    alice_agent_id: str,
    bob_keypair: object,
    bob_agent_id: str,
    platform_keypair: object,
    platform_agent_id: str,
) -> None:
    """A platform-signed ruling is recorded via local verification while Identity is down."""
    task_id = await setup_task_in_dispute(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )

    # Identity goes down after the (agent-signed) dispute setup; the platform ruling lands.
    _take_identity_down()

    resp = await submit_ruling(
        client,
        platform_keypair,
        platform_agent_id,
        task_id,
        worker_pct=50,
        ruling_summary="Split ruling",
    )

    assert resp.status_code == 200
    assert resp.json()["worker_pct"] == 50


@pytest.mark.unit
async def test_agent_op_fails_cleanly_when_identity_down(
    client: AsyncClient,
    alice_keypair: object,
    alice_agent_id: str,
) -> None:
    """Guard: an agent op still routes via Identity and fails cleanly (502) when it is down."""
    _take_identity_down()

    resp = await create_task(client, alice_keypair, alice_agent_id)

    assert resp.status_code == 502
    assert resp.json()["error"] == "identity_service_unavailable"
