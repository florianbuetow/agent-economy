"""Integration tests for metrics APIs."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def _insert_task(
    write_db,
    task_id: str,
    reward: int,
    status: str,
    created_at: str,
    approved_at: str | None,
) -> None:
    await write_db.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, execution_deadline, "
        "review_deadline, escrow_id, worker_id, accepted_bid_id, dispute_reason, ruling_id, "
        "worker_pct, ruling_summary, created_at, accepted_at, submitted_at, approved_at, "
        "cancelled_at, disputed_at, ruled_at, expired_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            "a-alice",
            f"Task {task_id}",
            "Generated task spec",
            reward,
            status,
            86400,
            604800,
            172800,
            created_at,
            None,
            None,
            f"esc-{task_id}",
            "a-bob" if status == "approved" else None,
            None,
            None,
            None,
            None,
            None,
            created_at,
            None,
            None,
            approved_at,
            None,
            None,
            None,
            None,
        ),
    )


async def test_gdp_total(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["gdp"]["total"] == 350


async def test_gdp_per_agent(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert isinstance(response.json()["gdp"]["per_agent"], int | float)


async def test_gdp_rate_per_hour(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    rate = response.json()["gdp"]["rate_per_hour"]
    assert isinstance(rate, int | float)
    assert rate >= 0


async def test_gdp_staleness(client, write_db):
    before = await client.get("/api/metrics")
    assert before.status_code == 200
    assert before.json()["gdp"]["total"] == 350

    await _insert_task(
        write_db,
        task_id="t-stale-gdp",
        reward=100,
        status="approved",
        created_at="2026-03-02T05:25:00Z",
        approved_at="2026-03-02T05:55:00Z",
    )
    await write_db.commit()

    after = await client.get("/api/metrics")
    assert after.status_code == 200
    assert after.json()["gdp"]["total"] == 450


async def test_agents_total_registered(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["agents"]["total_registered"] == 5


async def test_agents_with_completed_tasks(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["agents"]["with_completed_tasks"] >= 1


async def test_agents_active(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    active = response.json()["agents"]["active"]
    assert isinstance(active, int)
    assert active >= 0


async def test_tasks_total_created(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["tasks"]["total_created"] == 12


async def test_tasks_completed_all_time(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["tasks"]["completed_all_time"] == 2


async def test_tasks_open(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["tasks"]["open"] == 4


async def test_tasks_in_execution(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["tasks"]["in_execution"] == 2


async def test_tasks_disputed(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["tasks"]["disputed"] == 2


async def test_tasks_completion_rate(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    rate = response.json()["tasks"]["completion_rate"]
    assert isinstance(rate, int | float)
    assert 0 <= rate <= 1


async def test_tasks_staleness(client, write_db):
    before = await client.get("/api/metrics")
    assert before.status_code == 200
    assert before.json()["tasks"]["open"] == 4

    await _insert_task(
        write_db,
        task_id="t-stale-open",
        reward=42,
        status="open",
        created_at="2026-03-02T06:25:00Z",
        approved_at=None,
    )
    await write_db.commit()

    after = await client.get("/api/metrics")
    assert after.status_code == 200
    assert after.json()["tasks"]["open"] == 5


async def test_escrow_total_locked(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["escrow"]["total_locked"] == 250


async def test_escrow_staleness(client, write_db):
    before = await client.get("/api/metrics")
    assert before.status_code == 200
    before_total = before.json()["escrow"]["total_locked"]

    await write_db.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "esc-stale-1",
            "a-bob",
            40,
            "t-stale-open",
            "locked",
            "2026-03-02T06:30:00Z",
            None,
        ),
    )
    await write_db.commit()

    after = await client.get("/api/metrics")
    assert after.status_code == 200
    assert after.json()["escrow"]["total_locked"] == before_total + 40


async def test_spec_quality_avg_and_breakdown(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    spec = response.json()["spec_quality"]
    assert isinstance(spec["avg_score"], int | float)
    assert isinstance(spec["extremely_satisfied_pct"], int | float)
    assert isinstance(spec["satisfied_pct"], int | float)
    assert isinstance(spec["dissatisfied_pct"], int | float)


async def test_spec_quality_trend(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    spec = response.json()["spec_quality"]
    assert spec["trend_direction"] in {"improving", "declining", "stable"}
    assert isinstance(spec["trend_delta"], int | float)


async def test_labor_avg_bids_and_reward(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    labor = response.json()["labor_market"]
    assert labor["avg_bids_per_task"] > 0
    assert labor["avg_reward"] > 0


async def test_labor_reward_distribution_buckets(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    buckets = response.json()["labor_market"]["reward_distribution"]
    assert set(buckets.keys()) == {"0_to_10", "11_to_50", "51_to_100", "over_100"}


async def test_gdp_history_valid_params(client):
    response = await client.get("/api/metrics/gdp/history?window=24h&resolution=1h")
    assert response.status_code == 200
    data = response.json()
    assert data["window"] == "24h"
    assert data["resolution"] == "1h"
    assert isinstance(data["data_points"], list)


async def test_gdp_history_invalid_window_returns_400(client):
    response = await client.get("/api/metrics/gdp/history?window=2h&resolution=1h")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_parameter"
