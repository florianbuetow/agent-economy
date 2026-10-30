"""Plan item 3.3: rebuttal replay is idempotent, changes conflict, late is rejected."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.unit.routers.conftest import (
    PLATFORM_AGENT_ID,
    expire_rebuttal_window,
    file_and_rebut,
    file_dispute,
    inject_identity_verify,
    rebuttal_payload,
    token_body,
)

if TYPE_CHECKING:
    from httpx import AsyncClient, Response

pytestmark = pytest.mark.unit

_TEXT = "The specification was ambiguous."


async def _post_rebuttal(client: AsyncClient, dispute_id: str, text: str) -> Response:
    payload = rebuttal_payload(dispute_id, rebuttal=text)
    inject_identity_verify(PLATFORM_AGENT_ID, payload)
    return await client.post(f"/disputes/{dispute_id}/rebuttal", json=token_body(payload))


async def test_identical_rebuttal_replay_returns_same_record(client: AsyncClient) -> None:
    dispute = await file_and_rebut(client, rebuttal_text=_TEXT)
    dispute_id = dispute["dispute_id"]
    before = (await client.get(f"/disputes/{dispute_id}")).json()

    response = await _post_rebuttal(client, dispute_id, _TEXT)

    assert response.status_code == 200
    body = response.json()
    assert body["rebuttal"] == _TEXT
    assert body["rebutted_at"] == before["rebutted_at"]


async def test_changed_rebuttal_returns_409(client: AsyncClient) -> None:
    dispute = await file_and_rebut(client, rebuttal_text=_TEXT)

    response = await _post_rebuttal(client, dispute["dispute_id"], "A different argument.")

    assert response.status_code == 409
    assert response.json()["error"] == "rebuttal_already_submitted"


async def test_rebuttal_after_deadline_is_rejected(client: AsyncClient) -> None:
    dispute = await file_dispute(client)
    dispute_id = dispute["dispute_id"]
    expire_rebuttal_window(dispute_id)

    response = await _post_rebuttal(client, dispute_id, _TEXT)

    assert response.status_code == 409
    assert response.json()["error"] == "rebuttal_deadline_passed"
    stored = (await client.get(f"/disputes/{dispute_id}")).json()
    assert stored["rebuttal"] is None
