"""G10 — feedback is authorized against the task (plan item 0.4, finding H1).

Peer feedback is accepted only for an approved or ruled task, only between that
task's poster and worker, and only in the category that matches the direction:
the poster rates the worker's delivery quality, the worker rates the poster's
specification quality. Every case goes through the public ``POST /feedback``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from reputation_service.core.state import get_app_state
from tests.fakes.fake_task_board import FakeTaskBoard
from tests.helpers import make_jws_token, make_mock_platform_agent

if TYPE_CHECKING:
    from httpx import AsyncClient

POSTER_ID = "a-poster-g10"
WORKER_ID = "a-worker-g10"
OUTSIDER_ID = "a-outsider-g10"
PLATFORM_ID = "a-platform-g10"
TASK_ID = "t-g10"


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "action": "submit_feedback",
        "task_id": TASK_ID,
        "from_agent_id": POSTER_ID,
        "to_agent_id": WORKER_ID,
        "category": "delivery_quality",
        "rating": "satisfied",
        "comment": "Good work",
    }
    base.update(overrides)
    return base


def _board(status: str) -> FakeTaskBoard:
    """Install a Task Board that knows exactly one task with the given status."""
    board = FakeTaskBoard(default=None)
    board.set_task(TASK_ID, POSTER_ID, WORKER_ID, status)
    state = get_app_state()
    state.task_board_client = board
    mock_agent = make_mock_platform_agent(verify_payload=None)
    mock_agent.agent_id = PLATFORM_ID
    state.platform_agent = mock_agent
    return board


async def _post(client: AsyncClient, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    token = make_jws_token(payload, kid=str(payload["from_agent_id"]))
    response = await client.post("/feedback", json={"token": token})
    return response.status_code, response.json()


def _stored_sources() -> list[str | None]:
    store = get_app_state().feedback_store
    assert store is not None
    return [record.source for record in store.get_by_task(TASK_ID)]


@pytest.mark.unit
class TestLegitimatePeerFeedback:
    """Poster and worker may rate each other on an eligible task."""

    @pytest.mark.parametrize("status", ["approved", "ruled"])
    async def test_poster_rates_worker_delivery(self, client: AsyncClient, status: str) -> None:
        _board(status)
        code, body = await _post(client, _payload())
        assert code == 201, body
        assert body["from_agent_id"] == POSTER_ID
        assert _stored_sources() == ["peer"]

    async def test_worker_rates_poster_spec(self, client: AsyncClient) -> None:
        _board("approved")
        code, body = await _post(
            client,
            _payload(from_agent_id=WORKER_ID, to_agent_id=POSTER_ID, category="spec_quality"),
        )
        assert code == 201, body
        assert _stored_sources() == ["peer"]


@pytest.mark.unit
class TestRejectedPeerFeedback:
    """Feedback that the task does not authorize is refused and not stored."""

    async def test_non_party_sender_is_rejected(self, client: AsyncClient) -> None:
        _board("approved")
        code, body = await _post(client, _payload(from_agent_id=OUTSIDER_ID))
        assert code == 403
        assert body["error"] == "not_task_party"
        assert _stored_sources() == []

    async def test_non_party_target_is_rejected(self, client: AsyncClient) -> None:
        _board("approved")
        code, body = await _post(client, _payload(to_agent_id=OUTSIDER_ID))
        assert code == 403
        assert body["error"] == "not_task_party"
        assert _stored_sources() == []

    async def test_poster_cannot_rate_spec_quality(self, client: AsyncClient) -> None:
        _board("approved")
        code, body = await _post(client, _payload(category="spec_quality"))
        assert code == 400
        assert body["error"] == "category_direction_mismatch"
        assert _stored_sources() == []

    async def test_worker_cannot_rate_delivery_quality(self, client: AsyncClient) -> None:
        _board("approved")
        code, body = await _post(
            client,
            _payload(from_agent_id=WORKER_ID, to_agent_id=POSTER_ID, category="delivery_quality"),
        )
        assert code == 400
        assert body["error"] == "category_direction_mismatch"
        assert _stored_sources() == []

    async def test_unknown_task_is_rejected(self, client: AsyncClient) -> None:
        _board("approved")
        code, body = await _post(client, _payload(task_id="t-does-not-exist"))
        assert code == 404
        assert body["error"] == "task_not_found"

    @pytest.mark.parametrize(
        "status", ["open", "accepted", "submitted", "disputed", "cancelled", "expired"]
    )
    async def test_ineligible_task_status_is_rejected(
        self, client: AsyncClient, status: str
    ) -> None:
        _board(status)
        code, body = await _post(client, _payload())
        assert code == 409
        assert body["error"] == "task_not_eligible"
        assert _stored_sources() == []

    async def test_task_board_unreachable_returns_503(self, client: AsyncClient) -> None:
        board = _board("approved")
        board.unavailable = True
        code, body = await _post(client, _payload())
        assert code == 503
        assert body["error"] == "task_board_unavailable"
        assert set(body) == {"error", "message", "details"}
        assert _stored_sources() == []


@pytest.mark.unit
class TestReplay:
    """An identical resubmission is idempotent; a changed one conflicts."""

    async def test_exact_replay_returns_200_with_stored_record(self, client: AsyncClient) -> None:
        _board("approved")
        first_code, first = await _post(client, _payload())
        replay_code, replay = await _post(client, _payload())
        assert first_code == 201
        assert replay_code == 200
        assert replay == first
        assert _stored_sources() == ["peer"]

    async def test_changed_replay_returns_409(self, client: AsyncClient) -> None:
        _board("approved")
        first_code, _ = await _post(client, _payload())
        changed_code, changed = await _post(client, _payload(rating="dissatisfied"))
        assert first_code == 201
        assert changed_code == 409
        assert changed["error"] == "feedback_exists"


@pytest.mark.unit
class TestCourtFeedbackSource:
    """Platform-signed (Court) feedback keeps its path and is marked as court."""

    async def test_platform_feedback_is_stored_with_court_source(self, client: AsyncClient) -> None:
        _board("ruled")
        code, body = await _post(
            client,
            _payload(from_agent_id=PLATFORM_ID, to_agent_id=POSTER_ID, category="spec_quality"),
        )
        assert code == 201, body
        assert body["visible"] is True
        assert _stored_sources() == ["court"]
