"""GAP-A6 — court/platform force_visible semantics (WP-07).

WP-03 already covers the AUTH side of this path: a platform-signed token verifies
locally and survives an Identity outage
(``tests/unit/routers/test_two_tier_feedback_auth.py``). What was never exercised is
the SEMANTICS a caller sees once that verification succeeds and ``force_visible``
reaches the store (plan §2.7 / §5 WP-07):

1. The record is stored immediately visible -- no sealing, regardless of whether a
   reverse pair exists.
2. ``from_agent_id`` on the stored/returned record is the platform's own id (the
   router pins it via ``is_platform`` at ``routers/feedback.py`` -- see
   ``_select_verifier``/``is_platform`` there).
3. Category convention: court's ``ruling_orchestrator._record_feedback`` always sends
   ``spec_quality`` addressed to the claimant/poster and ``delivery_quality``
   addressed to the respondent/worker
   (``court_service/services/ruling_orchestrator.py:265-282``). Reputation itself has
   no notion of poster/worker -- it just stores whatever (category, to_agent_id) pair
   the platform sends, immediately visible. This test documents that convention by
   replaying the exact shape court sends.
4. Critically: a force-visible write must not disturb an unrelated, still-sealed
   ordinary feedback pair on the same task.

Several of these assertions pass against the current implementation without any
production change -- that is expected and noted per-test below (semantics
documentation, not a bug fix). Each such assertion is backed by a manual mutation
check (see WP-07 report) proving the guard it documents is load-bearing, not
accidental.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from reputation_service.core.state import get_app_state
from tests.helpers import make_jws_token, make_mock_platform_agent

if TYPE_CHECKING:
    from httpx import AsyncClient

PLATFORM_ID = "a-platform-uuid"
WORKER_ID = "a-worker-uuid"
POSTER_ID = "a-poster-uuid"


def _payload(**overrides: object) -> dict[str, object]:
    """Return a valid submit_feedback JWS payload."""
    base: dict[str, object] = {
        "action": "submit_feedback",
        "task_id": "task-a6",
        "from_agent_id": WORKER_ID,
        "to_agent_id": POSTER_ID,
        "category": "spec_quality",
        "rating": "satisfied",
        "comment": "Good work",
    }
    base.update(overrides)
    return base


def _token_body(payload: dict[str, object], kid: str) -> dict[str, object]:
    return {"token": make_jws_token(payload, kid=kid)}


def _inject_mock(payload: dict[str, object], *, platform_id: str | None = None) -> None:
    """Inject a mock platform agent that verifies exactly `payload`.

    Mirrors ``tests/unit/routers/test_feedback.py::_inject_mock``: the router's
    verifier (whichever of platform_verifier/identity_client is selected) always
    calls ``state.platform_agent.validate_certificate``, so pointing that single mock
    at the payload under test is enough for both the platform and the ordinary-agent
    paths.

    ``platform_id`` additionally sets the mock's ``agent_id`` so the router's
    ``is_platform`` check (kid == platform_agent.agent_id) can be exercised.
    """
    state = get_app_state()
    mock_agent = make_mock_platform_agent(verify_payload=payload)
    if platform_id is not None:
        mock_agent.agent_id = platform_id
    state.platform_agent = mock_agent


@pytest.mark.unit
class TestForceVisibleImmediateVisibility:
    """Behavior 1 + 2: force_visible stores immediately visible, from=platform."""

    async def test_platform_feedback_with_no_counterpart_is_immediately_visible(
        self, client: AsyncClient
    ) -> None:
        """Passes immediately: force_visible bypasses sealing at the router/store
        boundary today (H-7, db_writer.py `force_visible = bool(data.get(...))`).
        Documented here at the reputation-service level, which had zero coverage of
        this specific case (WP-03's test only covers delivery_quality)."""
        payload = _payload(
            task_id="task-a6-visible",
            from_agent_id=PLATFORM_ID,
            to_agent_id=POSTER_ID,
            category="spec_quality",
            rating="dissatisfied",
            comment="Court ruling: spec was ambiguous",
        )
        _inject_mock(payload, platform_id=PLATFORM_ID)

        response = await client.post("/feedback", json=_token_body(payload, kid=PLATFORM_ID))

        assert response.status_code == 201
        body = response.json()
        assert body["visible"] is True
        assert body["from_agent_id"] == PLATFORM_ID

    async def test_ordinary_agent_feedback_with_no_counterpart_stays_sealed(
        self, client: AsyncClient
    ) -> None:
        """Control case: without a platform signer, the same shape stays sealed.

        This is the counter-test that makes the previous assertion meaningful --
        immediate visibility is specific to force_visible, not to "first feedback on
        a task" in general.
        """
        payload = _payload(
            task_id="task-a6-sealed-control",
            from_agent_id=WORKER_ID,
            to_agent_id=POSTER_ID,
            category="spec_quality",
        )
        _inject_mock(payload)

        response = await client.post("/feedback", json=_token_body(payload, kid=WORKER_ID))

        assert response.status_code == 201
        assert response.json()["visible"] is False


@pytest.mark.unit
class TestCourtRulingCategoryConvention:
    """Behavior 3: spec_quality -> poster/claimant, delivery_quality -> worker/respondent.

    Replays the exact payload shape court_service.ruling_orchestrator sends after a
    ruling (two force_visible submissions, same task, same platform signer, opposite
    categories/targets). Passes immediately -- reputation stores whatever category/
    to_agent_id pair it is given; the convention itself is enforced by the caller
    (court), not by reputation. This test documents and pins that contract from the
    reputation side so a future change to either side is caught here too.
    """

    async def test_platform_ruling_feedback_pair_matches_court_convention(
        self, client: AsyncClient
    ) -> None:
        task_id = "task-a6-ruling"

        spec_payload = _payload(
            task_id=task_id,
            from_agent_id=PLATFORM_ID,
            to_agent_id=POSTER_ID,
            category="spec_quality",
            rating="satisfied",
            comment="Ruling: spec was clear",
        )
        _inject_mock(spec_payload, platform_id=PLATFORM_ID)
        spec_resp = await client.post("/feedback", json=_token_body(spec_payload, kid=PLATFORM_ID))
        assert spec_resp.status_code == 201
        spec_body = spec_resp.json()
        assert spec_body["visible"] is True
        assert spec_body["from_agent_id"] == PLATFORM_ID
        assert spec_body["to_agent_id"] == POSTER_ID
        assert spec_body["category"] == "spec_quality"

        delivery_payload = _payload(
            task_id=task_id,
            from_agent_id=PLATFORM_ID,
            to_agent_id=WORKER_ID,
            category="delivery_quality",
            rating="dissatisfied",
            comment="Ruling: delivery fell short",
        )
        _inject_mock(delivery_payload, platform_id=PLATFORM_ID)
        delivery_resp = await client.post(
            "/feedback", json=_token_body(delivery_payload, kid=PLATFORM_ID)
        )
        assert delivery_resp.status_code == 201
        delivery_body = delivery_resp.json()
        assert delivery_body["visible"] is True
        assert delivery_body["from_agent_id"] == PLATFORM_ID
        assert delivery_body["to_agent_id"] == WORKER_ID
        assert delivery_body["category"] == "delivery_quality"

        # Both are immediately readable -- no reveal-timeout wait for court feedback.
        task_resp = await client.get(f"/feedback/task/{task_id}")
        ids = {f["feedback_id"] for f in task_resp.json()["feedback"]}
        assert {spec_body["feedback_id"], delivery_body["feedback_id"]} == ids


@pytest.mark.unit
class TestForceVisibleDoesNotInterfereWithSealedPairs:
    """Behavior 4 (critical): a force-visible write must not reveal or otherwise
    disturb an unrelated, still-sealed ordinary feedback pair on the same task.

    Passes immediately -- the reverse-pair lookup is scoped by the exact
    (task_id, from_agent_id, to_agent_id) triple, and a platform write's
    from_agent_id is always the platform, never a real task participant. Mutation
    check (see WP-07 report): broadening that scoping to task_id alone makes this
    test fail, proving the scoping is load-bearing.
    """

    async def test_platform_write_does_not_reveal_an_unrelated_sealed_pair(
        self, client: AsyncClient
    ) -> None:
        task_id = "task-a6-interference"

        # Ordinary, one-sided feedback: worker rates the poster's spec. No
        # counterpart has been submitted yet, so it must be sealed.
        sealed_payload = _payload(
            task_id=task_id,
            from_agent_id=WORKER_ID,
            to_agent_id=POSTER_ID,
            category="spec_quality",
            rating="satisfied",
        )
        _inject_mock(sealed_payload)
        sealed_resp = await client.post(
            "/feedback", json=_token_body(sealed_payload, kid=WORKER_ID)
        )
        assert sealed_resp.status_code == 201
        assert sealed_resp.json()["visible"] is False
        sealed_id = sealed_resp.json()["feedback_id"]

        # Platform force-visible ruling feedback on the SAME task, a disjoint
        # (from, to) pair: platform -> poster, not worker -> poster.
        platform_payload = _payload(
            task_id=task_id,
            from_agent_id=PLATFORM_ID,
            to_agent_id=POSTER_ID,
            category="spec_quality",
            rating="dissatisfied",
            comment="Court ruling",
        )
        _inject_mock(platform_payload, platform_id=PLATFORM_ID)
        platform_resp = await client.post(
            "/feedback", json=_token_body(platform_payload, kid=PLATFORM_ID)
        )
        assert platform_resp.status_code == 201
        assert platform_resp.json()["visible"] is True
        platform_id_fb = platform_resp.json()["feedback_id"]

        # The unrelated sealed pair must remain sealed: 404 via direct GET...
        get_resp = await client.get(f"/feedback/{sealed_id}")
        assert get_resp.status_code == 404

        # ...and absent from the task listing (visible-only filter).
        task_resp = await client.get(f"/feedback/task/{task_id}")
        ids = [f["feedback_id"] for f in task_resp.json()["feedback"]]
        assert sealed_id not in ids
        assert platform_id_fb in ids
