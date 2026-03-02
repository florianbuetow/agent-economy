"""Integration tests for task APIs."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# Competitive
async def test_competitive_only_tasks_with_bids(client):
    response = await client.get("/api/tasks/-/competitive")
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert tasks
    assert all(task["bid_count"] > 0 for task in tasks)


async def test_competitive_sorted_by_bid_count_desc(client):
    response = await client.get("/api/tasks/-/competitive")
    assert response.status_code == 200
    bid_counts = [task["bid_count"] for task in response.json()["tasks"]]
    assert bid_counts == sorted(bid_counts, reverse=True)


async def test_competitive_default_status_open(client):
    response = await client.get("/api/tasks/-/competitive")
    assert response.status_code == 200
    statuses = {task["status"] for task in response.json()["tasks"]}
    assert statuses.issubset({"open", "accepted"})


async def test_competitive_limit(client):
    response = await client.get("/api/tasks/-/competitive?limit=2")
    assert response.status_code == 200
    assert len(response.json()["tasks"]) <= 2


async def test_competitive_includes_poster_info(client):
    response = await client.get("/api/tasks/-/competitive")
    assert response.status_code == 200
    for task in response.json()["tasks"]:
        assert set(task["poster"].keys()) == {"agent_id", "name"}


async def test_competitive_staleness_new_bid(client, write_db):
    before = await client.get("/api/tasks/-/competitive")
    assert before.status_code == 200
    before_ids = {task["task_id"] for task in before.json()["tasks"]}
    assert "t-task7" not in before_ids

    await write_db.execute(
        "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "bid-13",
            "t-task7",
            "a-eve",
            "Fresh bid for staleness test",
            "2026-03-02T06:10:00Z",
        ),
    )
    await write_db.commit()

    after = await client.get("/api/tasks/-/competitive")
    assert after.status_code == 200
    after_ids = {task["task_id"] for task in after.json()["tasks"]}
    assert "t-task7" in after_ids


# Uncontested
async def test_uncontested_open_tasks_without_bids(client):
    response = await client.get("/api/tasks/-/uncontested")
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert tasks
    task_ids = {task["task_id"] for task in tasks}

    for task_id in task_ids:
        drilldown = await client.get(f"/api/tasks/{task_id}")
        assert drilldown.status_code == 200
        assert drilldown.json()["status"] == "open"
        assert drilldown.json()["bids"] == []


async def test_uncontested_min_age_filter(client):
    response = await client.get("/api/tasks/-/uncontested?min_age_minutes=0")
    assert response.status_code == 200
    assert isinstance(response.json()["tasks"], list)


async def test_uncontested_limit(client):
    response = await client.get("/api/tasks/-/uncontested?limit=1")
    assert response.status_code == 200
    assert len(response.json()["tasks"]) == 1


async def test_uncontested_has_minutes_field(client):
    response = await client.get("/api/tasks/-/uncontested")
    assert response.status_code == 200
    for task in response.json()["tasks"]:
        assert task["minutes_without_bids"] > 0


async def test_uncontested_staleness_bid_removes_task(client, write_db):
    before = await client.get("/api/tasks/-/uncontested?min_age_minutes=0")
    assert before.status_code == 200
    tasks = before.json()["tasks"]
    assert tasks

    task_id = tasks[0]["task_id"]
    bid_id = f"bid-stale-{task_id}"

    await write_db.execute(
        "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            bid_id,
            task_id,
            "a-alice",
            "Bid to remove task from uncontested list",
            "2026-03-02T06:20:00Z",
        ),
    )
    await write_db.commit()

    after = await client.get("/api/tasks/-/uncontested?min_age_minutes=0")
    assert after.status_code == 200
    after_ids = {task["task_id"] for task in after.json()["tasks"]}
    assert task_id not in after_ids


# Drilldown
async def test_drilldown_approved_task_full(client):
    response = await client.get("/api/tasks/t-task1")
    assert response.status_code == 200
    data = response.json()
    assert data["poster"]["agent_id"] == "a-alice"
    assert data["worker"]["agent_id"] == "a-bob"
    assert len(data["bids"]) == 2
    assert len(data["assets"]) == 2
    assert data["feedback"]


async def test_drilldown_not_found_returns_404(client):
    response = await client.get("/api/tasks/t-nonexistent")
    assert response.status_code == 404
    assert response.json()["error"] == "task_not_found"


async def test_drilldown_bids_with_delivery_quality(client):
    response = await client.get("/api/tasks/t-task1")
    assert response.status_code == 200
    for bid in response.json()["bids"]:
        assert set(bid["bidder"]["delivery_quality"].keys()) == {
            "extremely_satisfied",
            "satisfied",
            "dissatisfied",
        }


async def test_drilldown_accepted_bid_flag(client):
    response = await client.get("/api/tasks/t-task1")
    assert response.status_code == 200
    bids = {bid["bid_id"]: bid for bid in response.json()["bids"]}
    assert bids["bid-1"]["accepted"] is True
    assert bids["bid-2"]["accepted"] is False


async def test_drilldown_disputed_task_has_dispute(client):
    response = await client.get("/api/tasks/t-task5")
    assert response.status_code == 200
    dispute = response.json()["dispute"]
    assert dispute is not None
    assert dispute["claim_id"] == "clm-1"
    assert dispute["rebuttal"] is not None
    assert dispute["ruling"] is not None


async def test_drilldown_non_disputed_has_null_dispute(client):
    response = await client.get("/api/tasks/t-task1")
    assert response.status_code == 200
    assert response.json()["dispute"] is None


async def test_drilldown_feedback_visible_only(client):
    response = await client.get("/api/tasks/t-task3")
    assert response.status_code == 200
    feedback_ids = {fb["feedback_id"] for fb in response.json()["feedback"]}
    assert "fb-8" not in feedback_ids


async def test_drilldown_staleness_new_bid(client, write_db):
    before = await client.get("/api/tasks/t-task1")
    assert before.status_code == 200
    before_count = len(before.json()["bids"])

    await write_db.execute(
        "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "bid-99",
            "t-task1",
            "a-dave",
            "Additional bid for staleness",
            "2026-03-02T06:30:00Z",
        ),
    )
    await write_db.commit()

    after = await client.get("/api/tasks/t-task1")
    assert after.status_code == 200
    assert len(after.json()["bids"]) == before_count + 1
