"""Router test fixtures — app with lifespan and async client."""

import pytest
from httpx import ASGITransport, AsyncClient

from ui_service.app import create_app
from ui_service.config import clear_settings_cache
from ui_service.core import lifespan as lifespan_module
from ui_service.core.lifespan import lifespan
from ui_service.core.state import reset_app_state


class _StubUserAgent:
    """Network-free user agent for router tests."""

    agent_id = "a-test-ui"
    name = "ui-user-agent"

    async def register(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _StubAgentFactory:
    """Agent factory that cannot register a real operator."""

    def __init__(self, config_path) -> None:
        self._config_path = config_path

    def user_agent(self, handle: str) -> _StubUserAgent:
        assert handle == "ui-operator-test"
        return _StubUserAgent()


@pytest.fixture
async def app(tmp_path, monkeypatch):
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
  directory: "{tmp_path / "logs"}"
database:
  path: "{tmp_path / "economy.db"}"
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
  handle: "ui-operator-test"
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setattr(lifespan_module, "AgentFactory", _StubAgentFactory)

    clear_settings_cache()
    reset_app_state()
    try:
        test_app = create_app()
        async with lifespan(test_app):
            yield test_app
    finally:
        reset_app_state()
        clear_settings_cache()


@pytest.fixture
async def client(app):
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://localhost",
        headers={"Origin": "http://localhost", "X-ATE-CSRF": "local-operator"},
    ) as c:
        yield c
