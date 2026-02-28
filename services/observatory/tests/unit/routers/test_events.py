"""Tests for events endpoints."""

import asyncio
import json
import sqlite3

import pytest

from observatory_service.core.state import get_app_state
from observatory_service.services import events as events_service

# === Events History Tests ===


@pytest.mark.unit
async def test_evt_01_reverse_chronological(seeded_client):
    """EVT-01: Events in reverse chronological order."""
    response = await seeded_client.get("/api/events")
    assert response.status_code == 200
    data = response.json()
    assert len(data["events"]) > 0
    ids = [e["event_id"] for e in data["events"]]
    assert ids == sorted(ids, reverse=True)
    for event in data["events"]:
        for key in ["event_id", "event_source", "event_type", "timestamp", "summary", "payload"]:
            assert key in event


@pytest.mark.unit
async def test_evt_02_limit_parameter(seeded_client):
    """EVT-02: Limit parameter works."""
    response = await seeded_client.get("/api/events?limit=3")
    assert response.status_code == 200
    data = response.json()
    assert len(data["events"]) == 3
    assert data["has_more"] is True


@pytest.mark.unit
async def test_evt_03_before_parameter(seeded_client):
    """EVT-03: Before parameter for backward pagination."""
    response = await seeded_client.get("/api/events?before=10&limit=5")
    assert response.status_code == 200
    data = response.json()
    for event in data["events"]:
        assert event["event_id"] < 10
    assert len(data["events"]) <= 5


@pytest.mark.unit
async def test_evt_04_after_parameter(seeded_client):
    """EVT-04: After parameter for forward pagination."""
    response = await seeded_client.get("/api/events?after=10&limit=5")
    assert response.status_code == 200
    data = response.json()
    for event in data["events"]:
        assert event["event_id"] > 10


@pytest.mark.unit
async def test_evt_05_filter_by_source(seeded_client):
    """EVT-05: Filter by event source."""
    response = await seeded_client.get("/api/events?source=board")
    assert response.status_code == 200
    data = response.json()
    for event in data["events"]:
        assert event["event_source"] == "board"


@pytest.mark.unit
async def test_evt_06_filter_by_type(seeded_client):
    """EVT-06: Filter by event type."""
    response = await seeded_client.get("/api/events?type=task.created")
    assert response.status_code == 200
    data = response.json()
    for event in data["events"]:
        assert event["event_type"] == "task.created"


@pytest.mark.unit
async def test_evt_07_filter_by_agent_id(seeded_client):
    """EVT-07: Filter by agent_id."""
    response = await seeded_client.get("/api/events?agent_id=a-alice")
    assert response.status_code == 200
    data = response.json()
    for event in data["events"]:
        assert event["agent_id"] == "a-alice"


@pytest.mark.unit
async def test_evt_08_filter_by_task_id(seeded_client):
    """EVT-08: Filter by task_id."""
    response = await seeded_client.get("/api/events?task_id=t-1")
    assert response.status_code == 200
    data = response.json()
    for event in data["events"]:
        assert event["task_id"] == "t-1"


@pytest.mark.unit
async def test_evt_09_combined_filters(seeded_client):
    """EVT-09: Combined filters work together."""
    response = await seeded_client.get("/api/events?source=board&type=task.created")
    assert response.status_code == 200
    data = response.json()
    for event in data["events"]:
        assert event["event_source"] == "board"
        assert event["event_type"] == "task.created"


@pytest.mark.unit
async def test_evt_10_empty_result_set(seeded_client):
    """EVT-10: Nonexistent source returns empty array."""
    response = await seeded_client.get("/api/events?source=nonexistent")
    assert response.status_code == 200
    data = response.json()
    assert data["events"] == []
    assert data["has_more"] is False


@pytest.mark.unit
async def test_evt_11_invalid_limit_below_minimum(seeded_client):
    """EVT-11: limit=-1 returns 400."""
    response = await seeded_client.get("/api/events?limit=-1")
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_PARAMETER"


