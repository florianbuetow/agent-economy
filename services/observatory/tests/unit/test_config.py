"""Tests for configuration loading."""

import os

import pytest
from service_commons.config import ConfigurationError

from observatory_service.config import clear_settings_cache, get_settings


@pytest.mark.unit
def test_config_loads_from_yaml(tmp_path):
    """Settings loads all sections from a valid config file."""
    config_content = """
service:
  name: "observatory"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8006
  log_level: "info"
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
  dist_path: "frontend/dist"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert settings.service.name == "observatory"
    assert settings.server.port == 8006
    assert settings.sse.poll_interval_seconds == 1
    assert settings.frontend.dist_path == "frontend/dist"

    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_rejects_extra_fields(tmp_path):
    """Settings rejects unknown configuration keys."""
    config_content = """
service:
  name: "observatory"
  version: "0.1.0"
  unknown_field: "should fail"
server:
  host: "0.0.0.0"
  port: 8006
  log_level: "info"
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
  dist_path: "frontend/dist"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()

    with pytest.raises(ConfigurationError):
        get_settings()

    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)
