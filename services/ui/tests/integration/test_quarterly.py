"""Integration tests for quarterly report API."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def _insert_approved_task_for_q1(write_db, task_id: str, reward: int) -> None:
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
            f"Quarterly {task_id}",
            "Quarterly staleness task",
            reward,
            "approved",
            86400,
            604800,
            172800,
            "2026-03-02T05:30:00Z",
            None,
            None,
            f"esc-{task_id}",
            "a-bob",
            None,
            None,
            None,
            None,
            None,
            "2026-03-02T05:30:00Z",
            None,
            None,
            "2026-03-02T06:30:00Z",
            None,
            None,
            None,
            None,
        ),
    )


async def test_quarterly_explicit_2026_q1(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200


async def test_quarterly_default_current_quarter(client):
    response = await client.get("/api/quarterly-report")
    assert response.status_code == 200
    data = response.json()
    assert "quarter" in data
    assert "gdp" in data


async def test_quarterly_invalid_format_returns_400(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q5")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_quarter"


async def test_quarterly_invalid_year_returns_400(client):
    response = await client.get("/api/quarterly-report?quarter=ABCD-Q1")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_quarter"


async def test_quarterly_no_data_returns_404(client):
    response = await client.get("/api/quarterly-report?quarter=2020-Q1")
    assert response.status_code == 404
    assert response.json()["error"] == "no_data"


async def test_quarterly_gdp_total(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    assert response.json()["gdp"]["total"] == 350


async def test_quarterly_gdp_previous_quarter_zero(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    assert response.json()["gdp"]["previous_quarter"] == 0


async def test_quarterly_period_boundaries(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    period = response.json()["period"]
    assert period["start"] == "2026-01-01T00:00:00Z"
    assert period["end"].endswith("T23:59:59Z")
    assert period["start"] < period["end"]


async def test_quarterly_tasks_posted(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    assert response.json()["tasks"]["posted"] == 12


async def test_quarterly_tasks_completed(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    assert response.json()["tasks"]["completed"] == 2


async def test_quarterly_tasks_disputed(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    assert response.json()["tasks"]["disputed"] >= 1


async def test_quarterly_labor_avg_bids(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    assert response.json()["labor_market"]["avg_bids_per_task"] > 0


async def test_quarterly_agents_registrations(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    assert response.json()["agents"]["new_registrations"] == 5


async def test_quarterly_agents_total_at_end(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    assert response.json()["agents"]["total_at_quarter_end"] == 5


async def test_quarterly_notable_highest_value_task(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    highest = response.json()["notable"]["highest_value_task"]
    assert highest["reward"] == 300
    assert highest["task_id"] == "t-task9"


async def test_quarterly_notable_top_workers(client):
    response = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    assert isinstance(response.json()["notable"]["top_workers"], list)


async def test_quarterly_staleness_new_approved_task(client, write_db):
    before = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert before.status_code == 200
    assert before.json()["gdp"]["total"] == 350

    await _insert_approved_task_for_q1(write_db, task_id="t-quarterly-1", reward=100)
    await write_db.commit()

    after = await client.get("/api/quarterly-report?quarter=2026-Q1")
    assert after.status_code == 200
    assert after.json()["gdp"]["total"] == 450
