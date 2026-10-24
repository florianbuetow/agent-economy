"""Malformed Court acknowledgment preserves the committed rebuttal projection."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from task_board_service.core.state import get_app_state
from tests.integration import test_mp07_rebuttal_bridge as bridge

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_malformed_rebuttal_ack_preserves_commit_and_returns_typed_502(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, _accepted, claim_id = await bridge.create_disputed_task(
        client, gateway_client, poster_keypair[0], worker_keypair[0], gateway_db_path
    )
    event_cursor = bridge.get_event_cursor(gateway_db_path)

    async def _commit_then_fail_decode(dispute_id: str, rebuttal: str) -> None:
        assert dispute_id == claim_id
        await bridge.persist_rebuttal(
            gateway_client, setup=setup, claim_id=claim_id, rebuttal=rebuttal
        )
        raise json.JSONDecodeError("Injected malformed Court acknowledgment", "not-json", 0)

    court_call = AsyncMock(side_effect=_commit_then_fail_decode)
    bridge.install_rebuttal_call(court_call)
    state = get_app_state()
    assert state.store is not None
    with patch.object(state.store, "update_task", wraps=state.store.update_task) as generic_update:
        response = await bridge.submit_public_rebuttal(client, worker_keypair[0], setup, claim_id)

    assert response.status_code == 502, response.text
    assert response.json() == {
        "error": "court_unavailable",
        "message": "Cannot connect to Court",
        "details": {},
    }
    court_call.assert_awaited_once_with(claim_id, bridge.REBUTTAL)
    bridge.assert_committed_projection(
        gateway_db_path,
        setup=setup,
        claim_id=claim_id,
        event_cursor=event_cursor,
        generic_status_calls=generic_update.call_count,
    )
