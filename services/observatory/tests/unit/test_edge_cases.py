"""Edge case and read-only enforcement tests."""

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from observatory_service.app import create_app
from observatory_service.config import clear_settings_cache
from observatory_service.core.lifespan import lifespan
from observatory_service.core.state import get_app_state, reset_app_state

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# test_edge_cases.py -> unit -> tests -> observatory -> services -> repo_root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_SCHEMA_SQL_PATH = _REPO_ROOT / "docs" / "specifications" / "schema.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ts(dt: datetime) -> str:
    """Format a datetime as ISO 8601 UTC with trailing Z."""
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_config(tmp_path: Path, db_path: str) -> Path:
    """Write a test config.yaml pointing at the given database path."""
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
  path: "{db_path}"
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
    return config_path


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


async def _make_client(tmp_path, db_path):
    """Create a test app + async client wired to the given database."""
    config_path = _write_config(tmp_path, str(db_path))
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    reset_app_state()
    test_app = create_app()
    async with lifespan(test_app):
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


def _create_schema_db(tmp_path: Path, name: str = "test.db") -> Path:
    """Create a temporary SQLite database with only the schema applied."""
    schema_sql = _SCHEMA_SQL_PATH.read_text()
    db_file = tmp_path / name
    conn = sqlite3.connect(str(db_file))
    conn.executescript(schema_sql)
    conn.close()
    return db_file


# ============================================================================
# EDGE-01: Empty database -- all endpoints return gracefully (no 500s)
# ============================================================================


@pytest.fixture
async def empty_edge_client(tmp_path):
    """Client backed by a schema-only database (no rows)."""
    db_path = _create_schema_db(tmp_path, "empty_edge.db")
    async for client in _make_client(tmp_path, db_path):
        yield client


@pytest.mark.unit
async def test_edge_01_empty_metrics(empty_edge_client):
    """EDGE-01a: GET /api/metrics returns 200 with zeros on empty DB."""
    r = await empty_edge_client.get("/api/metrics")
    assert r.status_code == 200
    data = r.json()
    assert data["gdp"]["total"] == 0
    assert data["agents"]["total_registered"] == 0
    assert data["agents"]["active"] == 0
    assert data["tasks"]["total_created"] == 0


@pytest.mark.unit
async def test_edge_01_empty_events(empty_edge_client):
    """EDGE-01b: GET /api/events returns 200 with empty events array."""
    r = await empty_edge_client.get("/api/events")
    assert r.status_code == 200
    data = r.json()
    assert data["events"] == []


@pytest.mark.unit
async def test_edge_01_empty_agents(empty_edge_client):
    """EDGE-01c: GET /api/agents returns 200 with empty agents array."""
    r = await empty_edge_client.get("/api/agents")
    assert r.status_code == 200
    data = r.json()
    assert data["agents"] == []


@pytest.mark.unit
async def test_edge_01_empty_competitive_tasks(empty_edge_client):
    """EDGE-01d: GET /api/tasks/-/competitive returns 200 with empty tasks."""
    r = await empty_edge_client.get("/api/tasks/-/competitive")
    assert r.status_code == 200
    data = r.json()
    assert data["tasks"] == []


@pytest.mark.unit
async def test_edge_01_empty_uncontested_tasks(empty_edge_client):
    """EDGE-01e: GET /api/tasks/-/uncontested returns 200 with empty tasks."""
    r = await empty_edge_client.get("/api/tasks/-/uncontested")
    assert r.status_code == 200
    data = r.json()
    assert data["tasks"] == []


@pytest.mark.unit
async def test_edge_01_empty_gdp_history(empty_edge_client):
    """EDGE-01f: GET /api/metrics/gdp/history returns 200 with empty or all-zero data_points."""
    r = await empty_edge_client.get("/api/metrics/gdp/history?window=1h&resolution=1m")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["data_points"], list)
    # All data points (if any) should have gdp == 0
    for dp in data["data_points"]:
        assert dp["gdp"] == 0


@pytest.mark.unit
async def test_edge_01_empty_quarterly_report(empty_edge_client):
    """EDGE-01g: GET /api/quarterly-report returns 404 with NO_DATA on empty DB."""
    r = await empty_edge_client.get("/api/quarterly-report")
    assert r.status_code == 404
    data = r.json()
    assert data["error"] == "NO_DATA"


