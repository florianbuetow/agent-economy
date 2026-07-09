"""Integration tests for economy phase computation (GAP-A11 hotfix, T-001).

Covers observatory-service-tests.md MET-12/MET-13 (stalled phase on an empty
database) and the observatory-service-specs.md Economy Phases table's
`stable` dispute-rate ceiling, which the pre-fix `else -> "stable"` fallback
silently dropped.

Uses a schema-only database (no seed data) so task counts and dispute rates
can be constructed exactly, and freezes the injectable clock seam
(`ui_service.services.database._clock`, see commit cac136d) so the 60-minute
/ 3.5-day / 7-day windows are deterministic regardless of wall-clock time.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from ui_service.app import create_app
from ui_service.config import clear_settings_cache
from ui_service.core.lifespan import lifespan
from ui_service.core.state import reset_app_state
from ui_service.services import database as database_service

pytestmark = pytest.mark.integration

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"
FROZEN_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def empty_db_path(tmp_path: Path) -> Path:
    """Create a schema-only SQLite database with no rows."""
    db_file = tmp_path / "empty_economy.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
async def empty_app(empty_db_path: Path, tmp_path: Path):
    """Create test app pointing at the empty (unseeded) database."""
    web_dir = tmp_path / "web"
    web_dir.mkdir(exist_ok=True)
    (web_dir / "index.html").write_text("<html><body>Test</body></html>")

    config_content = f"""\
service:
  name: "ui"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
logging:
  level: "WARNING"
  directory: "{tmp_path / "logs"}"
database:
  path: "{empty_db_path}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "{web_dir}"
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
        yield test_app

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


@pytest.fixture
async def empty_client(empty_app):
    """Async HTTP client for the empty-DB test app."""
    transport = ASGITransport(app=empty_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def empty_write_db(empty_db_path: Path):
    """Writable connection to the empty test DB for seeding phase scenarios."""
    conn = await aiosqlite.connect(str(empty_db_path))
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys = OFF")
    yield conn
    await conn.close()


async def _insert_task(
    write_db: aiosqlite.Connection,
    task_id: str,
    status: str,
    created_at: datetime,
) -> None:
    """Insert a minimal board_tasks row (FK checks disabled on this connection)."""
    created_at_iso = database_service.to_iso(created_at)
    await write_db.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, escrow_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            "a-poster",
            f"Task {task_id}",
            "Generated task spec",
            10,
            status,
            86400,
            604800,
            172800,
            created_at_iso,
            f"esc-{task_id}",
            created_at_iso,
        ),
    )


async def _seed_stable_trend_with_dispute_rate(
    write_db: aiosqlite.Connection,
    disputed_filler_count: int,
    open_filler_count: int,
) -> None:
    """Seed a flat (stable) task-creation trend with a controllable dispute rate.

    4 tasks 10 minutes ago (current 3.5-day period, also within the 60-minute
    "recent" window so the phase isn't "stalled") + 4 tasks 5 days ago
    (previous 3.5-7 day period) => equal counts => task_creation_trend
    == "stable" (ratio 1.0, within the +/-5% tolerance).

    `disputed_filler_count` + `open_filler_count` tasks dated 30 days ago
    (outside the 7-day trend window, so they don't affect the trend) pad
    `total_created` and `disputed_count` to produce an exact dispute_rate.
    """
    for i in range(4):
        await _insert_task(write_db, f"t-current-{i}", "open", FROZEN_NOW - timedelta(minutes=10))
    for i in range(4):
        await _insert_task(write_db, f"t-previous-{i}", "open", FROZEN_NOW - timedelta(days=5))
    filler_ts = FROZEN_NOW - timedelta(days=30)
    for i in range(disputed_filler_count):
        await _insert_task(write_db, f"t-filler-disputed-{i}", "disputed", filler_ts)
    for i in range(open_filler_count):
        await _insert_task(write_db, f"t-filler-open-{i}", "open", filler_ts)
    await write_db.commit()


async def test_economy_phase_stalled_on_empty_db(empty_client: AsyncClient) -> None:
    """MET-12/MET-13: empty DB has no tasks in the last 60 minutes -> stalled.

    observatory-service-specs.md Economy Phases table: `stalled` = "No tasks
    created in the last 60 minutes."
    """
    response = await empty_client.get("/api/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["economy_phase"]["phase"] == "stalled"


async def test_economy_phase_stable_below_dispute_ceiling(
    empty_client: AsyncClient,
    empty_write_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flat trend + dispute_rate 10% (< 15% ceiling) -> stable.

    observatory-service-specs.md Economy Phases table: `stable` = "Task
    creation rate flat (+/-5%) AND dispute rate < 15%."
    """
    monkeypatch.setattr(database_service, "_clock", lambda: FROZEN_NOW)
    # 20 total tasks (4 + 4 trend tasks + 12 filler), 2 disputed -> 10%.
    await _seed_stable_trend_with_dispute_rate(
        empty_write_db, disputed_filler_count=2, open_filler_count=10
    )

    response = await empty_client.get("/api/metrics")
    assert response.status_code == 200
    phase = response.json()["economy_phase"]
    assert phase["task_creation_trend"] == "stable"
    assert phase["dispute_rate"] == 0.1
    assert phase["phase"] == "stable"


async def test_economy_phase_contracting_above_dispute_threshold(
    empty_client: AsyncClient,
    empty_write_db: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flat trend + dispute_rate 25% (> 20%) -> contracting.

    observatory-service-specs.md Economy Phases table: `contracting` = "Task
    creation rate declining over 7-day window OR dispute rate > 20%". The
    dispute-rate arm of that OR must fire even when the trend is flat.

    Note: a flat trend at 15-20% disputes matches no row of the spec table
    (it is neither `stable`, which needs <15%, nor `contracting`, which needs
    >20%). That gap is deliberately left to the residual `stable` branch in
    compute_economy_phase rather than guessed at here.
    """
    monkeypatch.setattr(database_service, "_clock", lambda: FROZEN_NOW)
    # 20 total tasks (4 + 4 trend tasks + 12 filler), 5 disputed -> exactly 25%.
    await _seed_stable_trend_with_dispute_rate(
        empty_write_db, disputed_filler_count=5, open_filler_count=7
    )

    response = await empty_client.get("/api/metrics")
    assert response.status_code == 200
    phase = response.json()["economy_phase"]
    assert phase["task_creation_trend"] == "stable"
    assert phase["dispute_rate"] == 0.25
    assert phase["phase"] == "contracting"
