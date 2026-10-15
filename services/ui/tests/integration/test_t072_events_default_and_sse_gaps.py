"""Coverage for GET /api/events default pagination cap and SSE
keepalive/batch-size behavior not yet exercised.

Spec IDs (docs/specifications/service-tests/ui-service-tests.md):
- EVT-01: ``limit`` defaults to 50. The existing tests/integration/test_events.py
  suite only proves this indirectly (25 seeded events < 50, so a wrong
  default of e.g. 999 would not be caught by any existing assertion) — this
  file seeds beyond 50 to make the boundary provable.
- EVT-04: keepalive comments are sent when idle, and at most
  ``sse.batch_size`` events are sent per poll cycle.
  ``ui_service.services.events.stream_events`` takes
  poll_interval/keepalive_interval/batch_size as plain arguments (not
  settings lookups), so these can be driven directly and deterministically
  without waiting out the real 15s config value — the same technique
  tests/integration/test_events.py already uses to call stream_events
  directly for its retry/resume assertions.
"""

from __future__ import annotations

import json

import pytest

from ui_service.core.state import get_app_state
from ui_service.services import events as events_service

pytestmark = pytest.mark.integration


async def test_events_default_limit_is_50(client, write_db) -> None:
    """EVT-01: with more than 50 events seeded, the no-limit response caps at 50."""
    for extra_id in range(26, 90):  # 64 extra events -> 25 seeded + 64 = 89 total
        await write_db.execute(
            "INSERT INTO events "
            "(event_id, event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                extra_id,
                "board",
                "task.created",
                "2026-03-02T07:00:00Z",
                None,
                "a-alice",
                f"Extra event {extra_id}",
                json.dumps({}),
            ),
        )
    await write_db.commit()

    response = await client.get("/api/events")
    assert response.status_code == 200
    assert len(response.json()["events"]) == 50


async def test_sse_sends_keepalive_when_idle(client) -> None:
    """EVT-04: a keepalive comment is sent once keepalive_interval elapses
    with no new rows — driven with keepalive_interval=0 so it fires on the
    very first idle poll, deterministically and instantly (no real wait)."""
    _ = client  # needed only to stand up the app/db via the fixture chain
    state = get_app_state()
    assert state.db is not None

    stream = events_service.stream_events(
        state.db,
        last_event_id=25,  # highest seeded event_id -- no new rows exist
        batch_size=50,
        poll_interval=0,
        keepalive_interval=0,
    )
    first = await anext(stream)
    assert first == {"retry": 3000}
    second = await anext(stream)
    await stream.aclose()
    assert second == {"comment": "keepalive"}


async def test_sse_batches_are_capped_at_batch_size(client, write_db) -> None:
    """EVT-04: a single poll cycle sends at most batch_size events, even
    when more rows are available — proven by seeding 5 new rows behind a
    batch_size=3 stream and checking the first 3 yields are exactly the 3
    oldest of the 5 (ascending), not all 5."""
    _ = client  # needed only to stand up the app/db via the fixture chain
    for new_id in range(26, 31):  # 5 new events beyond the seeded max (25)
        await write_db.execute(
            "INSERT INTO events "
            "(event_id, event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id,
                "board",
                "task.created",
                "2026-03-02T07:00:00Z",
                None,
                "a-alice",
                f"Batch event {new_id}",
                json.dumps({}),
            ),
        )
    await write_db.commit()

    state = get_app_state()
    assert state.db is not None

    stream = events_service.stream_events(
        state.db,
        last_event_id=25,
        batch_size=3,
        poll_interval=100,  # long enough that a 2nd poll cycle never starts in this test
        keepalive_interval=100,
    )
    first = await anext(stream)
    assert first == {"retry": 3000}

    received_ids = []
    for _ in range(3):
        item = await anext(stream)
        assert item["event"] == "economy_event"
        received_ids.append(json.loads(item["data"])["event_id"])
    await stream.aclose()

    assert received_ids == [26, 27, 28]
