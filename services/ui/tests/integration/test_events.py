"""Integration tests for events APIs and SSE."""

from __future__ import annotations

import json

import pytest

from ui_service.config import get_settings
from ui_service.core.state import get_app_state
from ui_service.routers import events as events_router
from ui_service.services import events as events_service

pytestmark = pytest.mark.integration


def _event_ids(data: dict[str, object]) -> list[int]:
    events = data["events"]
    assert isinstance(events, list)
    return [int(event["event_id"]) for event in events]


async def test_events_returns_reverse_chronological(client):
    response = await client.get("/api/events")
    assert response.status_code == 200
    ids = _event_ids(response.json())
    assert ids == sorted(ids, reverse=True)


async def test_events_default_returns_all_25(client):
    response = await client.get("/api/events")
    assert response.status_code == 200
    data = response.json()
    assert len(data["events"]) == 25


async def test_events_limit_param(client):
    response = await client.get("/api/events?limit=5")
    assert response.status_code == 200
    assert len(response.json()["events"]) == 5


async def test_events_limit_clamped_to_200(client):
    response = await client.get("/api/events?limit=999")
    assert response.status_code == 200
    assert len(response.json()["events"]) <= 200


async def test_events_before_cursor(client):
    response = await client.get("/api/events?before=15")
    assert response.status_code == 200
    assert all(event["event_id"] < 15 for event in response.json()["events"])


async def test_events_after_cursor(client):
    response = await client.get("/api/events?after=20")
    assert response.status_code == 200
    assert all(event["event_id"] > 20 for event in response.json()["events"])


async def test_events_before_and_after_range(client):
    response = await client.get("/api/events?before=20&after=10")
    assert response.status_code == 200
    assert all(10 < event["event_id"] < 20 for event in response.json()["events"])


async def test_events_filter_by_source(client):
    response = await client.get("/api/events?source=identity")
    assert response.status_code == 200
    ids = _event_ids(response.json())
    assert ids == [22, 4, 3, 2, 1]


async def test_events_filter_by_type(client):
    response = await client.get("/api/events?type=task.created")
    assert response.status_code == 200
    ids = _event_ids(response.json())
    assert ids == [25, 23, 16, 7]


async def test_events_filter_by_agent_id(client):
    response = await client.get("/api/events?agent_id=a-alice")
    assert response.status_code == 200
    assert all(event["agent_id"] == "a-alice" for event in response.json()["events"])


async def test_events_filter_by_task_id(client):
    response = await client.get("/api/events?task_id=t-task1")
    assert response.status_code == 200
    ids = _event_ids(response.json())
    assert ids == [15, 14, 13, 12, 11, 10, 9, 8, 7]


async def test_events_has_more_true(client):
    response = await client.get("/api/events?limit=5")
    assert response.status_code == 200
    assert response.json()["has_more"] is True


async def test_events_has_more_false(client):
    response = await client.get("/api/events?limit=50")
    assert response.status_code == 200
    assert response.json()["has_more"] is False


async def test_events_oldest_newest_ids(client):
    response = await client.get("/api/events")
    assert response.status_code == 200
    data = response.json()
    ids = _event_ids(data)
    assert data["newest_event_id"] == ids[0]
    assert data["oldest_event_id"] == ids[-1]


async def test_events_invalid_limit_returns_400(client):
    response = await client.get("/api/events?limit=abc")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_parameter"


async def test_events_invalid_before_returns_400(client):
    response = await client.get("/api/events?before=abc")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_parameter"


async def test_events_limit_zero_returns_400(client):
    response = await client.get("/api/events?limit=0")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_parameter"


async def test_events_payload_is_dict(client):
    response = await client.get("/api/events")
    assert response.status_code == 200
    assert all(isinstance(event["payload"], dict) for event in response.json()["events"])


async def test_events_staleness_new_event(client, write_db):
    await write_db.execute(
        "INSERT INTO events "
        "(event_id, event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            26,
            "board",
            "task.created",
            "2026-03-02T06:30:00Z",
            "t-task6",
            "a-alice",
            "Inserted newest event",
            json.dumps({"title": "Newest"}),
        ),
    )
    await write_db.commit()

    response = await client.get("/api/events?limit=1")
    assert response.status_code == 200
    assert response.json()["events"][0]["event_id"] == 26


async def test_sse_content_type(client):
    _ = client
    response = await events_router.stream_events(last_event_id=0)
    assert response.media_type == "text/event-stream"
    assert response.headers["X-Accel-Buffering"] == "no"


async def test_sse_sends_existing_events(client):
    _ = client
    state = get_app_state()
    assert state.db is not None
    settings = get_settings()

    stream = events_service.stream_events(
        state.db,
        last_event_id=24,
        batch_size=settings.sse.batch_size,
        poll_interval=settings.sse.poll_interval_seconds,
        keepalive_interval=settings.sse.keepalive_interval_seconds,
    )
    first = await anext(stream)
    second = await anext(stream)
    await stream.aclose()

    assert first["retry"] == 3000
    assert second["event"] == "economy_event"
    payload = json.loads(str(second["data"]))
    assert payload["event_id"] == 25


async def test_sse_retry_directive(client):
    _ = client
    state = get_app_state()
    assert state.db is not None
    settings = get_settings()

    stream = events_service.stream_events(
        state.db,
        last_event_id=25,
        batch_size=settings.sse.batch_size,
        poll_interval=settings.sse.poll_interval_seconds,
        keepalive_interval=settings.sse.keepalive_interval_seconds,
    )
    first = await anext(stream)
    await stream.aclose()
    assert first == {"retry": 3000}
