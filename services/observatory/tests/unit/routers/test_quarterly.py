"""Tests for quarterly report endpoint."""

import os
import sqlite3
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from observatory_service.app import create_app
from observatory_service.config import clear_settings_cache
from observatory_service.core.lifespan import lifespan
from observatory_service.core.state import reset_app_state


def _current_quarter_label() -> str:
    """Return the current quarter label, e.g. '2026-Q1'."""
    now = datetime.now(UTC)
    q = (now.month - 1) // 3 + 1
    return f"{now.year}-Q{q}"


def _insert_task(conn, params):
    """Insert a single board_tasks row."""
    conn.execute(
        """INSERT INTO board_tasks (
            task_id, poster_id, title, spec, reward, status,
            bidding_deadline_seconds, deadline_seconds,
            review_deadline_seconds,
            bidding_deadline, execution_deadline, review_deadline,
            escrow_id, worker_id, accepted_bid_id,
            dispute_reason, ruling_id, worker_pct,
            ruling_summary,
            created_at, accepted_at, submitted_at, approved_at,
            cancelled_at, disputed_at, ruled_at, expired_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?
        )""",
        params,
    )


# ---------------------------------------------------------------------------
# QTR-03 custom fixture: tasks in Q4 2025 and Q1 2026
# ---------------------------------------------------------------------------
@pytest.fixture
def q4q1_db_path(tmp_path, schema_sql):
    """Database with tasks spanning Q4 2025 (GDP=200) and Q1 2026 (GDP=234)."""
    db_file = tmp_path / "q4q1.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(schema_sql)

    # Register agents
    conn.executemany(
        "INSERT INTO identity_agents "
        "(agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        [
            ("a-alice", "Alice", "ed25519:alice-key", "2025-09-01T00:00:00Z"),
            ("a-bob", "Bob", "ed25519:bob-key", "2025-09-01T00:00:00Z"),
        ],
    )

    # Bank accounts
    conn.executemany(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
        [
            ("a-alice", 1000, "2025-09-01T00:00:00Z"),
            ("a-bob", 1000, "2025-09-01T00:00:00Z"),
        ],
    )

    # Escrow entries
    conn.executemany(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, "
        "status, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "esc-q4",
                "a-alice",
                200,
                "t-q4",
                "released",
                "2025-11-01T00:00:00Z",
                "2025-11-15T00:00:00Z",
            ),
            (
                "esc-q1a",
                "a-alice",
                200,
                "t-q1a",
                "released",
                "2026-01-10T00:00:00Z",
                "2026-01-20T00:00:00Z",
            ),
            (
                "esc-q1b",
                "a-alice",
                34,
                "t-q1b",
                "released",
                "2026-02-01T00:00:00Z",
                "2026-02-10T00:00:00Z",
            ),
        ],
    )

    # Q4 2025 task: approved, reward=200 -> GDP contribution = 200
    _insert_task(
        conn,
        (
            "t-q4",
            "a-alice",
            "Q4 task",
            "Task in Q4 2025",
            200,
            "approved",
            3600,
            7200,
            3600,
            "2025-11-02T00:00:00Z",
            "2025-11-03T00:00:00Z",
            "2025-11-04T00:00:00Z",
            "esc-q4",
            "a-bob",
            "bid-q4",
            None,
            None,
            None,
            None,
            "2025-11-01T00:00:00Z",
            "2025-11-02T00:00:00Z",
            "2025-11-10T00:00:00Z",
            "2025-11-15T00:00:00Z",
            None,
            None,
            None,
            None,
        ),
    )

    # Q1 2026 task a: approved, reward=200 -> GDP contribution = 200
    _insert_task(
        conn,
        (
            "t-q1a",
            "a-alice",
            "Q1 task A",
            "First task in Q1 2026",
            200,
            "approved",
            3600,
            7200,
            3600,
            "2026-01-11T00:00:00Z",
            "2026-01-12T00:00:00Z",
            "2026-01-13T00:00:00Z",
            "esc-q1a",
            "a-bob",
            "bid-q1a",
            None,
            None,
            None,
            None,
            "2026-01-10T00:00:00Z",
            "2026-01-11T00:00:00Z",
            "2026-01-15T00:00:00Z",
            "2026-01-20T00:00:00Z",
            None,
            None,
            None,
            None,
        ),
    )

    # Q1 2026 task b: approved, reward=34 -> GDP contribution = 34
    # Total Q1 GDP = 200 + 34 = 234
    _insert_task(
        conn,
        (
            "t-q1b",
            "a-alice",
            "Q1 task B",
            "Second task in Q1 2026",
            34,
            "approved",
            3600,
            7200,
            3600,
            "2026-02-02T00:00:00Z",
            "2026-02-03T00:00:00Z",
            "2026-02-04T00:00:00Z",
            "esc-q1b",
            "a-bob",
            "bid-q1b",
            None,
            None,
            None,
            None,
            "2026-02-01T00:00:00Z",
            "2026-02-02T00:00:00Z",
            "2026-02-05T00:00:00Z",
            "2026-02-10T00:00:00Z",
            None,
            None,
            None,
            None,
        ),
    )

    # Bids for the Q1 tasks (needed for labor market stats)
    conn.executemany(
        "INSERT INTO board_bids "
        "(bid_id, task_id, bidder_id, proposal, submitted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("bid-q4", "t-q4", "a-bob", "I can do Q4 task", "2025-11-01T01:00:00Z"),
            ("bid-q1a", "t-q1a", "a-bob", "I can do Q1 task A", "2026-01-10T01:00:00Z"),
            ("bid-q1b", "t-q1b", "a-bob", "I can do Q1 task B", "2026-02-01T01:00:00Z"),
        ],
    )

    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