@pytest.mark.unit
async def test_edge_01_no_500_errors(empty_edge_client):
    """EDGE-01h: None of the main endpoints return 500 on empty DB."""
    endpoints = [
        "/api/metrics",
        "/api/events",
        "/api/agents",
        "/api/tasks/-/competitive",
        "/api/tasks/-/uncontested",
        "/api/metrics/gdp/history?window=1h&resolution=1m",
        "/api/quarterly-report",
        "/health",
    ]
    for endpoint in endpoints:
        r = await empty_edge_client.get(endpoint)
        assert r.status_code != 500, f"{endpoint} returned 500: {r.text}"


# ============================================================================
# EDGE-02: Very long task spec text
# ============================================================================


@pytest.fixture
async def long_spec_client(tmp_path):
    """Client backed by a DB with a task that has a 10,000 character spec."""
    db_path = _create_schema_db(tmp_path, "long_spec.db")
    conn = sqlite3.connect(str(db_path))

    now = datetime.now(UTC)
    ts_now = _ts(now)
    bd = _ts(now + timedelta(hours=1))

    # Agent needed as poster
    conn.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        ("a-poster", "Poster", "ed25519:poster-key", ts_now),
    )
    conn.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
        ("a-poster", 1000, ts_now),
    )
    # Escrow for the task
    conn.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("esc-long", "a-poster", 100, "t-long", "locked", ts_now, None),
    )

    long_spec = "A" * 10_000
    _insert_task(
        conn,
        (
            "t-long",
            "a-poster",
            "Long Spec Task",
            long_spec,
            100,
            "open",
            3600,
            7200,
            3600,
            bd,
            None,
            None,
            "esc-long",
            None,
            None,
            None,
            None,
            None,
            None,
            ts_now,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    )

    conn.commit()
    conn.close()

    async for client in _make_client(tmp_path, db_path):
        yield client


@pytest.mark.unit
async def test_edge_02_very_long_task_spec(long_spec_client):
    """EDGE-02: Task with 10,000 character spec is returned fully."""
    r = await long_spec_client.get("/api/tasks/t-long")
    assert r.status_code == 200
    data = r.json()
    assert len(data["spec"]) == 10_000
    assert data["spec"] == "A" * 10_000


# ============================================================================
# EDGE-03: Agent with no activity
# ============================================================================


@pytest.fixture
async def inactive_agent_client(tmp_path):
    """Client backed by a DB with a single agent that has zero activity."""
    db_path = _create_schema_db(tmp_path, "inactive_agent.db")
    conn = sqlite3.connect(str(db_path))

    now = datetime.now(UTC)
    ts_now = _ts(now)

    conn.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        ("a-lonely", "Lonely Agent", "ed25519:lonely-key", ts_now),
    )
    conn.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
        ("a-lonely", 0, ts_now),
    )

    conn.commit()
    conn.close()

    async for client in _make_client(tmp_path, db_path):
        yield client


@pytest.mark.unit
async def test_edge_03_agent_with_no_activity(inactive_agent_client):
    """EDGE-03: Agent with zero activity has all-zero stats and empty arrays."""
    r = await inactive_agent_client.get("/api/agents/a-lonely")
    assert r.status_code == 200
    data = r.json()
    assert data["agent_id"] == "a-lonely"
    assert data["stats"]["tasks_posted"] == 0
    assert data["stats"]["tasks_completed_as_worker"] == 0
    assert data["stats"]["total_earned"] == 0
    assert data["recent_tasks"] == []
    assert data["recent_feedback"] == []


# ============================================================================
# EDGE-04: Task with no bids
# ============================================================================


