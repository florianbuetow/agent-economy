"""Tests for task endpoints."""
import sqlite3
from datetime import UTC, datetime, timedelta
import pytest


# === Task Drilldown Tests ===

@pytest.mark.unit
async def test_task_01_full_lifecycle(seeded_client):
    """TASK-01: Full task drilldown includes all fields."""
    response = await seeded_client.get("/api/tasks/t-1")
    assert response.status_code == 200
    data = response.json()
    for key in ["task_id", "poster", "worker", "title", "spec", "reward", "status", "deadlines", "timestamps", "bids", "assets", "feedback", "dispute"]:
        assert key in data
    assert data["task_id"] == "t-1"
    assert data["status"] == "approved"


@pytest.mark.unit
async def test_task_02_poster_worker_resolved(seeded_client):
    """TASK-02: Poster and worker resolved to names."""
    response = await seeded_client.get("/api/tasks/t-1")
    data = response.json()
    assert data["poster"]["name"] == "Alice"
    assert data["poster"]["agent_id"] == "a-alice"
    assert data["worker"]["name"] == "Bob"
    assert data["worker"]["agent_id"] == "a-bob"


@pytest.mark.unit
async def test_task_03_bids_include_delivery_quality(seeded_client):
    """TASK-03: Bids include bidder delivery quality."""
    response = await seeded_client.get("/api/tasks/t-1")
    data = response.json()
    assert len(data["bids"]) > 0
    for bid in data["bids"]:
        assert "bidder" in bid
        assert "delivery_quality" in bid["bidder"]
        dq = bid["bidder"]["delivery_quality"]
        for key in ["extremely_satisfied", "satisfied", "dissatisfied"]:
            assert key in dq


@pytest.mark.unit
async def test_task_04_accepted_bid_marked(seeded_client):
    """TASK-04: Exactly one bid has accepted=true."""
    response = await seeded_client.get("/api/tasks/t-1")
    data = response.json()
    accepted = [b for b in data["bids"] if b["accepted"]]
    assert len(accepted) == 1
    assert accepted[0]["bidder"]["agent_id"] == "a-bob"


@pytest.mark.unit
async def test_task_05_dispute_data(seeded_client):
    """TASK-05: Task with dispute includes full dispute data."""
    response = await seeded_client.get("/api/tasks/t-5")
    data = response.json()
    assert data["dispute"] is not None
    d = data["dispute"]
    assert "claim_id" in d
    assert len(d["reason"]) > 0
    assert d["rebuttal"] is not None
    assert "content" in d["rebuttal"]
    assert "submitted_at" in d["rebuttal"]
    assert d["ruling"] is not None
    assert d["ruling"]["worker_pct"] == 70
    assert "ruling_id" in d["ruling"]
    assert "summary" in d["ruling"]
    assert "ruled_at" in d["ruling"]


@pytest.mark.unit
async def test_task_06_no_dispute(seeded_client):
    """TASK-06: Task without dispute has null dispute."""
    response = await seeded_client.get("/api/tasks/t-1")
    data = response.json()
    assert data["dispute"] is None


@pytest.mark.unit
async def test_task_07_open_task_no_worker(seeded_client):
    """TASK-07: Open task has null worker."""
    response = await seeded_client.get("/api/tasks/t-4")
    data = response.json()
    assert data["worker"] is None
    assert data["status"] == "open"


@pytest.mark.unit
async def test_task_08_visible_feedback_only(seeded_client, seeded_db_path):
    """TASK-08: Feedback only includes visible entries."""
    # Add sealed feedback for t-1
    conn = sqlite3.connect(str(seeded_db_path))
    conn.execute("""
        INSERT INTO reputation_feedback VALUES
        ('fb-sealed-task', 't-1', 'a-charlie', 'a-bob', 'worker', 'delivery_quality', 'dissatisfied', 'sealed', '2026-01-01T00:00:00Z', 0)
    """)
    conn.commit()
    conn.close()

    response = await seeded_client.get("/api/tasks/t-1")
    data = response.json()
    for fb in data["feedback"]:
        assert fb["feedback_id"] != "fb-sealed-task"


@pytest.mark.unit
async def test_task_09_not_found(seeded_client):
    """TASK-09: Nonexistent task returns 404."""
    response = await seeded_client.get("/api/tasks/t-nonexistent")
    assert response.status_code == 404
    assert response.json()["error"] == "TASK_NOT_FOUND"


# === Competitive Tasks Tests ===

@pytest.mark.unit
async def test_comp_01_sorted_by_bid_count(seeded_client):
    """COMP-01: Tasks sorted by bid_count descending."""
    response = await seeded_client.get("/api/tasks/-/competitive?limit=5&status=all")
    assert response.status_code == 200
    data = response.json()
    counts = [t["bid_count"] for t in data["tasks"]]
    assert counts == sorted(counts, reverse=True)
    if counts:
        assert counts[0] == 3  # t-1 has 3 bids


