"""Unit tests for configuration loading."""

import pytest

from ui_service.config import Settings, clear_settings_cache, get_settings


@pytest.mark.unit
def test_config_loads_from_yaml(tmp_path, monkeypatch):
    """Config loads correctly from a valid YAML file."""
    config_content = f"""
service:
  name: "ui"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
logging:
  level: "INFO"
  directory: "data/logs"
database:
  path: "data/economy.db"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "data/web"
request:
  max_body_size: 1572864
recovery:
  maintenance_lock_path: "maintenance.lock"
browser_security:
  allowed_hosts: ["127.0.0.1"]
  allowed_origins: ["http://127.0.0.1:8008"]
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

    clear_settings_cache()
    try:
        settings = get_settings()

        assert isinstance(settings, Settings)
        assert settings.service.name == "ui"
        assert settings.server.port == 8008
        assert settings.database.path == "data/economy.db"
        assert settings.sse.poll_interval_seconds == 1
        assert settings.frontend.web_root == "data/web"
        assert settings.request.max_body_size == 1572864
        assert settings.browser_security.allowed_hosts == ["127.0.0.1"]
        assert settings.user_agent.agent_config_path == str(
            tmp_path / "agent-config.yaml",
        )
        assert settings.user_agent.handle == "ui-operator-test"
    finally:
        clear_settings_cache()


@pytest.mark.unit
def test_config_rejects_extra_fields(tmp_path, monkeypatch):
    """Config with extra fields causes validation error."""
    config_content = f"""
service:
  name: "ui"
  version: "0.1.0"
  extra_field: "should fail"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
logging:
  level: "INFO"
  directory: "data/logs"
database:
  path: "data/economy.db"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "data/web"
request:
  max_body_size: 1572864
recovery:
  maintenance_lock_path: "maintenance.lock"
browser_security:
  allowed_hosts: ["127.0.0.1"]
  allowed_origins: ["http://127.0.0.1:8008"]
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

    clear_settings_cache()
    try:
        with pytest.raises(Exception) as exc_info:
            get_settings()

        error_text = str(exc_info.value)
        assert "extra_field" in error_text
        assert "Extra inputs are not permitted" in error_text
        assert "user_agent\n  Field required" not in error_text
    finally:
        clear_settings_cache()