@pytest.fixture
async def no_bids_client(tmp_path):
    """Client backed by a DB with a task in open state and zero bids."""
    db_path = _create_schema_db(tmp_path, "no_bids.db")
    conn = sqlite3.connect(str(db_path))

    now = datetime.now(UTC)
    ts_now = _ts(now)
    bd = _ts(now + timedelta(hours=1))

    conn.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        ("a-poster", "Poster", "ed25519:poster-key", ts_now),
    )
    conn.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
        ("a-poster", 1000, ts_now),
    )
    conn.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("esc-nobid", "a-poster", 75, "t-nobid", "locked", ts_now, None),
    )

    _insert_task(
        conn,
        (
            "t-nobid",
            "a-poster",
            "No Bids Task",
            "A task nobody wants",
            75,
            "open",
            3600,
            7200,
            3600,
            bd,
            None,
            None,
            "esc-nobid",
            None,
            None,
            None,
            None,
            None,
            None,
            ts_now,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    )

    conn.commit()
    conn.close()

    async for client in _make_client(tmp_path, db_path):
        yield client


@pytest.mark.unit
async def test_edge_04_task_with_no_bids(no_bids_client):
    """EDGE-04: Task with no bids has empty bids array and null worker."""
    r = await no_bids_client.get("/api/tasks/t-nobid")
    assert r.status_code == 200
    data = r.json()
    assert data["bids"] == []
    assert data["worker"] is None


# ============================================================================
# EDGE-05: Unicode in agent names and task titles
# ============================================================================


@pytest.fixture
async def unicode_client(tmp_path):
    """Client backed by a DB with Unicode agent names and task titles."""
    db_path = _create_schema_db(tmp_path, "unicode.db")
    conn = sqlite3.connect(str(db_path))

    now = datetime.now(UTC)
    ts_now = _ts(now)
    bd = _ts(now + timedelta(hours=1))

    # Agent with Chinese name
    conn.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        ("a-unicode", "\u6d4b\u8bd5\u4ee3\u7406", "ed25519:unicode-key", ts_now),
    )
    conn.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
        ("a-unicode", 500, ts_now),
    )
    conn.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("esc-uni", "a-unicode", 50, "t-unicode", "locked", ts_now, None),
    )

    # Task with French + emoji title
    unicode_title = "R\u00e9sum\u00e9 des donn\u00e9es \U0001f4ca"
    _insert_task(
        conn,
        (
            "t-unicode",
            "a-unicode",
            unicode_title,
            "Spec with unicode: \u00e9\u00e8\u00ea\u00eb",
            50,
            "open",
            3600,
            7200,
            3600,
            bd,
            None,
            None,
            "esc-uni",
            None,
            None,
            None,
            None,
            None,
            None,
            ts_now,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ),
    )

    conn.commit()
    conn.close()

    async for client in _make_client(tmp_path, db_path):
        yield client


@pytest.mark.unit
async def test_edge_05_unicode_agent_name(unicode_client):
    """EDGE-05a: Agent with Chinese name is returned with Unicode intact."""
    r = await unicode_client.get("/api/agents/a-unicode")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "\u6d4b\u8bd5\u4ee3\u7406"


@pytest.mark.unit
async def test_edge_05_unicode_task_title(unicode_client):
    """EDGE-05b: Task with French+emoji title is returned with Unicode intact."""
    r = await unicode_client.get("/api/tasks/t-unicode")
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "R\u00e9sum\u00e9 des donn\u00e9es \U0001f4ca"


# ============================================================================
# RO-01: Service operates with read-only database connection
# ============================================================================


@pytest.fixture
async def ro_app_state(tmp_path, schema_sql):
    """Start the app with lifespan and expose its state for read-only checks."""
    db_file = tmp_path / "ro_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(schema_sql)
    conn.close()

    config_path = _write_config(tmp_path, str(db_file))
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        state = get_app_state()
        yield state

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
async def test_ro_01_database_opened_read_only(ro_app_state):
    """RO-01: The database connection is opened in read-only mode."""
    db = ro_app_state.db
    assert db is not None

    # Attempting to write should raise an error
    with pytest.raises(Exception, match="readonly"):
        await db.execute("CREATE TABLE _ro_test (id INTEGER)")


# ============================================================================
# RO-02: All endpoints succeed with read-only connection
# ============================================================================


@pytest.fixture
async def ro_seeded_client(tmp_path, seeded_db_path):
    """Client backed by the standard seeded DB opened in read-only mode."""
    async for client in _make_client(tmp_path, seeded_db_path):
        yield client


@pytest.mark.unit
async def test_ro_02_all_endpoints_succeed_readonly(ro_seeded_client):
    """RO-02: All API endpoints succeed with a read-only database connection."""
    endpoints = [
        ("/api/metrics", 200),
        ("/api/events", 200),
        ("/api/agents", 200),
        ("/api/agents/a-alice", 200),
        ("/api/tasks/-/competitive", 200),
        ("/api/tasks/-/uncontested", 200),
        ("/api/tasks/t-1", 200),
        ("/api/metrics/gdp/history?window=1h&resolution=1m", 200),
        ("/api/quarterly-report", None),  # May be 200 or 404 depending on quarter
        ("/health", 200),
    ]
    for endpoint, expected_status in endpoints:
        r = await ro_seeded_client.get(endpoint)
        assert r.status_code != 500, f"{endpoint} returned 500 with read-only DB: {r.text}"
        if expected_status is not None:
            assert r.status_code == expected_status, (
                f"{endpoint} expected {expected_status}, got {r.status_code}: {r.text}"
            )
