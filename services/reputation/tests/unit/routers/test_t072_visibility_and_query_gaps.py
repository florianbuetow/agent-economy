"""T-072 coverage sweep: closes several visibility/query gaps found against
docs/specifications/service-tests/reputation-service-tests.md.

- AGENT-02: feedback about the same target agent from two different tasks
  (and two different senders) both appear in the agent query.
- READ-05: two consecutive reads of a revealed record are byte-identical.
- VIS-08: revealing one task's feedback does not affect an unrelated,
  still-sealed task.
- VIS-10: after a reveal-completing write races with a concurrent read, the
  *settled* state (read once the race resolves) is definitively 2 visible
  entries -- extends ``test_persistence.py::test_db_02_concurrent_mutual_
  reveal_atomic``, which only asserts the mid-race snapshot is "0 or 2" and
  never checks the final state after the race resolves.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from reputation_service.core.state import get_app_state
from tests.helpers import make_jws_token, make_mock_platform_agent

if TYPE_CHECKING:
    from httpx import AsyncClient

ALICE_ID = "a-alice-uuid"
BOB_ID = "a-bob-uuid"
CAROL_ID = "a-carol-uuid"


def _feedback_payload(**overrides: object) -> dict[str, object]:
    """Return a valid JWS payload for feedback."""
    base: dict[str, object] = {
        "action": "submit_feedback",
        "task_id": "t-gap-1",
        "from_agent_id": ALICE_ID,
        "to_agent_id": BOB_ID,
        "category": "delivery_quality",
        "rating": "satisfied",
        "comment": "Good work",
    }
    base.update(overrides)
    return base


def _token_body(payload: dict[str, object], kid: str = ALICE_ID) -> dict[str, object]:
    return {"token": make_jws_token(payload, kid=kid)}


def _inject_mock(payload: dict[str, object]) -> None:
    """Inject a mock platform agent that verifies exactly `payload`."""
    state = get_app_state()
    state.platform_agent = make_mock_platform_agent(verify_payload=payload)


@pytest.mark.unit
async def test_agent_02_feedback_about_agent_from_multiple_tasks(client: AsyncClient) -> None:
    """AGENT-02: feedback about bob from two different tasks/senders both appear."""
    payload_ab = _feedback_payload(
        task_id="t-agent02-1", from_agent_id=ALICE_ID, to_agent_id=BOB_ID
    )
    _inject_mock(payload_ab)
    await client.post("/feedback", json=_token_body(payload_ab, kid=ALICE_ID))

    payload_ba = _feedback_payload(
        task_id="t-agent02-1", from_agent_id=BOB_ID, to_agent_id=ALICE_ID
    )
    _inject_mock(payload_ba)
    await client.post("/feedback", json=_token_body(payload_ba, kid=BOB_ID))

    payload_cb = _feedback_payload(
        task_id="t-agent02-2", from_agent_id=CAROL_ID, to_agent_id=BOB_ID
    )
    _inject_mock(payload_cb)
    await client.post("/feedback", json=_token_body(payload_cb, kid=CAROL_ID))

    payload_bc = _feedback_payload(
        task_id="t-agent02-2", from_agent_id=BOB_ID, to_agent_id=CAROL_ID
    )
    _inject_mock(payload_bc)
    await client.post("/feedback", json=_token_body(payload_bc, kid=BOB_ID))

    response = await client.get(f"/feedback/agent/{BOB_ID}")

    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == BOB_ID
    assert len(data["feedback"]) == 2
    senders = {f["from_agent_id"] for f in data["feedback"]}
    assert senders == {ALICE_ID, CAROL_ID}


@pytest.mark.unit
async def test_read_05_idempotent_read_returns_identical_response(client: AsyncClient) -> None:
    """READ-05: two consecutive GETs of a revealed record are byte-identical."""
    payload_ab = _feedback_payload(task_id="t-read05")
    _inject_mock(payload_ab)
    resp1 = await client.post("/feedback", json=_token_body(payload_ab))
    feedback_id = resp1.json()["feedback_id"]

    payload_ba = _feedback_payload(task_id="t-read05", from_agent_id=BOB_ID, to_agent_id=ALICE_ID)
    _inject_mock(payload_ba)
    await client.post("/feedback", json=_token_body(payload_ba, kid=BOB_ID))

    first = await client.get(f"/feedback/{feedback_id}")
    second = await client.get(f"/feedback/{feedback_id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.text == second.text


@pytest.mark.unit
async def test_vis_08_revealing_one_task_does_not_affect_another(client: AsyncClient) -> None:
    """VIS-08: revealing task_1 does not reveal an unrelated sealed task_2."""
    payload_1a = _feedback_payload(task_id="t-vis08-1")
    _inject_mock(payload_1a)
    await client.post("/feedback", json=_token_body(payload_1a))

    payload_1b = _feedback_payload(task_id="t-vis08-1", from_agent_id=BOB_ID, to_agent_id=ALICE_ID)
    _inject_mock(payload_1b)
    await client.post("/feedback", json=_token_body(payload_1b, kid=BOB_ID))

    payload_2 = _feedback_payload(task_id="t-vis08-2")
    _inject_mock(payload_2)
    await client.post("/feedback", json=_token_body(payload_2))

    revealed = await client.get("/feedback/task/t-vis08-1")
    assert len(revealed.json()["feedback"]) == 2

    sealed = await client.get("/feedback/task/t-vis08-2")
    assert sealed.json()["feedback"] == []


@pytest.mark.unit
async def test_vis_10_concurrent_mutual_reveal_settles_to_both_visible(
    client: AsyncClient,
) -> None:
    """VIS-10: the settled state after a reveal-write races a read is
    definitively 2 visible entries -- never 'both still sealed'."""
    payload_ab = _feedback_payload(task_id="t-vis10")
    _inject_mock(payload_ab)
    resp1 = await client.post("/feedback", json=_token_body(payload_ab))
    assert resp1.status_code == 201
    assert resp1.json()["visible"] is False
    sealed_id = resp1.json()["feedback_id"]

    payload_ba = _feedback_payload(task_id="t-vis10", from_agent_id=BOB_ID, to_agent_id=ALICE_ID)
    _inject_mock(payload_ba)

    post_resp, _get_resp = await asyncio.gather(
        client.post("/feedback", json=_token_body(payload_ba, kid=BOB_ID)),
        client.get("/feedback/task/t-vis10"),
    )
    assert post_resp.status_code == 201
    assert post_resp.json()["visible"] is True

    # Definitive settled state, read once the race has resolved.
    final = await client.get("/feedback/task/t-vis10")
    assert final.status_code == 200
    entries = final.json()["feedback"]
    assert len(entries) == 2
    assert all(entry["visible"] is True for entry in entries)

    direct = await client.get(f"/feedback/{sealed_id}")
    assert direct.status_code == 200
