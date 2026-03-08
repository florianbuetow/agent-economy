"""Integration tests for agent APIs."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# List agents
async def test_list_returns_all_agents(client):
    response = await client.get("/api/agents")
    assert response.status_code == 200
    assert response.json()["total_count"] == 5


async def test_list_default_sort_total_earned_desc(client):
    response = await client.get("/api/agents")
    assert response.status_code == 200
    first = response.json()["agents"][0]
    assert first["agent_id"] == "a-bob"
    assert first["stats"]["total_earned"] == 200


async def test_list_sort_by_total_spent(client):
    response = await client.get("/api/agents?sort_by=total_spent")
    assert response.status_code == 200
    assert response.json()["agents"][0]["agent_id"] == "a-alice"
    assert response.json()["agents"][0]["stats"]["total_spent"] == 450


async def test_list_sort_by_tasks_posted(client):
    response = await client.get("/api/agents?sort_by=tasks_posted")
    assert response.status_code == 200
    assert response.json()["agents"][0]["agent_id"] == "a-alice"
    assert response.json()["agents"][0]["stats"]["tasks_posted"] == 4


async def test_list_sort_by_tasks_completed(client):
    response = await client.get("/api/agents?sort_by=tasks_completed")
    assert response.status_code == 200
    assert response.json()["agents"][0]["agent_id"] == "a-bob"
    assert response.json()["agents"][0]["stats"]["tasks_completed_as_worker"] == 2


async def test_list_sort_by_spec_quality(client):
    response = await client.get("/api/agents?sort_by=spec_quality")
    assert response.status_code == 200
    assert response.json()["agents"][0]["agent_id"] == "a-alice"


async def test_list_sort_by_delivery_quality(client):
    response = await client.get("/api/agents?sort_by=delivery_quality")
    assert response.status_code == 200
    assert response.json()["agents"][0]["agent_id"] == "a-bob"


async def test_list_sort_asc_reverses(client):
    desc_response = await client.get("/api/agents?sort_by=total_earned&order=desc")
    asc_response = await client.get("/api/agents?sort_by=total_earned&order=asc")

    top_desc_id = desc_response.json()["agents"][0]["agent_id"]
    last_asc_id = asc_response.json()["agents"][-1]["agent_id"]
    assert top_desc_id == last_asc_id


async def test_list_pagination_limit_offset(client):
    response = await client.get("/api/agents?limit=2&offset=2")
    assert response.status_code == 200
    assert len(response.json()["agents"]) == 2
    assert response.json()["total_count"] == 5
    assert response.json()["limit"] == 2
    assert response.json()["offset"] == 2


async def test_list_invalid_sort_returns_400(client):
    response = await client.get("/api/agents?sort_by=invalid")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_parameter"


async def test_list_agent_stats_structure(client):
    response = await client.get("/api/agents")
    assert response.status_code == 200
    stats = response.json()["agents"][0]["stats"]
    assert set(stats.keys()) == {
        "tasks_posted",
        "tasks_completed_as_worker",
        "total_earned",
        "total_spent",
        "spec_quality",
        "delivery_quality",
        "current_streak",
    }
    assert set(stats["spec_quality"].keys()) == {
        "extremely_satisfied",
        "satisfied",
        "dissatisfied",
    }
    assert set(stats["delivery_quality"].keys()) == {
        "extremely_satisfied",
        "satisfied",
        "dissatisfied",
    }


# Agent profile
async def test_profile_returns_correct_agent(client):
    response = await client.get("/api/agents/a-alice")
    assert response.status_code == 200
    assert response.json()["name"] == "Alice"


async def test_profile_not_found_returns_404(client):
    response = await client.get("/api/agents/a-nonexistent")
    assert response.status_code == 404
    assert response.json()["error"] == "agent_not_found"


async def test_profile_balance_matches_seed(client):
    response = await client.get("/api/agents/a-alice")
    assert response.status_code == 200
    assert response.json()["balance"] == 800


async def test_profile_recent_tasks_included(client):
    response = await client.get("/api/agents/a-alice")
    assert response.status_code == 200
    assert isinstance(response.json()["recent_tasks"], list)
    assert len(response.json()["recent_tasks"]) >= 4


async def test_profile_recent_tasks_role_field(client):
    response = await client.get("/api/agents/a-alice")
    assert response.status_code == 200
    roles = {item["role"] for item in response.json()["recent_tasks"]}
    assert roles.issubset({"poster", "worker"})


async def test_profile_feedback_excludes_invisible(client):
    response = await client.get("/api/agents/a-bob")
    assert response.status_code == 200
    feedback_ids = {item["feedback_id"] for item in response.json()["recent_feedback"]}
    assert "fb-8" not in feedback_ids


async def test_profile_balance_staleness(client, write_db):
    await write_db.execute(
        "UPDATE bank_accounts SET balance = ? WHERE account_id = ?",
        (999, "a-alice"),
    )
    await write_db.commit()

    response = await client.get("/api/agents/a-alice")
    assert response.status_code == 200
    assert response.json()["balance"] == 999


# Agent feed
async def test_feed_returns_agent_events(client):
    response = await client.get("/api/agents/a-alice/feed")
    assert response.status_code == 200
    events = response.json()["events"]
    assert events
    for event in events:
        assert (
            event["agent_id"] == "a-alice"
            or event["poster_id"] == "a-alice"
            or event["worker_id"] == "a-alice"
        )


async def test_feed_pagination_before(client):
    first_page = await client.get("/api/agents/a-alice/feed?limit=3")
    assert first_page.status_code == 200
    first_events = first_page.json()["events"]
    assert len(first_events) == 3

    before_id = first_events[-1]["event_id"]
    second_page = await client.get(f"/api/agents/a-alice/feed?limit=3&before={before_id}")
    assert second_page.status_code == 200

    for event in second_page.json()["events"]:
        assert event["event_id"] < before_id


async def test_feed_has_more_flag(client):
    response = await client.get("/api/agents/a-alice/feed?limit=1")
    assert response.status_code == 200
    assert response.json()["has_more"] is True


# Agent earnings
async def test_earnings_total_and_avg(client):
    response = await client.get("/api/agents/a-bob/earnings")
    assert response.status_code == 200
    data = response.json()
    assert data["total_earned"] == 200
    assert data["tasks_approved"] == 2
    assert data["avg_per_task"] == 100


async def test_earnings_cumulative_data_points(client):
    response = await client.get("/api/agents/a-bob/earnings")
    assert response.status_code == 200
    data_points = response.json()["data_points"]

    cumulatives = [point["cumulative"] for point in data_points]
    assert cumulatives == sorted(cumulatives)


async def test_earnings_no_earnings_agent(client):
    response = await client.get("/api/agents/a-dave/earnings")
    assert response.status_code == 200
    data = response.json()
    assert data["total_earned"] == 0
    assert data["tasks_approved"] == 0
    assert data["avg_per_task"] == 0


async def test_earnings_staleness(client, write_db):
    baseline = await client.get("/api/agents/a-bob/earnings")
    assert baseline.status_code == 200
    before_total = baseline.json()["total_earned"]

    await write_db.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "tx-11",
            "a-bob",
            "escrow_release",
            100,
            1300,
            "esc-7",
            "2026-03-02T06:30:00Z",
        ),
    )
    await write_db.commit()

    after = await client.get("/api/agents/a-bob/earnings")
    assert after.status_code == 200
    assert after.json()["total_earned"] == before_total + 100
