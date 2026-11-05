"""Adversarial governed-upload replay after an ambiguous acknowledgment."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.unit.routers.test_asset_read_authorization import (
    _keyed_upload,
    _list_headers,
    setup_task_in_execution,
)

if TYPE_CHECKING:
    from httpx import AsyncClient

    from tests.unit.routers.conftest import _Keypair

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_changed_bytes_under_a_committed_key_return_the_original_without_reading_body(
    client: AsyncClient,
    alice_keypair: _Keypair,
    alice_agent_id: str,
    bob_keypair: _Keypair,
    bob_agent_id: str,
) -> None:
    """Characterize AST-06's current opaque-key contract pending an owner decision."""
    task_id, _bid_id = await setup_task_in_execution(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    key = "ambiguous-upload-v1"
    committed = await _keyed_upload(
        client, bob_keypair, bob_agent_id, task_id, key, b"original bytes"
    )
    assert committed.status_code == 201

    # Treat the first response as lost: recovery knows only the immutable key/body.
    exact_retry = await _keyed_upload(
        client, bob_keypair, bob_agent_id, task_id, key, b"original bytes"
    )
    changed_retry = await _keyed_upload(
        client, bob_keypair, bob_agent_id, task_id, key, b"different bytes"
    )
    listing = await client.get(
        f"/tasks/{task_id}/assets",
        headers=_list_headers(bob_keypair, bob_agent_id, task_id),
    )

    assert exact_retry.status_code == 201
    assert exact_retry.json() == committed.json()
    assert changed_retry.status_code == 201
    assert changed_retry.json() == committed.json()
    assert len(listing.json()["assets"]) == 1
    assert listing.json()["assets"][0]["asset_id"] == committed.json()["asset_id"]
