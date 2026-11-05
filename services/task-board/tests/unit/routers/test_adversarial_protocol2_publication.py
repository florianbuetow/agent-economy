"""Frozen protocol-2 input contracts reject changed bytes after uncertain publish."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state
from tests.unit.routers.test_u_poster_inputs import (
    ALICE_AGENT_ID,
    _admit,
    _publish,
    _upload,
    input_store,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

    from tests.fakes.input_gateway_store import InputGatewayStore

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# Imported fixture is intentionally exposed to this module.
__all__ = ["input_store"]


async def test_changed_input_bytes_cannot_modify_a_frozen_uncertain_publication(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    input_store: InputGatewayStore,
) -> None:
    """PUB-02: frozen input hash remains bound while publish outcome is unknown."""
    task_id, publication_id = await _admit(client, alice_keypair, 1)
    original = await _upload(
        client,
        alice_keypair,
        ALICE_AGENT_ID,
        publication_id,
        poster_id=ALICE_AGENT_ID,
        content=b"original input bytes\n",
    )
    assert original.status_code == 201
    state = get_app_state()
    assert state.central_bank_client is not None
    state.central_bank_client.escrow_lock = AsyncMock(
        side_effect=ServiceError(
            "central_bank_unavailable", "Injected unknown lock outcome", 502, {}
        )
    )

    pending = await _publish(client, alice_keypair, task_id, publication_id)
    assert pending.status_code == 202
    assert pending.json()["publication_state"] == "awaiting_lock"
    before = input_store.writer.connection.execute(
        "SELECT state,input_manifest_sha256,publication_contract_sha256 "
        "FROM board_task_publications WHERE operation_id=?",
        (publication_id,),
    ).fetchone()
    assert before is not None
    assert before[0] == "awaiting_lock"
    assert all(isinstance(value, str) and len(value) == 64 for value in before[1:])

    changed = await _upload(
        client,
        alice_keypair,
        ALICE_AGENT_ID,
        publication_id,
        poster_id=ALICE_AGENT_ID,
        content=b"changed input bytes\n",
    )
    assert changed.status_code == 409
    assert changed.json()["error"] == "invalid_status"
    after = input_store.writer.connection.execute(
        "SELECT state,input_manifest_sha256,publication_contract_sha256 "
        "FROM board_task_publications WHERE operation_id=?",
        (publication_id,),
    ).fetchone()
    assert after == before
    ready = [
        tuple(row)
        for row in input_store.writer.connection.execute(
            "SELECT content_sha256 FROM board_artifact_uploads "
            "WHERE publication_operation_id=? AND state='ready'",
            (publication_id,),
        ).fetchall()
    ]
    assert ready == [(original.json()["content_hash"],)]
    task_count = input_store.writer.connection.execute(
        "SELECT COUNT(*) FROM board_tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    escrow_count = input_store.writer.connection.execute(
        "SELECT COUNT(*) FROM bank_escrow WHERE task_id=?", (task_id,)
    ).fetchone()
    assert task_count is not None and tuple(task_count) == (0,)
    assert escrow_count is not None and tuple(escrow_count) == (0,)
