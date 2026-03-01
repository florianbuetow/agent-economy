"""Configuration loading tests."""

from __future__ import annotations

import os

import pytest

from central_bank_service.config import Settings, clear_settings_cache, get_settings


@pytest.mark.unit
def test_config_loads_from_yaml(tmp_path):
    """Config loads correctly from a valid YAML file."""
    config_content = """
service:
  name: "central-bank"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8002
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/central-bank.db"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"
platform:
  agent_id: "a-platform"
request:
  max_body_size: 1048576
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.service.name == "central-bank"
    assert settings.server.port == 8002
    assert settings.database.path == "data/central-bank.db"
    assert settings.identity.base_url == "http://localhost:8001"
    assert settings.platform.agent_id == "a-platform"
    assert settings.request.max_body_size == 1048576

    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_rejects_extra_fields(tmp_path):
    """Config with extra fields causes validation error."""
    config_content = """
service:
  name: "central-bank"
  version: "0.1.0"
  unknown_field: true
server:
  host: "0.0.0.0"
  port: 8002
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/central-bank.db"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"
platform:
  agent_id: "a-platform"
request:
  max_body_size: 1048576
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()

    with pytest.raises(Exception):  # noqa: B017
        get_settings()

    os.environ.pop("CONFIG_PATH", None)