@pytest.mark.unit
async def test_comp_02_default_status_open(seeded_client):
    """COMP-02: Default status filter is open."""
    response = await seeded_client.get("/api/tasks/-/competitive")
    data = response.json()
    for task in data["tasks"]:
        assert task["status"] in ("open", "accepted")


@pytest.mark.unit
async def test_comp_03_limit(seeded_client):
    """COMP-03: Limit parameter works."""
    response = await seeded_client.get("/api/tasks/-/competitive?limit=1&status=all")
    data = response.json()
    assert len(data["tasks"]) <= 1


@pytest.mark.unit
async def test_comp_04_empty_when_no_open(seeded_client, seeded_db_path):
    """COMP-04: Empty result when no open tasks with bids."""
    # Update all tasks to approved
    conn = sqlite3.connect(str(seeded_db_path))
    conn.execute("UPDATE board_tasks SET status = 'approved'")
    conn.commit()
    conn.close()
    response = await seeded_client.get("/api/tasks/-/competitive")
    data = response.json()
    assert data["tasks"] == []


# === Uncontested Tasks Tests ===

@pytest.mark.unit
async def test_uncon_01_zero_bids(seeded_client, seeded_db_path):
    """UNCON-01: Returns tasks with zero bids."""
    # Create a task with no bids, old enough
    now = datetime.now(UTC)
    created = (now - timedelta(minutes=25)).isoformat(timespec="seconds").replace("+00:00", "Z")
    deadline = (now + timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")

    conn = sqlite3.connect(str(seeded_db_path))
    conn.execute("""
        INSERT INTO bank_escrow VALUES ('esc-uncon', 'a-alice', 50, 't-uncon', 'locked', ?, NULL)
    """, (created,))
    conn.execute("""
        INSERT INTO board_tasks (task_id, poster_id, title, spec, reward, status,
            bidding_deadline_seconds, deadline_seconds, review_deadline_seconds,
            bidding_deadline, escrow_id, created_at)
        VALUES ('t-uncon', 'a-alice', 'Uncontested task', 'spec', 50, 'open',
            3600, 7200, 3600, ?, 'esc-uncon', ?)
    """, (deadline, created))
    conn.commit()
    conn.close()

    response = await seeded_client.get("/api/tasks/-/uncontested?min_age_minutes=10")
    data = response.json()
    task_ids = [t["task_id"] for t in data["tasks"]]
    assert "t-uncon" in task_ids
    uncon = next(t for t in data["tasks"] if t["task_id"] == "t-uncon")
    assert uncon["minutes_without_bids"] >= 20


@pytest.mark.unit
async def test_uncon_02_excludes_tasks_with_bids(seeded_client):
    """UNCON-02: Tasks with bids don't appear."""
    response = await seeded_client.get("/api/tasks/-/uncontested")
    data = response.json()
    # t-4 has 2 bids, should not appear
    task_ids = [t["task_id"] for t in data["tasks"]]
    assert "t-4" not in task_ids


@pytest.mark.unit
async def test_uncon_03_age_filter(seeded_client, seeded_db_path):
    """UNCON-03: Tasks younger than min_age_minutes excluded."""
    now = datetime.now(UTC)
    created = (now - timedelta(minutes=5)).isoformat(timespec="seconds").replace("+00:00", "Z")
    deadline = (now + timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")

    conn = sqlite3.connect(str(seeded_db_path))
    conn.execute("""
        INSERT INTO bank_escrow VALUES ('esc-young', 'a-bob', 30, 't-young', 'locked', ?, NULL)
    """, (created,))
    conn.execute("""
        INSERT INTO board_tasks (task_id, poster_id, title, spec, reward, status,
            bidding_deadline_seconds, deadline_seconds, review_deadline_seconds,
            bidding_deadline, escrow_id, created_at)
        VALUES ('t-young', 'a-bob', 'Young task', 'spec', 30, 'open',
            3600, 7200, 3600, ?, 'esc-young', ?)
    """, (deadline, created))
    conn.commit()
    conn.close()

    response = await seeded_client.get("/api/tasks/-/uncontested?min_age_minutes=10")
    data = response.json()
    task_ids = [t["task_id"] for t in data["tasks"]]
    assert "t-young" not in task_ids


@pytest.mark.unit
async def test_uncon_04_excludes_non_open(seeded_client):
    """UNCON-04: Only open tasks appear."""
    response = await seeded_client.get("/api/tasks/-/uncontested")
    data = response.json()
    # t-1 (approved) and t-3 (accepted) should not appear
    task_ids = [t["task_id"] for t in data["tasks"]]
    assert "t-1" not in task_ids
    assert "t-3" not in task_ids
