"""T-072 coverage sweep: closes several FB-* / SEC-03 gaps found against
docs/specifications/service-tests/reputation-service-tests.md.

Each of these behaviors is already proven correct at the business-logic layer
(``tests/unit/test_feedback_service.py``) but was never exercised end-to-end
through the router / JWS envelope:

- FB-02: spec_quality + extremely_satisfied combination
- FB-05: empty-string comment
- FB-08: same agent pair, different task is not treated as a duplicate
- FB-11: comment at exactly the configured max length (256)
- FB-16: wrong field types return invalid_field_type
- FB-21: all three rating values, three distinct tasks
- FB-24: real Unicode (emoji/CJK/accented) survives a round trip verbatim
  (the persistence suite's "unicode_comment" fixture is plain ASCII, so this
  was never actually exercised anywhere)
- SEC-03: feedback IDs are opaque fb-<uuid4>, checked for 5+ records
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from reputation_service.core.state import get_app_state
from tests.helpers import make_jws_token, make_mock_platform_agent

if TYPE_CHECKING:
    from httpx import AsyncClient

ALICE_ID = "a-alice-uuid"
BOB_ID = "a-bob-uuid"


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
async def test_fb_02_spec_quality_extremely_satisfied(client: AsyncClient) -> None:
    """FB-02: spec_quality category with extremely_satisfied rating round-trips."""
    payload = _feedback_payload(
        task_id="t-fb02",
        from_agent_id=BOB_ID,
        to_agent_id=ALICE_ID,
        category="spec_quality",
        rating="extremely_satisfied",
        comment="Very clear spec",
    )
    _inject_mock(payload)
    response = await client.post("/feedback", json=_token_body(payload, kid=BOB_ID))

    assert response.status_code == 201
    data = response.json()
    assert data["category"] == "spec_quality"
    assert data["rating"] == "extremely_satisfied"
    # FB-01's field-shape requirement: the internal-only `role` column is
    # never serialized on the response, regardless of category.
    assert "role" not in data


@pytest.mark.unit
async def test_fb_05_empty_string_comment_is_preserved(client: AsyncClient) -> None:
    """FB-05: an explicit empty-string comment round-trips as ''."""
    payload = _feedback_payload(task_id="t-fb05", comment="")
    _inject_mock(payload)
    response = await client.post("/feedback", json=_token_body(payload))

    assert response.status_code == 201
    assert response.json()["comment"] == ""


@pytest.mark.unit
async def test_fb_08_same_agents_different_task_is_allowed(client: AsyncClient) -> None:
    """FB-08: (alice, bob) feedback on a second task is not a duplicate."""
    payload_1 = _feedback_payload(task_id="t-fb08-a")
    _inject_mock(payload_1)
    resp1 = await client.post("/feedback", json=_token_body(payload_1))
    assert resp1.status_code == 201

    payload_2 = _feedback_payload(task_id="t-fb08-b")
    _inject_mock(payload_2)
    resp2 = await client.post("/feedback", json=_token_body(payload_2))
    assert resp2.status_code == 201

    assert resp1.json()["feedback_id"] != resp2.json()["feedback_id"]


@pytest.mark.unit
async def test_fb_11_comment_at_exactly_max_length_is_accepted(client: AsyncClient) -> None:
    """FB-11: a 256-character comment (the configured max) is accepted."""
    payload = _feedback_payload(task_id="t-fb11", comment="x" * 256)
    _inject_mock(payload)
    response = await client.post("/feedback", json=_token_body(payload))

    assert response.status_code == 201
    assert response.json()["comment"] == "x" * 256


@pytest.mark.unit
async def test_fb_16_wrong_field_types_return_invalid_field_type(client: AsyncClient) -> None:
    """FB-16: a wrong-typed required field returns 400 invalid_field_type."""
    payload = _feedback_payload(task_id=123)
    _inject_mock(payload)
    response = await client.post("/feedback", json=_token_body(payload))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_field_type"


@pytest.mark.unit
async def test_fb_21_all_three_rating_values_are_accepted(client: AsyncClient) -> None:
    """FB-21: dissatisfied, satisfied, and extremely_satisfied all round-trip."""
    for index, rating in enumerate(("dissatisfied", "satisfied", "extremely_satisfied")):
        payload = _feedback_payload(task_id=f"t-fb21-{index}", rating=rating)
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 201
        assert response.json()["rating"] == rating


@pytest.mark.unit
async def test_fb_24_unicode_comment_is_preserved_exactly(client: AsyncClient) -> None:
    """FB-24: emoji, CJK, and accented characters survive a round trip verbatim."""
    unicode_comment = "Great work! 素晴らしい仕事 🎉 café"
    payload = _feedback_payload(task_id="t-fb24", comment=unicode_comment)
    _inject_mock(payload)
    response = await client.post("/feedback", json=_token_body(payload))

    assert response.status_code == 201
    assert response.json()["comment"] == unicode_comment


@pytest.mark.unit
async def test_sec_03_feedback_ids_are_opaque_uuid4(client: AsyncClient) -> None:
    """SEC-03: every feedback_id matches fb-<uuid4> across 5+ records."""
    feedback_ids: list[str] = []
    for index in range(5):
        payload = _feedback_payload(
            task_id=f"t-sec03-{index}",
            from_agent_id=ALICE_ID,
            to_agent_id=BOB_ID,
        )
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 201
        feedback_ids.append(response.json()["feedback_id"])

    assert len(feedback_ids) == len({*feedback_ids})
    for feedback_id in feedback_ids:
        assert feedback_id.startswith("fb-")
        parsed = uuid.UUID(feedback_id.removeprefix("fb-"))
        assert parsed.version == 4
