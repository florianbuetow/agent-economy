"""Tests for metrics endpoints."""

import sqlite3

import pytest


@pytest.mark.unit
async def test_met_01_metrics_returns_all_fields(seeded_client):
    """MET-01: All required top-level keys present."""
    response = await seeded_client.get("/api/metrics")
    assert response.status_code == 200
    data = response.json()
    expected_keys = [
        "gdp",
        "agents",
        "tasks",
        "escrow",
        "spec_quality",
        "labor_market",
        "economy_phase",
        "computed_at",
    ]
    for key in expected_keys:
        assert key in data, f"Missing key: {key}"


@pytest.mark.unit
async def test_met_02_gdp_total(seeded_client):
    """MET-02: GDP total = 100 + 50 + 84 = 234."""
    response = await seeded_client.get("/api/metrics")
    data = response.json()
    assert data["gdp"]["total"] == 234


@pytest.mark.unit
async def test_met_03_gdp_per_agent(seeded_client):
    """MET-03: GDP per agent = total / active."""
    response = await seeded_client.get("/api/metrics")
    data = response.json()
    expected = data["gdp"]["total"] / data["agents"]["active"]
    assert abs(data["gdp"]["per_agent"] - expected) < 0.1


@pytest.mark.unit
async def test_met_04_active_agents_excludes_inactive(seeded_client, seeded_db_path):
    """MET-04: Dave (registered, no tasks) is not active."""
    conn = sqlite3.connect(str(seeded_db_path))
    conn.execute(
        "INSERT INTO identity_agents VALUES "
        "('a-dave', 'Dave', 'ed25519:dave_key', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    response = await seeded_client.get("/api/metrics")
    data = response.json()
    assert data["agents"]["total_registered"] == 4
    assert data["agents"]["active"] == 3


@pytest.mark.unit
async def test_met_05_tasks_by_status(seeded_client):
    """MET-05: Task status counts are correct."""
    response = await seeded_client.get("/api/metrics")
    data = response.json()
    assert data["tasks"]["completed_all_time"] == 2
    assert data["tasks"]["open"] >= 1
    assert data["tasks"]["in_execution"] >= 1


@pytest.mark.unit
async def test_met_06_completion_rate(seeded_client):
    """MET-06: Completion rate = 2 / (2+1) = 0.667."""
    response = await seeded_client.get("/api/metrics")
    data = response.json()
    assert abs(data["tasks"]["completion_rate"] - 0.667) < 0.01


@pytest.mark.unit
async def test_met_07_escrow_total(seeded_client):
    """MET-07: Escrow total locked = 140."""
    response = await seeded_client.get("/api/metrics")
    data = response.json()
    assert data["escrow"]["total_locked"] == 140


@pytest.mark.unit
async def test_met_08_spec_quality_sums_to_one(seeded_client):
    """MET-08: Spec quality percentages sum to 1.0."""
    response = await seeded_client.get("/api/metrics")
    sq = response.json()["spec_quality"]
    total = sq["extremely_satisfied_pct"] + sq["satisfied_pct"] + sq["dissatisfied_pct"]
    assert abs(total - 1.0) < 0.01


@pytest.mark.unit
async def test_met_09_spec_quality_ignores_sealed(seeded_client, seeded_db_path):
    """MET-09: Sealed feedback does not affect spec quality."""
    response1 = await seeded_client.get("/api/metrics")
    sq_before = response1.json()["spec_quality"]

    conn = sqlite3.connect(str(seeded_db_path))
    conn.execute("""
        INSERT INTO reputation_feedback VALUES
        ('fb-sealed-extra', 't-1', 'a-charlie', 'a-alice', 'worker',
         'spec_quality', 'dissatisfied', 'bad',
         '2026-01-01T00:00:00Z', 0)
    """)
    conn.commit()
    conn.close()

    response2 = await seeded_client.get("/api/metrics")
    sq_after = response2.json()["spec_quality"]
    assert sq_before["extremely_satisfied_pct"] == sq_after["extremely_satisfied_pct"]


@pytest.mark.unit
async def test_met_10_avg_bids_per_task(seeded_client):
    """MET-10: Avg bids per task computed across tasks with bids."""
    response = await seeded_client.get("/api/metrics")
    data = response.json()
    assert data["labor_market"]["avg_bids_per_task"] > 0


@pytest.mark.unit
async def test_met_11_reward_distribution(seeded_client):
    """MET-11: Reward distribution buckets correct."""
    response = await seeded_client.get("/api/metrics")
    rd = response.json()["labor_market"]["reward_distribution"]
    assert rd["0_to_10"] == 0
    assert rd["11_to_50"] == 1  # 50
    assert rd["51_to_100"] == 2  # 80, 60
    assert rd["over_100"] == 2  # 100, 120


@pytest.mark.unit
async def test_met_12_stalled_when_no_tasks(empty_client):
    """MET-12: Economy phase is stalled when no tasks."""
    response = await empty_client.get("/api/metrics")
    data = response.json()
    assert data["economy_phase"]["phase"] == "stalled"


@pytest.mark.unit
async def test_met_13_empty_database(empty_client):
    """MET-13: All zeros on empty database."""
    response = await empty_client.get("/api/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["gdp"]["total"] == 0
    assert data["agents"]["total_registered"] == 0
    assert data["agents"]["active"] == 0
    assert data["tasks"]["total_created"] == 0
    assert data["economy_phase"]["phase"] == "stalled"


# GDP History tests


@pytest.mark.unit
async def test_gdp_01_returns_data_points(seeded_client):
    """GDP-01: Returns data points for 1h window."""
    response = await seeded_client.get("/api/metrics/gdp/history?window=1h&resolution=1m")
    assert response.status_code == 200
    data = response.json()
    assert data["window"] == "1h"
    assert data["resolution"] == "1m"
    assert isinstance(data["data_points"], list)
    assert len(data["data_points"]) <= 60
    for dp in data["data_points"]:
        assert "timestamp" in dp
        assert "gdp" in dp
        assert dp["gdp"] >= 0


@pytest.mark.unit
async def test_gdp_02_monotonically_non_decreasing(seeded_client):
    """GDP-02: GDP is monotonically non-decreasing."""
    response = await seeded_client.get("/api/metrics/gdp/history?window=1h&resolution=1m")
    data = response.json()
    points = data["data_points"]
    for i in range(1, len(points)):
        assert points[i]["gdp"] >= points[i - 1]["gdp"]


@pytest.mark.unit
async def test_gdp_03_invalid_window(seeded_client):
    """GDP-03: Invalid window returns 400."""
    response = await seeded_client.get("/api/metrics/gdp/history?window=2h")
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_PARAMETER"


@pytest.mark.unit
async def test_gdp_04_invalid_resolution(seeded_client):
    """GDP-04: Invalid resolution returns 400."""
    response = await seeded_client.get("/api/metrics/gdp/history?window=1h&resolution=30s")
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_PARAMETER"


@pytest.mark.unit
async def test_gdp_05_empty_database(empty_client):
    """GDP-05: Empty database returns zero data points."""
    response = await empty_client.get("/api/metrics/gdp/history?window=1h&resolution=1m")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["data_points"], list)
