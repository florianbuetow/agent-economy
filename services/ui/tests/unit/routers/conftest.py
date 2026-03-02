"""Router test fixtures — app with lifespan and async client."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from ui_service.app import create_app
from ui_service.config import clear_settings_cache
from ui_service.core.lifespan import lifespan
from ui_service.core.state import reset_app_state


@pytest.fixture
async def app(tmp_path):
    """Create a test app with a temporary web root."""
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    index_html = web_dir / "index.html"
    index_html.write_text("<html><body>Test Landing Page</body></html>")

    config_content = f"""
service:
  name: "ui"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
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
async def client(app):
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
