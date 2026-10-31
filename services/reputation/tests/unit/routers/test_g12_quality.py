"""G12 — service-owned quality score (plan item 3.5, business rules 6 and 7).

``GET /agents/{agent_id}/quality`` returns separate specification and delivery
dimensions. Each eligible task counts once with equal weight; the Court finding
for a task replaces the peer finding; sealed feedback, cancelled and expired
tasks never count; an agent with no eligible task is explicitly unrated. Scores
and counts are public. Source-level provenance is returned only to the subject,
the operator, or a participant of the source task, per record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.jws import VerifiedJws, parse_jws
from service_auth.signing import create_jws

from reputation_service.core.state import get_app_state
from tests.fakes.fake_task_board import FakeTaskBoard

if TYPE_CHECKING:
    from httpx import AsyncClient

WORKER = "a-worker"
POSTER_1 = "a-poster-1"
POSTER_2 = "a-poster-2"
OPERATOR = "a-operator"
OUTSIDER = "a-outsider"
COURT = "a-platform-test-id"
OPERATOR_NAME = "operator"

_KEY = Ed25519PrivateKey.generate()


class _TestVerifier:
    """Identity stand-in: accepts tokens signed by the test key, rejects 'bad' ones."""

    async def verify_jws(self, token: str) -> VerifiedJws:
        if token.endswith(".bad"):
            raise InvalidSignature
        parsed = parse_jws(token)
        return VerifiedJws(header=parsed.header, payload=dict(parsed.payload))

    async def close(self) -> None:
        return None


class _TestDirectory:
    """Identity agent lookup: only OPERATOR carries the configured operator name."""

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        names = {OPERATOR: OPERATOR_NAME, WORKER: "worker", POSTER_1: "p1", OUTSIDER: "x"}
        name = names.get(agent_id)
        return None if name is None else {"agent_id": agent_id, "name": name}


def _token(kid: str, agent_id: str) -> str:
    return create_jws(
        {"action": "get_quality"},
        _KEY,
        kid=kid,
        audience="reputation",
        resource=f"GET /agents/{agent_id}/quality",
        token_ttl_seconds=60,
    )


@dataclass(frozen=True)
class _Row:
    task_id: str
    from_agent_id: str
    to_agent_id: str
    category: str
    rating: str
    visible: bool
    source: str


# t1 approved: peer 100 delivery for WORKER, worker rates poster spec 50.
# t2 ruled:    peer 0, Court 50 -> Court replaces peer, task counts once.
# t3 approved: sealed peer rating -> excluded and never disclosed.
# t4 cancelled, t5 expired -> never count.
# t6 approved: peer 50 (auto-approved tasks are status approved too).
_TASKS = {
    "t1": (POSTER_1, WORKER, "approved"),
    "t2": (POSTER_2, WORKER, "ruled"),
    "t3": (POSTER_1, WORKER, "approved"),
    "t4": (POSTER_1, WORKER, "cancelled"),
    "t5": (POSTER_2, WORKER, "expired"),
    "t6": (POSTER_2, WORKER, "approved"),
}
_ROWS = [
    _Row("t1", POSTER_1, WORKER, "delivery_quality", "extremely_satisfied", True, "peer"),
    _Row("t1", WORKER, POSTER_1, "spec_quality", "satisfied", True, "peer"),
    _Row("t2", POSTER_2, WORKER, "delivery_quality", "dissatisfied", True, "peer"),
    _Row("t2", COURT, WORKER, "delivery_quality", "satisfied", True, "court"),
    _Row("t3", POSTER_1, WORKER, "delivery_quality", "dissatisfied", False, "peer"),
    _Row("t4", POSTER_1, WORKER, "delivery_quality", "dissatisfied", True, "peer"),
    _Row("t5", POSTER_2, WORKER, "delivery_quality", "dissatisfied", True, "peer"),
    _Row("t6", POSTER_2, WORKER, "delivery_quality", "satisfied", True, "peer"),
]

_ALL_WORKER_DELIVERY = {
    ("t1", "peer", True, None),
    ("t2", "peer", False, "court_override"),
    ("t2", "court", True, None),
    ("t4", "peer", False, "task_not_eligible"),
    ("t5", "peer", False, "task_not_eligible"),
    ("t6", "peer", True, None),
}


@pytest.fixture
async def seeded(client: AsyncClient) -> AsyncClient:
    state = get_app_state()
    board = FakeTaskBoard(default=None)
    for task_id, (poster, worker, status) in _TASKS.items():
        board.set_task(task_id, poster, worker, status)
    state.task_board_client = board
    state.identity_client = _TestVerifier()
    state.agent_directory = _TestDirectory()
    store = state.feedback_store
    assert store is not None
    for row in _ROWS:
        store.insert_feedback(
            task_id=row.task_id,
            from_agent_id=row.from_agent_id,
            to_agent_id=row.to_agent_id,
            category=row.category,
            rating=row.rating,
            comment=None,
            force_visible=row.visible,
            source=row.source,
        )
    return client


def _provenance_keys(dimension: dict[str, Any]) -> set[tuple[str, str, bool, str | None]]:
    return {
        (p["task_id"], p["source"], p["included"], p["exclusion_reason"])
        for p in dimension["provenance"]
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("viewer", "expected_delivery_provenance"),
    [
        pytest.param(None, None, id="public-unsigned"),
        pytest.param(OUTSIDER, None, id="outsider-signed"),
        pytest.param(WORKER, _ALL_WORKER_DELIVERY, id="subject"),
        pytest.param(OPERATOR, _ALL_WORKER_DELIVERY, id="operator"),
        pytest.param(
            POSTER_1,
            {("t1", "peer", True, None), ("t4", "peer", False, "task_not_eligible")},
            id="participant-sees-only-own-tasks",
        ),
    ],
)
async def test_worker_quality(
    seeded: AsyncClient,
    viewer: str | None,
    expected_delivery_provenance: set[tuple[str, str, bool, str | None]] | None,
) -> None:
    headers = {} if viewer is None else {"Authorization": f"Bearer {_token(viewer, WORKER)}"}
    response = await seeded.get(f"/agents/{WORKER}/quality", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agent_id"] == WORKER
    delivery = body["delivery_quality"]
    spec = body["specification_quality"]

    # Equal task weighting with Court override: tasks t1=100, t2=50 (Court), t6=50.
    # Per-record averaging would give 50; double counting t2 would give 50.
    assert delivery["rated"] is True
    assert delivery["score"] == 67  # 200/3 rounded half-up
    assert delivery["task_count"] == 3
    assert delivery["rating_counts"] == {
        "extremely_satisfied": 1,
        "satisfied": 2,
        "dissatisfied": 0,
    }
    # No specification feedback about the worker: explicitly unrated.
    assert spec["rated"] is False
    assert spec["score"] is None
    assert spec["task_count"] == 0

    if expected_delivery_provenance is None:
        assert "provenance" not in delivery
        assert "provenance" not in spec
    else:
        assert _provenance_keys(delivery) == expected_delivery_provenance
        assert spec["provenance"] == []
        # Sealed t3 is never disclosed, even to the subject.
        assert all(p["task_id"] != "t3" for p in delivery["provenance"])


@pytest.mark.unit
async def test_poster_specification_dimension_is_separate(seeded: AsyncClient) -> None:
    response = await seeded.get(f"/agents/{POSTER_1}/quality")
    assert response.status_code == 200
    body = response.json()
    assert body["specification_quality"]["score"] == 50
    assert body["specification_quality"]["task_count"] == 1
    assert body["delivery_quality"] == {
        "rated": False,
        "score": None,
        "task_count": 0,
        "rating_counts": {"extremely_satisfied": 0, "satisfied": 0, "dissatisfied": 0},
    }


@pytest.mark.unit
async def test_unknown_agent_is_unrated(seeded: AsyncClient) -> None:
    response = await seeded.get("/agents/a-nobody/quality")
    assert response.status_code == 200
    body = response.json()
    for dimension in ("specification_quality", "delivery_quality"):
        assert body[dimension]["rated"] is False
        assert body[dimension]["score"] is None
        assert body[dimension]["task_count"] == 0


@pytest.mark.unit
async def test_task_board_outage_is_not_unrated(seeded: AsyncClient) -> None:
    board = get_app_state().task_board_client
    assert isinstance(board, FakeTaskBoard)
    board.unavailable = True
    response = await seeded.get(f"/agents/{WORKER}/quality")
    assert response.status_code == 503
    assert response.json()["error"] == "task_board_unavailable"


@pytest.mark.unit
async def test_invalid_signature_is_rejected(seeded: AsyncClient) -> None:
    token = _token(WORKER, WORKER).rsplit(".", 1)[0] + ".bad"
    response = await seeded.get(
        f"/agents/{WORKER}/quality", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.unit
async def test_token_for_another_agent_is_rejected(seeded: AsyncClient) -> None:
    response = await seeded.get(
        f"/agents/{WORKER}/quality",
        headers={"Authorization": f"Bearer {_token(WORKER, POSTER_1)}"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"
