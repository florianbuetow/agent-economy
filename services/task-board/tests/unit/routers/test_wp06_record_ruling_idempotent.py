"""WP-06.2 (T-040/GAP-A4): record_ruling is idempotent for a retried ruling.

Re-recording the identical ruling_id on an already-ruled task must converge to 200
with exactly one escrow settlement, so a Court retry after a partial failure does not
double-settle or dead-end on invalid_status. A different ruling_id on a ruled task is
still a conflict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from task_board_service.core.state import get_app_state
from tests.unit.routers.conftest import make_jws_token, setup_task_in_dispute

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient


async def _record_ruling(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    platform_id: str,
    task_id: str,
    *,
    ruling_id: str,
    worker_pct: int = 50,
) -> Any:
    payload = {
        "action": "record_ruling",
        "task_id": task_id,
        "ruling_id": ruling_id,
        "worker_pct": worker_pct,
        "ruling_summary": "Split ruling",
    }
    token = make_jws_token(platform_keypair[0], platform_id, payload)
    return await client.post(f"/tasks/{task_id}/ruling", json={"token": token})


@pytest.mark.unit
async def test_identical_ruling_id_rerecord_is_idempotent(
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    bob_keypair: Any,
    bob_agent_id: str,
    platform_keypair: Any,
    platform_agent_id: str,
) -> None:
    """Re-recording the same ruling_id on a ruled task -> 200, one settlement."""
    task_id = await setup_task_in_dispute(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    state = get_app_state()
    split_mock = state.central_bank_client.escrow_split

    first = await _record_ruling(
        client, platform_keypair, platform_agent_id, task_id, ruling_id="rul-fixed"
    )
    assert first.status_code == 200
    assert first.json()["status"] == "ruled"
    assert split_mock.await_count == 1

    second = await _record_ruling(
        client, platform_keypair, platform_agent_id, task_id, ruling_id="rul-fixed"
    )
    assert second.status_code == 200
    assert second.json()["status"] == "ruled"
    assert second.json()["ruling_id"] == "rul-fixed"
    # Exactly one ledger settlement despite the retry.
    assert split_mock.await_count == 1


@pytest.mark.unit
async def test_different_ruling_id_on_ruled_task_conflicts(
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    bob_keypair: Any,
    bob_agent_id: str,
    platform_keypair: Any,
    platform_agent_id: str,
) -> None:
    """A different ruling_id on an already-ruled task stays a 409 conflict."""
    task_id = await setup_task_in_dispute(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    first = await _record_ruling(
        client, platform_keypair, platform_agent_id, task_id, ruling_id="rul-one"
    )
    assert first.status_code == 200

    second = await _record_ruling(
        client, platform_keypair, platform_agent_id, task_id, ruling_id="rul-two"
    )
    assert second.status_code == 409
    assert second.json()["error"] == "invalid_status"
