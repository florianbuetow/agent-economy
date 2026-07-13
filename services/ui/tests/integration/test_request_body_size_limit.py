"""Integration test for WP-08 item 2: enforce ``request.max_body_size``.

Before this fix, ``RequestConfig.max_body_size`` was validated by Pydantic but
never wired into any middleware — an oversized POST body to a proxy JSON
endpoint was accepted (or rejected downstream for unrelated reasons) instead of
being rejected at the edge with 413, as every other service in this repo does.

This test builds its own app instance (rather than reusing the shared
``app``/``client`` fixtures in ``conftest.py``) so it can set a small
``max_body_size`` and prove the boundary precisely and quickly, without
constructing multi-megabyte payloads.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from ui_service.app import create_app
from ui_service.config import clear_settings_cache
from ui_service.core.lifespan import lifespan
from ui_service.core.state import reset_app_state
from ui_service.services import quarterly as quarterly_service

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

SMALL_MAX_BODY_SIZE = 64


@pytest.fixture
async def small_body_limit_client(db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A test client whose app enforces a tiny max_body_size (64 bytes)."""
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
  max_body_size: {SMALL_MAX_BODY_SIZE}
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()
    monkeypatch.setattr(quarterly_service, "utc_now", lambda: datetime(2026, 3, 2, tzinfo=UTC))

    test_app = create_app()
    async with lifespan(test_app):
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


async def test_oversized_proxy_task_body_rejected_with_413(small_body_limit_client):
    """A POST body exceeding max_body_size on the JSON proxy endpoint is rejected with 413."""
    oversized_spec = "x" * (SMALL_MAX_BODY_SIZE * 4)
    response = await small_body_limit_client.post(
        "/api/proxy/tasks",
        json={
            "title": "Oversized task",
            "spec": oversized_spec,
            "reward": 10,
            "bidding_deadline_seconds": 60,
            "execution_deadline_seconds": 60,
            "review_deadline_seconds": 60,
        },
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


async def test_oversized_proxy_dispute_body_rejected_with_413(small_body_limit_client):
    """A POST body exceeding max_body_size on the dispute proxy endpoint is rejected with 413."""
    oversized_reason = "x" * (SMALL_MAX_BODY_SIZE * 4)
    response = await small_body_limit_client.post(
        "/api/proxy/tasks/t-any/dispute",
        json={"reason": oversized_reason},
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


async def test_body_within_limit_is_not_rejected_for_size(small_body_limit_client):
    """A small body must not be rejected by the size guard (may still 503 downstream)."""
    response = await small_body_limit_client.post(
        "/api/proxy/tasks/t-any/dispute",
        json={"reason": "ok"},
    )
    assert response.status_code != 413
