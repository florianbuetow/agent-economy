"""Integration tests for /health endpoint."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ui_service.app import create_app
from ui_service.config import clear_settings_cache
from ui_service.core.lifespan import lifespan
from ui_service.core.state import reset_app_state

pytestmark = pytest.mark.integration


async def _create_client_with_db_path(db_path: str, web_root: str, cfg_path: str):
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
  directory: "{web_root}/logs"
database:
  path: "{db_path}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "{web_root}"
request:
  max_body_size: 1572864
"""
    Path(cfg_path).write_text(config_content)

    os.environ["CONFIG_PATH"] = cfg_path
    clear_settings_cache()
    reset_app_state()

    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as local_client:
            yield local_client

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


async def test_health_returns_200_with_status_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_health_has_uptime_seconds(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["uptime_seconds"] >= 0


async def test_health_has_started_at_iso(client):
    response = await client.get("/health")
    started_at = response.json()["started_at"]
    assert isinstance(started_at, str)
    assert started_at
    assert "T" in started_at
    assert started_at.endswith("Z")


async def test_health_latest_event_id_matches_seed(client):
    response = await client.get("/health")
    assert response.json()["latest_event_id"] == 25


async def test_health_database_readable_true(client):
    response = await client.get("/health")
    assert response.json()["database_readable"] is True


async def test_health_post_not_allowed(client):
    response = await client.post("/health")
    assert response.status_code == 405


async def test_health_response_has_exact_keys(client):
    response = await client.get("/health")
    assert set(response.json().keys()) == {
        "status",
        "uptime_seconds",
        "started_at",
        "latest_event_id",
        "database_readable",
    }


async def test_health_uptime_increases_between_calls(client):
    first = await client.get("/health")
    await asyncio.sleep(0.02)
    second = await client.get("/health")
    assert second.json()["uptime_seconds"] >= first.json()["uptime_seconds"]


async def test_health_types_correct(client):
    data = (await client.get("/health")).json()
    assert isinstance(data["uptime_seconds"], int | float)
    assert isinstance(data["started_at"], str)
    assert isinstance(data["latest_event_id"], int)
    assert isinstance(data["database_readable"], bool)


async def test_health_no_db_returns_readable_false(tmp_path):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html><body>Test</body></html>")

    missing_db = tmp_path / "does-not-exist.db"
    config_path = tmp_path / "config.yaml"

    async for local_client in _create_client_with_db_path(
        db_path=str(missing_db),
        web_root=str(web_dir),
        cfg_path=str(config_path),
    ):
        response = await local_client.get("/health")
        assert response.status_code == 200
        assert response.json()["database_readable"] is False
        assert response.json()["latest_event_id"] == 0


async def test_health_latest_event_id_updates_on_insert(client, write_db):
    await write_db.execute(
        "INSERT INTO events "
        "(event_id, event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            26,
            "board",
            "task.created",
            "2026-03-02T06:30:00Z",
            "t-task6",
            "a-alice",
            "Inserted from staleness test",
            json.dumps({"title": "Inserted"}),
        ),
    )
    await write_db.commit()

    response = await client.get("/health")
    assert response.json()["latest_event_id"] == 26


async def test_health_latest_event_id_zero_when_no_events(client, write_db):
    await write_db.execute("DELETE FROM events")
    await write_db.commit()

    response = await client.get("/health")
    assert response.json()["latest_event_id"] == 0