@pytest.mark.unit
async def test_evt_12_limit_clamped_to_200(seeded_client, seeded_db_path):
    """EVT-12: Limit over 200 is clamped (not rejected)."""
    # Insert 235 more events (already have 15, total will be 250)
    conn = sqlite3.connect(str(seeded_db_path))
    for i in range(235):
        conn.execute(
            "INSERT INTO events (event_source, event_type, timestamp, summary, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            ("board", "task.created", "2026-01-01T00:00:00Z", f"event {i}", "{}"),
        )
    conn.commit()
    conn.close()
    response = await seeded_client.get("/api/events?limit=9999")
    assert response.status_code == 200
    data = response.json()
    assert len(data["events"]) == 200
    assert data["has_more"] is True


@pytest.mark.unit
async def test_evt_13_non_integer_limit(seeded_client):
    """EVT-13: Non-integer limit returns 400."""
    response = await seeded_client.get("/api/events?limit=abc")
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_PARAMETER"


# === SSE Stream Tests ===
# These test the stream_events generator directly because httpx's ASGITransport
# does not support true streaming (it buffers the full response).


@pytest.mark.unit
async def test_sse_01_stream_delivers_events(seeded_app):  # noqa: ARG001
    """SSE-01: Stream delivers events as economy_event messages."""
    state = get_app_state()
    gen = events_service.stream_events(
        state.db, last_event_id=0, batch_size=50, poll_interval=1, keepalive_interval=15
    )

    messages = []
    try:
        async with asyncio.timeout(5):
            async for msg in gen:
                messages.append(msg)
                # Stop after collecting enough data events
                data_events = [m for m in messages if m.get("event") == "economy_event"]
                if len(data_events) >= 5:
                    break
    except TimeoutError:
        pass

    data_events = [m for m in messages if m.get("event") == "economy_event"]
    assert len(data_events) >= 5

    # Verify ascending order (SSE streams in ASC order)
    ids = [json.loads(e["data"])["event_id"] for e in data_events]
    assert ids == sorted(ids)

    # Verify event shape
    for event_msg in data_events:
        event_data = json.loads(event_msg["data"])
        assert "event_id" in event_data
        assert "event_source" in event_data
        assert "event_type" in event_data
        assert "timestamp" in event_data
        assert "summary" in event_data
        assert "payload" in event_data

    # Verify SSE message format
    for event_msg in data_events:
        assert event_msg["event"] == "economy_event"
        assert "id" in event_msg
        assert "data" in event_msg


@pytest.mark.unit
async def test_sse_02_cursor_based_resumption(seeded_app):  # noqa: ARG001
    """SSE-02: Cursor-based resumption only returns events after cursor."""
    state = get_app_state()
    gen = events_service.stream_events(
        state.db, last_event_id=10, batch_size=50, poll_interval=1, keepalive_interval=15
    )

    messages = []
    try:
        async with asyncio.timeout(5):
            async for msg in gen:
                messages.append(msg)
                data_events = [m for m in messages if m.get("event") == "economy_event"]
                if len(data_events) >= 3:
                    break
    except TimeoutError:
        pass

    data_events = [m for m in messages if m.get("event") == "economy_event"]
    assert len(data_events) > 0
    for event_msg in data_events:
        event_data = json.loads(event_msg["data"])
        assert event_data["event_id"] > 10

    # First event after cursor 10 should be event 11
    first_event = json.loads(data_events[0]["data"])
    assert first_event["event_id"] == 11


@pytest.mark.unit
async def test_sse_03_retry_directive(seeded_app):  # noqa: ARG001
    """SSE-03: Stream starts with a retry directive."""
    state = get_app_state()
    gen = events_service.stream_events(
        state.db, last_event_id=0, batch_size=50, poll_interval=1, keepalive_interval=15
    )

    # First message should be the retry directive
    first_msg = await gen.__anext__()
    assert first_msg == {"retry": 3000}