async def q4q1_client(q4q1_db_path, tmp_path):
    """Async HTTP client backed by the Q4/Q1 database."""
    config_content = f"""
service:
  name: "observatory"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8006
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
database:
  path: "{q4q1_db_path}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  dist_path: "{tmp_path}/dist"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)

    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


# ===========================================================================
# QTR-01: Returns report for current quarter
# ===========================================================================
@pytest.mark.unit
async def test_qtr_01_current_quarter(seeded_client):
    """QTR-01: Default request returns report for current quarter."""
    response = await seeded_client.get("/api/quarterly-report")
    assert response.status_code == 200
    data = response.json()

    expected_quarter = _current_quarter_label()
    assert data["quarter"] == expected_quarter

    # Period fields are present and valid
    assert "period" in data
    assert "start" in data["period"]
    assert "end" in data["period"]

    # GDP total matches the standard economy: 100 + 50 + 84 = 234
    assert data["gdp"]["total"] == 234


# ===========================================================================
# QTR-02: Explicit quarter parameter
# ===========================================================================
@pytest.mark.unit
async def test_qtr_02_explicit_quarter(seeded_client):
    """QTR-02: Explicit quarter parameter returns matching quarter."""
    current = _current_quarter_label()
    response = await seeded_client.get(f"/api/quarterly-report?quarter={current}")
    assert response.status_code == 200
    data = response.json()
    assert data["quarter"] == current


# ===========================================================================
# QTR-03: GDP delta from previous quarter
# ===========================================================================
@pytest.mark.unit
async def test_qtr_03_gdp_delta(q4q1_client):
    """QTR-03: GDP delta computed correctly between Q4 2025 and Q1 2026."""
    response = await q4q1_client.get("/api/quarterly-report?quarter=2026-Q1")
    assert response.status_code == 200
    data = response.json()

    assert data["gdp"]["previous_quarter"] == 200
    # delta_pct = (234 - 200) / 200 * 100 = 17.0
    assert abs(data["gdp"]["delta_pct"] - 17.0) <= 1.0


# ===========================================================================
# QTR-04: Notable tasks are correct
# ===========================================================================
@pytest.mark.unit
async def test_qtr_04_notable(seeded_client):
    """QTR-04: Notable tasks and agents match the standard economy."""
    response = await seeded_client.get("/api/quarterly-report")
    assert response.status_code == 200
    data = response.json()
    notable = data["notable"]

    # Highest value task: t-5 with reward=120
    assert notable["highest_value_task"]["reward"] == 120

    # Most competitive task: t-1 with 3 bids
    assert notable["most_competitive_task"]["bid_count"] == 3

    # Top workers: at most 3, sorted by earnings descending
    top_workers = notable["top_workers"]
    assert len(top_workers) <= 3
    assert len(top_workers) >= 1
    # Bob earned 100 (t-1) + 84 (t-5) = 184; Charlie earned 50 (t-2)
    earnings = [w["earned"] for w in top_workers]
    assert earnings == sorted(earnings, reverse=True)
    assert top_workers[0]["earned"] == 184

    # Top posters: at most 3, sorted by spending descending
    top_posters = notable["top_posters"]
    assert len(top_posters) <= 3
    assert len(top_posters) >= 1
    # Alice spent 270 (100+50+120), Bob spent 80, Charlie spent 60
    spendings = [p["spent"] for p in top_posters]
    assert spendings == sorted(spendings, reverse=True)
    assert top_posters[0]["spent"] == 270


# ===========================================================================
# QTR-05: Quarter number out of range
# ===========================================================================
@pytest.mark.unit
async def test_qtr_05_quarter_out_of_range(seeded_client):
    """QTR-05: Q5 is out of range -> 400 INVALID_QUARTER."""
    response = await seeded_client.get("/api/quarterly-report?quarter=2026-Q5")
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_QUARTER"


# ===========================================================================
# QTR-06: Malformed quarter string
# ===========================================================================
@pytest.mark.unit
async def test_qtr_06_malformed_quarter(seeded_client):
    """QTR-06: Malformed quarter format -> 400 INVALID_QUARTER."""
    response = await seeded_client.get("/api/quarterly-report?quarter=Q1-2026")
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_QUARTER"


# ===========================================================================
# QTR-07: Quarter with no data
# ===========================================================================
@pytest.mark.unit
async def test_qtr_07_no_data(seeded_client):
    """QTR-07: Quarter with no economy data -> 404 NO_DATA."""
    response = await seeded_client.get("/api/quarterly-report?quarter=2020-Q1")
    assert response.status_code == 404
    assert response.json()["error"] == "NO_DATA"
