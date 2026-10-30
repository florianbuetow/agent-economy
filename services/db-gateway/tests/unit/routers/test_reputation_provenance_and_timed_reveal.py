"""Reputation feedback provenance (G10) and durable timed reveal (G11).

Provenance is stored in the ``source`` column: ``court`` for platform (ruling)
feedback, ``peer`` for feedback between the task parties. Reads return it. The
timed reveal flips the stored ``visible`` flag and emits exactly one
``feedback.revealed`` event per newly revealed row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.conftest import make_event
from tests.unit.routers.test_reputation_reads import _setup_feedback_pair, _wire_db_reader

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

SUBMITTED_AT = "2026-02-28T15:00:00+00:00"


def _body(task_id: str, from_id: str, to_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "feedback_id": f"fb-{uuid4()}",
        "task_id": task_id,
        "from_agent_id": from_id,
        "to_agent_id": to_id,
        "role": "poster",
        "source": "peer",
        "category": "delivery_quality",
        "rating": "satisfied",
        "comment": "Good work",
        "submitted_at": SUBMITTED_AT,
        "event": make_event(source="reputation", event_type="feedback.submitted"),
    }
    body.update(overrides)
    return body


def _revealed_events(db_writer: DbWriter, task_id: str) -> int:
    row = db_writer.connection.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = 'feedback.revealed' AND task_id = ?",
        (task_id,),
    ).fetchone()
    return int(row[0])


def _stored_visible(db_writer: DbWriter, feedback_id: str) -> int:
    row = db_writer.connection.execute(
        "SELECT visible FROM reputation_feedback WHERE feedback_id = ?", (feedback_id,)
    ).fetchone()
    return int(row[0])


def _reveal(client: TestClient, cutoff: str) -> Any:
    return client.post(
        "/reputation/feedback/reveal-expired",
        json={"cutoff": cutoff, "timestamp": "2026-03-02T00:00:00+00:00"},
    )


@pytest.mark.unit
class TestFeedbackProvenance:
    def test_peer_feedback_projects_source_peer(self, app_with_writer: TestClient) -> None:
        alice, bob, tid, _ = _setup_feedback_pair(app_with_writer)
        body = _body(tid, alice, bob)
        assert app_with_writer.post("/reputation/feedback", json=body).status_code == 201
        _wire_db_reader()

        fetched = app_with_writer.get(f"/reputation/feedback/{body['feedback_id']}").json()
        assert fetched["source"] == "peer"
        assert fetched["role"] == "poster"

    def test_court_feedback_projects_source_court(self, app_with_writer: TestClient) -> None:
        alice, bob, tid, _ = _setup_feedback_pair(app_with_writer)
        body = _body(tid, alice, bob, source="court", force_visible=True)
        response = app_with_writer.post("/reputation/feedback", json=body)
        assert response.status_code == 201
        assert response.json()["visible"] is True
        _wire_db_reader()

        listed = app_with_writer.get(f"/reputation/feedback?task_id={tid}").json()["feedback"]
        assert [item["source"] for item in listed] == ["court"]

    @pytest.mark.parametrize("source", ["unknown", "PEER"])
    def test_invalid_source_is_rejected(self, app_with_writer: TestClient, source: object) -> None:
        alice, bob, tid, _ = _setup_feedback_pair(app_with_writer)
        response = app_with_writer.post(
            "/reputation/feedback", json=_body(tid, alice, bob, source=source)
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_source"

    def test_missing_source_is_rejected(self, app_with_writer: TestClient) -> None:
        alice, bob, tid, _ = _setup_feedback_pair(app_with_writer)
        body = _body(tid, alice, bob)
        del body["source"]
        response = app_with_writer.post("/reputation/feedback", json=body)
        assert response.status_code == 400


@pytest.mark.unit
class TestTimedReveal:
    def test_reveal_before_cutoff_reveals_nothing(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        alice, bob, tid, _ = _setup_feedback_pair(app_with_writer)
        body = _body(tid, alice, bob)
        app_with_writer.post("/reputation/feedback", json=body)

        response = _reveal(app_with_writer, "2026-02-28T14:59:59+00:00")
        assert response.status_code == 200
        assert response.json() == {"revealed": 0}
        assert _stored_visible(db_writer, body["feedback_id"]) == 0
        assert _revealed_events(db_writer, tid) == 0

    def test_reveal_after_cutoff_flips_flag_and_emits_one_event(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        alice, bob, tid, _ = _setup_feedback_pair(app_with_writer)
        body = _body(tid, alice, bob)
        app_with_writer.post("/reputation/feedback", json=body)

        first = _reveal(app_with_writer, "2026-03-01T15:00:00+00:00")
        assert first.status_code == 200
        assert first.json() == {"revealed": 1}
        assert _stored_visible(db_writer, body["feedback_id"]) == 1
        assert _revealed_events(db_writer, tid) == 1

        second = _reveal(app_with_writer, "2026-03-01T15:00:00+00:00")
        assert second.json() == {"revealed": 0}
        assert _revealed_events(db_writer, tid) == 1

    def test_mutually_revealed_pair_is_not_revealed_again(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        alice, bob, tid, _ = _setup_feedback_pair(app_with_writer)
        app_with_writer.post("/reputation/feedback", json=_body(tid, alice, bob))
        app_with_writer.post(
            "/reputation/feedback",
            json=_body(tid, bob, alice, role="worker", category="spec_quality"),
        )
        assert _revealed_events(db_writer, tid) == 1

        assert _reveal(app_with_writer, "2026-03-01T15:00:00+00:00").json() == {"revealed": 0}
        assert _revealed_events(db_writer, tid) == 1

    def test_invalid_cutoff_is_rejected(self, app_with_writer: TestClient) -> None:
        response = _reveal(app_with_writer, "not-a-time")
        assert response.status_code == 400
