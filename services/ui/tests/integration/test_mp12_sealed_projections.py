"""MP-12 acceptance coverage for sealed bids and feedback projections."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from service_commons.exceptions import ServiceError

from ui_service.core.state import get_app_state
from ui_service.services import events as events_service

pytestmark = pytest.mark.integration

_OPEN_TASK_ID = "t-task6"
_POST_OPEN_TASK_ID = "t-task1"
_OPEN_BID_EVENT_ID = 100
_POST_OPEN_BID_EVENT_ID = 99
_SEALED_EVENT_ID = 101
_SEALED_RATING = "mp12-rating-extremely-satisfied"
_SEALED_CATEGORY = "mp12-category-spec-quality"
_SEALED_COMMENT = "mp12-comment-private"
_OPEN_BID_ID = "bid-mp12-open-secret"
_OPEN_BIDDER_ID = "a-bob"
_OPEN_BID_PROPOSAL = "mp12-open-bid-proposal-secret"
_OPEN_BID_SUMMARY = "Bob offered 73 credits for Alice's open task"


class _PosterBidAgent:
    """Network-free operator that returns the authoritative poster bid view."""

    agent_id = "a-alice"

    def __init__(self) -> None:
        self.requested_task_ids: list[str] = []

    async def list_bids(self, task_id: str) -> list[dict[str, object]]:
        self.requested_task_ids.append(task_id)
        return [
            {
                "bid_id": "bid-mp12-poster",
                "bidder_id": "a-bob",
                "amount": 73,
                "submitted_at": "2026-03-02T00:15:55Z",
            }
        ]


class _ForbiddenBidAgent:
    """Network-free non-poster rejected by the authoritative bid service."""

    agent_id = "a-bob"

    async def list_bids(self, task_id: str) -> list[dict[str, object]]:
        raise ServiceError(
            error="forbidden",
            message=f"Agent '{self.agent_id}' is not the poster for task '{task_id}'",
            status_code=403,
            details={"task_id": task_id},
        )


async def _insert_event(
    write_db: Any,
    *,
    event_id: int,
    event_type: str,
    task_id: str | None,
    agent_id: str | None,
    summary: str,
    payload: dict[str, object],
    timestamp: str = "2026-03-02T06:40:00Z",
) -> None:
    await write_db.execute(
        "INSERT INTO events "
        "(event_id, event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            "reputation" if event_type.startswith("feedback.") else "board",
            event_type,
            timestamp,
            task_id,
            agent_id,
            summary,
            json.dumps(payload),
        ),
    )


def _assert_feedback_secret_absent(event: dict[str, object]) -> None:
    serialized = json.dumps(event, sort_keys=True)
    assert _SEALED_RATING not in serialized
    assert _SEALED_CATEGORY not in serialized
    assert _SEALED_COMMENT not in serialized

    payload = event["payload"]
    assert isinstance(payload, dict)
    assert not {"rating", "category", "comment"}.intersection(payload)


def _assert_open_bid_secret_absent(event: dict[str, object]) -> None:
    serialized = json.dumps(event, sort_keys=True)
    assert event["agent_id"] is None
    assert _OPEN_BID_ID not in serialized
    assert _OPEN_BIDDER_ID not in serialized
    assert _OPEN_BID_PROPOSAL not in serialized
    assert _OPEN_BID_SUMMARY not in serialized

    payload = event["payload"]
    assert isinstance(payload, dict)
    assert not {"bid_id", "bidder_id", "amount", "proposal"}.intersection(payload)


async def _next_sse_event(
    *,
    event_id: int,
    last_event_id: int,
) -> dict[str, object]:
    state = get_app_state()
    assert state.db is not None
    stream: Any = events_service.stream_events(
        state.db,
        last_event_id=last_event_id,
        batch_size=200,
        poll_interval=60,
        keepalive_interval=60,
    )
    try:
        async with asyncio.timeout(3):
            assert await anext(stream) == {"retry": 3000}
            for _ in range(200):
                item = await anext(stream)
                if item.get("id") == str(event_id):
                    payload = json.loads(str(item["data"]))
                    assert isinstance(payload, dict)
                    return payload
    finally:
        await stream.aclose()
    pytest.fail(f"SSE event {event_id} was not emitted")


async def _insert_bid_events(write_db: Any) -> None:
    await _insert_event(
        write_db,
        event_id=_POST_OPEN_BID_EVENT_ID,
        event_type="bid.submitted",
        task_id=_POST_OPEN_TASK_ID,
        agent_id="a-bob",
        summary="Bob bid on a task whose open phase has ended",
        payload={"bid_id": "bid-mp12-post-open", "amount": 83},
    )
    await _insert_event(
        write_db,
        event_id=_OPEN_BID_EVENT_ID,
        event_type="bid.submitted",
        task_id=_OPEN_TASK_ID,
        agent_id=_OPEN_BIDDER_ID,
        summary=_OPEN_BID_SUMMARY,
        payload={
            "bid_id": _OPEN_BID_ID,
            "bidder_id": _OPEN_BIDDER_ID,
            "amount": 73,
            "proposal": _OPEN_BID_PROPOSAL,
        },
    )
    await write_db.commit()


async def _projection_snapshot(client: Any) -> dict[str, Any]:
    task_response = await client.get("/api/tasks/t-task8")
    profile_response = await client.get("/api/agents/a-carol")
    agents_response = await client.get("/api/agents?limit=200")
    metrics_response = await client.get("/api/metrics")
    quarterly_response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    sparkline_response = await client.get("/api/metrics/sparklines?window=24h")
    responses = (
        task_response,
        profile_response,
        agents_response,
        metrics_response,
        quarterly_response,
        sparkline_response,
    )
    assert all(response.status_code == 200 for response in responses)

    agents = agents_response.json()["agents"]
    carol = next(agent for agent in agents if agent["agent_id"] == "a-carol")
    return {
        "task_feedback": task_response.json()["feedback"],
        "profile_feedback": profile_response.json()["recent_feedback"],
        "profile_spec_quality": profile_response.json()["stats"]["spec_quality"],
        "list_spec_quality": carol["stats"]["spec_quality"],
        "metrics_spec_quality": metrics_response.json()["spec_quality"],
        "quarterly_spec_quality": quarterly_response.json()["spec_quality"],
        "sparkline_spec_quality": sparkline_response.json()["metrics"]["spec_quality"],
    }


async def test_public_open_task_hides_bid_details_but_keeps_count(
    client: Any, write_db: Any
) -> None:
    await write_db.execute(
        "UPDATE board_bids SET amount = ?, proposal = ? WHERE bid_id = ?",
        (73, "mp12-open-proposal-secret", "bid-8"),
    )
    await write_db.commit()

    drilldown = await client.get(f"/api/tasks/{_OPEN_TASK_ID}")
    assert drilldown.status_code == 200
    assert drilldown.json()["status"] == "open"
    assert drilldown.json()["bid_count"] == 1
    assert drilldown.json()["bids"] == []
    serialized = drilldown.text
    assert "bid-8" not in serialized
    assert "a-bob" not in serialized
    assert "mp12-open-proposal-secret" not in serialized

    competitive = await client.get("/api/tasks/-/competitive")
    assert competitive.status_code == 200
    task = next(item for item in competitive.json()["tasks"] if item["task_id"] == _OPEN_TASK_ID)
    assert task["bid_count"] == 1


async def test_public_post_open_bids_use_canonical_integer_amount(
    client: Any, write_db: Any
) -> None:
    await write_db.execute("UPDATE board_bids SET amount = ? WHERE bid_id = ?", (83, "bid-1"))
    await write_db.execute("UPDATE board_bids SET amount = ? WHERE bid_id = ?", (91, "bid-2"))
    await write_db.commit()

    response = await client.get(f"/api/tasks/{_POST_OPEN_TASK_ID}")
    assert response.status_code == 200
    assert response.json()["status"] != "open"
    bids = response.json()["bids"]
    assert [(bid["bid_id"], bid["amount"]) for bid in bids] == [
        ("bid-1", 83),
        ("bid-2", 91),
    ]
    assert all(isinstance(bid["amount"], int) for bid in bids)
    assert all("proposal" not in bid for bid in bids)


async def test_poster_proxy_lists_open_bids_without_cacheable_response(
    client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _PosterBidAgent()
    monkeypatch.setattr(get_app_state(), "user_agent", agent)

    response = await client.get(f"/api/proxy/tasks/{_OPEN_TASK_ID}/bids")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "task_id": _OPEN_TASK_ID,
        "bids": [
            {
                "bid_id": "bid-mp12-poster",
                "bidder": {
                    "agent_id": "a-bob",
                    "name": "Bob",
                    "delivery_quality": {
                        "extremely_satisfied": 1,
                        "satisfied": 1,
                        "dissatisfied": 0,
                    },
                },
                "amount": 73,
                "submitted_at": "2026-03-02T00:15:55Z",
                "accepted": False,
            }
        ],
    }
    assert agent.requested_task_ids == [_OPEN_TASK_ID]
    cache_control = response.headers["cache-control"].lower()
    assert "private" in cache_control
    assert "no-store" in cache_control


async def test_nonposter_proxy_cannot_list_open_bids(
    client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_app_state(), "user_agent", _ForbiddenBidAgent())

    response = await client.get(f"/api/proxy/tasks/{_OPEN_TASK_ID}/bids")

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"] == "forbidden"


async def test_open_bid_secrets_are_redacted_from_public_event_history(
    client: Any, write_db: Any
) -> None:
    await _insert_event(
        write_db,
        event_id=_OPEN_BID_EVENT_ID,
        event_type="bid.submitted",
        task_id=_OPEN_TASK_ID,
        agent_id=_OPEN_BIDDER_ID,
        summary=_OPEN_BID_SUMMARY,
        payload={
            "bid_id": _OPEN_BID_ID,
            "bidder_id": _OPEN_BIDDER_ID,
            "amount": 73,
            "proposal": _OPEN_BID_PROPOSAL,
        },
    )
    await write_db.commit()

    response = await client.get("/api/events?type=bid.submitted")
    assert response.status_code == 200
    event = next(
        item for item in response.json()["events"] if item["event_id"] == _OPEN_BID_EVENT_ID
    )
    _assert_open_bid_secret_absent(event)


async def test_open_bid_actor_filter_does_not_match_sealed_membership(
    client: Any, write_db: Any
) -> None:
    await _insert_bid_events(write_db)

    response = await client.get("/api/events?agent_id=a-bob&type=bid.submitted&limit=1")

    assert response.status_code == 200
    assert [event["event_id"] for event in response.json()["events"]] == [_POST_OPEN_BID_EVENT_ID]


@pytest.mark.parametrize("last_event_id", [0, _POST_OPEN_BID_EVENT_ID])
async def test_open_bid_secrets_are_redacted_from_initial_and_resumed_sse(
    client: Any,
    write_db: Any,
    last_event_id: int,
) -> None:
    _ = client
    await _insert_event(
        write_db,
        event_id=_OPEN_BID_EVENT_ID,
        event_type="bid.submitted",
        task_id=_OPEN_TASK_ID,
        agent_id=_OPEN_BIDDER_ID,
        summary=_OPEN_BID_SUMMARY,
        payload={
            "bid_id": _OPEN_BID_ID,
            "bidder_id": _OPEN_BIDDER_ID,
            "amount": 73,
            "proposal": _OPEN_BID_PROPOSAL,
        },
    )
    await write_db.commit()

    event = await _next_sse_event(event_id=_OPEN_BID_EVENT_ID, last_event_id=last_event_id)
    assert event["event_type"] == "bid.submitted"
    _assert_open_bid_secret_absent(event)


async def test_legacy_sealed_feedback_is_redacted_from_event_history(
    client: Any, write_db: Any
) -> None:
    await _insert_event(
        write_db,
        event_id=_SEALED_EVENT_ID,
        event_type="feedback.submitted",
        task_id="t-task8",
        agent_id="a-bob",
        summary=f"{_SEALED_RATING} {_SEALED_CATEGORY} {_SEALED_COMMENT}",
        payload={
            "rating": _SEALED_RATING,
            "category": _SEALED_CATEGORY,
            "comment": _SEALED_COMMENT,
        },
    )
    await write_db.commit()

    response = await client.get("/api/events?type=feedback.submitted")
    assert response.status_code == 200
    events = response.json()["events"]
    event = next(item for item in events if item["event_id"] == _SEALED_EVENT_ID)
    _assert_feedback_secret_absent(event)


async def test_feedback_actor_filter_does_not_match_sealed_membership(
    client: Any, write_db: Any
) -> None:
    await _insert_event(
        write_db,
        event_id=_SEALED_EVENT_ID,
        event_type="feedback.submitted",
        task_id="t-task8",
        agent_id="a-bob",
        summary=f"{_SEALED_RATING} {_SEALED_CATEGORY} {_SEALED_COMMENT}",
        payload={
            "rating": _SEALED_RATING,
            "category": _SEALED_CATEGORY,
            "comment": _SEALED_COMMENT,
        },
    )
    await write_db.commit()

    response = await client.get("/api/events?agent_id=a-bob&type=feedback.submitted")

    assert response.status_code == 200
    assert response.json()["events"] == []
    assert response.json()["has_more"] is False


@pytest.mark.parametrize("last_event_id", [0, _OPEN_BID_EVENT_ID])
async def test_legacy_sealed_feedback_is_redacted_from_initial_and_resumed_sse(
    client: Any,
    write_db: Any,
    last_event_id: int,
) -> None:
    _ = client
    await _insert_event(
        write_db,
        event_id=_SEALED_EVENT_ID,
        event_type="feedback.submitted",
        task_id="t-task8",
        agent_id="a-bob",
        summary=f"{_SEALED_RATING} {_SEALED_CATEGORY} {_SEALED_COMMENT}",
        payload={
            "rating": _SEALED_RATING,
            "category": _SEALED_CATEGORY,
            "comment": _SEALED_COMMENT,
        },
    )
    await write_db.commit()

    event = await _next_sse_event(event_id=_SEALED_EVENT_ID, last_event_id=last_event_id)
    assert event["event_type"] == "feedback.submitted"
    _assert_feedback_secret_absent(event)


@pytest.mark.parametrize("agent_id", ["a-alice", "a-bob"])
async def test_open_bid_membership_is_removed_from_agent_feed_before_pagination(
    client: Any,
    write_db: Any,
    agent_id: str,
) -> None:
    await _insert_bid_events(write_db)

    response = await client.get(f"/api/agents/{agent_id}/feed?limit=1&type=BID")

    assert response.status_code == 200
    events = response.json()["events"]
    assert [event["event_id"] for event in events] == [_POST_OPEN_BID_EVENT_ID]
    assert all(event["event_id"] != _OPEN_BID_EVENT_ID for event in events)
    assert response.json()["has_more"] is True


async def test_open_bid_event_is_not_added_to_unrelated_agent_feed(
    client: Any, write_db: Any
) -> None:
    await _insert_bid_events(write_db)

    response = await client.get("/api/agents/a-eve/feed?limit=200")

    assert response.status_code == 200
    assert all(event["event_id"] != _OPEN_BID_EVENT_ID for event in response.json()["events"])


async def test_post_open_bid_event_remains_visible_in_agent_feed(
    client: Any, write_db: Any
) -> None:
    await _insert_event(
        write_db,
        event_id=_POST_OPEN_BID_EVENT_ID,
        event_type="bid.submitted",
        task_id=_POST_OPEN_TASK_ID,
        agent_id="a-bob",
        summary="Bob bid on a task whose open phase has ended",
        payload={"bid_id": "bid-mp12-post-open", "amount": 83},
    )
    await write_db.commit()

    response = await client.get("/api/agents/a-bob/feed?limit=200")

    assert response.status_code == 200
    assert _POST_OPEN_BID_EVENT_ID in {event["event_id"] for event in response.json()["events"]}


async def test_stored_producer_bid_actor_reveals_only_after_open(
    client: Any,
    write_db: Any,
) -> None:
    await write_db.execute(
        "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, amount, submitted_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "bid-mp12-losing",
            _OPEN_TASK_ID,
            "a-eve",
            "private legacy storage field",
            61,
            "2026-03-02T06:39:00Z",
        ),
    )
    await _insert_event(
        write_db,
        event_id=103,
        event_type="bid.submitted",
        task_id=_OPEN_TASK_ID,
        agent_id="a-eve",
        summary="Bid submitted",
        payload={},
    )
    await write_db.commit()

    open_history = await client.get("/api/events?type=bid.submitted&after=102")
    assert open_history.status_code == 200
    assert open_history.json()["events"][0]["agent_id"] is None
    open_feed = await client.get("/api/agents/a-eve/feed?type=BID&limit=200")
    assert open_feed.status_code == 200
    assert all(event["event_id"] != 103 for event in open_feed.json()["events"])

    await write_db.execute(
        "UPDATE board_tasks SET status = ?, worker_id = ?, accepted_bid_id = ? WHERE task_id = ?",
        ("accepted", "a-bob", "bid-8", _OPEN_TASK_ID),
    )
    await write_db.commit()

    public_history = await client.get("/api/events?agent_id=a-eve&type=bid.submitted&after=102")
    assert public_history.status_code == 200
    assert public_history.json()["events"][0]["agent_id"] == "a-eve"
    public_feed = await client.get("/api/agents/a-eve/feed?type=BID&limit=200")
    assert public_feed.status_code == 200
    assert 103 in {event["event_id"] for event in public_feed.json()["events"]}


async def test_sealed_feedback_has_no_projection_or_aggregate_influence(
    client: Any, write_db: Any
) -> None:
    """Direct visibility-fixture control; this does not exercise the reveal service."""
    before = await _projection_snapshot(client)
    await write_db.execute(
        "INSERT INTO reputation_feedback "
        "(feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, "
        "submitted_at, visible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "fb-mp12-sealed-spec",
            "t-task8",
            "a-bob",
            "a-carol",
            "worker",
            "spec_quality",
            "extremely_satisfied",
            _SEALED_COMMENT,
            "2026-03-02T05:00:00Z",
            0,
        ),
    )
    await write_db.commit()

    after_sealed = await _projection_snapshot(client)
    assert after_sealed == before

    await write_db.execute(
        "INSERT INTO reputation_feedback "
        "(feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, "
        "submitted_at, visible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "fb-mp12-mutual-delivery",
            "t-task8",
            "a-carol",
            "a-bob",
            "poster",
            "delivery_quality",
            "satisfied",
            "Visible mutual control",
            "2026-03-02T05:01:00Z",
            1,
        ),
    )
    await write_db.execute(
        "UPDATE reputation_feedback SET visible = 1 WHERE feedback_id = ?",
        ("fb-mp12-sealed-spec",),
    )
    reveal_timestamp = (
        (datetime.now(UTC) - timedelta(hours=1))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    await _insert_event(
        write_db,
        event_id=102,
        event_type="feedback.revealed",
        task_id="t-task8",
        agent_id="a-bob",
        summary="Mutual feedback revealed",
        payload={"category": "spec_quality"},
        timestamp=reveal_timestamp,
    )
    await write_db.commit()

    after_reveal = await _projection_snapshot(client)
    assert "fb-mp12-sealed-spec" in {item["feedback_id"] for item in after_reveal["task_feedback"]}
    assert "fb-mp12-mutual-delivery" in {
        item["feedback_id"] for item in after_reveal["task_feedback"]
    }
    assert "fb-mp12-sealed-spec" in {
        item["feedback_id"] for item in after_reveal["profile_feedback"]
    }
    assert after_reveal["profile_spec_quality"] != before["profile_spec_quality"]
    assert after_reveal["list_spec_quality"] != before["list_spec_quality"]
    assert after_reveal["metrics_spec_quality"] != before["metrics_spec_quality"]
    assert after_reveal["quarterly_spec_quality"] != before["quarterly_spec_quality"]
    assert after_reveal["sparkline_spec_quality"] != before["sparkline_spec_quality"]
