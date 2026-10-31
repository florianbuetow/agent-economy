"""MP-00 regression guards for explicit UI test configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from service_commons.config import ConfigurationError

from ui_service.config import clear_settings_cache, get_settings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
def test_extra_field_rejection_reports_the_unsupported_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An otherwise valid config fails specifically because its field is extra."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""\
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
  directory: "{tmp_path / "logs"}"
database:
  path: "{tmp_path / "economy.db"}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "{tmp_path / "web"}"
request:
  max_body_size: 1572864
recovery:
  maintenance_lock_path: "{tmp_path / "maintenance.lock"}"
browser_security:
  allowed_hosts: ["127.0.0.1"]
  allowed_origins: ["http://127.0.0.1:8008"]
  csrf_header_name: "X-ATE-CSRF"
  csrf_header_value: "local-operator"
  content_security_policy: "default-src 'self'; script-src 'self'; object-src 'none'"
user_agent:
  agent_config_path: "{tmp_path / "agent-config.yaml"}"
  handle: "ui-operator-test"
""",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    clear_settings_cache()
    try:
        with pytest.raises(ConfigurationError) as exc_info:
            get_settings()
    finally:
        clear_settings_cache()

    error_text = str(exc_info.value)
    assert "extra_field" in error_text
    assert "Extra inputs are not permitted" in error_text
    assert "user_agent\n  Field required" not in error_text
