"""Integration test fixtures with seeded SQLite database."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from ui_service.app import create_app
from ui_service.config import clear_settings_cache
from ui_service.core import lifespan as lifespan_module
from ui_service.core.lifespan import lifespan
from ui_service.core.state import get_app_state, reset_app_state
from ui_service.services import quarterly as quarterly_service

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"
INTEGRATION_TESTS_DIR = Path(__file__).resolve().parent

if str(INTEGRATION_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_TESTS_DIR))


class _StubUserAgent:
    """Network-free default operator for integration app startup."""

    agent_id = "a-test-ui"
    name = "ui-user-agent"

    async def register(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _StubAgentFactory:
    """Agent factory that cannot register against a live Identity service."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    def user_agent(self, handle: str) -> _StubUserAgent:
        assert handle == "ui-operator-integration-test"
        return _StubUserAgent()


def _fixture_financial_effect(status: str) -> dict[str, object]:
    settlement_state = "attention_required" if status in {"approved", "ruled"} else "locked"
    return {
        "schema_version": 1,
        "settlement_state": settlement_state,
        "receipt_state": "absent",
        "outcome": None,
        "operation_id": None,
        "receipt_reference": None,
        "business_projection_pending": False,
        "pending_target_status": None,
    }


class _FixtureTaskBoardReader:
    """Network-free Task Board projection built from explicit test fixture state."""

    def __init__(self, database: Path) -> None:
        sidecar = database.with_suffix(".board-projections.json")
        self._sidecar = json.loads(sidecar.read_text()) if sidecar.exists() else None
        self._database = database

    def _records(self) -> list[dict[str, object]]:
        if self._sidecar is not None:
            return [dict(item) for item in self._sidecar]
        connection = sqlite3.connect(str(self._database))
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT bt.*, btc.lifecycle_version, btc.accepted_bid_amount, "
                "(SELECT COUNT(*) FROM board_bids bb WHERE bb.task_id = bt.task_id) "
                "AS _authoritative_bid_count "
                "FROM board_tasks bt LEFT JOIN board_task_contracts btc "
                "ON btc.task_id=bt.task_id ORDER BY bt.created_at DESC"
            ).fetchall()
        finally:
            connection.close()
        records: list[dict[str, object]] = []
        for row in rows:
            value = dict(row)
            value["bid_count"] = value.pop("_authoritative_bid_count")
            value["financial_effect"] = _fixture_financial_effect(str(value["status"]))
            value["acceptance_deadline"] = value.get("bidding_deadline")
            records.append(value)
        return records

    async def list_tasks(
        self,
        status: str | None = None,
        poster_id: str | None = None,
        worker_id: str | None = None,
    ) -> list[dict[str, object]]:
        values = self._records()
        return [
            value
            for value in values
            if (status is None or value["status"] == status)
            and (poster_id is None or value["poster_id"] == poster_id)
            and (worker_id is None or value.get("worker_id") == worker_id)
        ]

    async def get_task(self, task_id: str) -> dict[str, object]:
        for value in self._records():
            if value["task_id"] == task_id:
                return value
        request = httpx.Request("GET", f"http://task-board/tasks/{task_id}")
        response = httpx.Response(
            404,
            request=request,
            json={"error": "task_not_found", "message": "Task not found", "details": {}},
        )
        raise httpx.HTTPStatusError("task not found", request=request, response=response)


class _FixtureQualityReader:
    """Network-free stand-in for Reputation ``GET /agents/{id}/quality``.

    Projects the fixture database's visible feedback into the service report
    shape (one observation per row); the real formula is tested in Reputation.
    """

    _POINTS = {"dissatisfied": 0, "satisfied": 50, "extremely_satisfied": 100}

    def __init__(self, database: Path) -> None:
        self._database = database

    async def get_agent_quality(self, agent_id: str) -> dict[str, object]:
        connection = sqlite3.connect(str(self._database))
        try:
            rows = connection.execute(
                "SELECT category, rating FROM reputation_feedback "
                "WHERE to_agent_id = ? AND visible = 1",
                (agent_id,),
            ).fetchall()
        finally:
            connection.close()
        report: dict[str, object] = {"agent_id": agent_id, "formula_version": "quality-v1"}
        for category, name in (
            ("spec_quality", "specification_quality"),
            ("delivery_quality", "delivery_quality"),
        ):
            ratings = [r for c, r in rows if c == category and r in self._POINTS]
            points = [self._POINTS[r] for r in ratings]
            report[name] = {
                "rated": bool(points),
                "score": (2 * sum(points) + len(points)) // (2 * len(points)) if points else None,
                "task_count": len(points),
                "rating_counts": {rating: ratings.count(rating) for rating in self._POINTS},
            }
        return report


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create and seed a SQLite database, returning its path."""
    from helpers import insert_seed_data  # noqa: PLC0415

    db_file = tmp_path / "economy.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("PRAGMA journal_mode=WAL")
    insert_seed_data(conn)
    conn.close()
    return db_file


@pytest.fixture
async def app(db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create test app pointing at the seeded database."""
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
  path: "{db_path}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "{web_dir}"
request:
  max_body_size: 1572864
recovery:
  maintenance_lock_path: "{tmp_path / "maintenance.lock"}"
browser_security:
  allowed_hosts: ["localhost"]
  allowed_origins: ["http://localhost"]
  csrf_header_name: "X-ATE-CSRF"
  csrf_header_value: "local-operator"
  content_security_policy: "default-src 'self'; script-src 'self'; object-src 'none'"
user_agent:
  agent_config_path: "{tmp_path / "agent-config.yaml"}"
  handle: "ui-operator-integration-test"
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setattr(lifespan_module, "AgentFactory", _StubAgentFactory)

    clear_settings_cache()
    reset_app_state()
    monkeypatch.setattr(quarterly_service, "utc_now", lambda: datetime(2026, 3, 2, tzinfo=UTC))
    try:
        test_app = create_app()
        async with lifespan(test_app):
            get_app_state().task_board_reader = _FixtureTaskBoardReader(db_path)
            get_app_state().quality_reader = _FixtureQualityReader(db_path)
            yield test_app
    finally:
        reset_app_state()
        clear_settings_cache()


@pytest.fixture
async def client(app):
    """Async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://localhost",
        headers={"Origin": "http://localhost", "X-ATE-CSRF": "local-operator"},
    ) as c:
        yield c


@pytest.fixture
async def write_db(db_path: Path):
    """Writable connection to the same DB for staleness tests."""
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    await conn.close()
