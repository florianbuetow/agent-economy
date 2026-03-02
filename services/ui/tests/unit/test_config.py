"""Unit tests for configuration loading."""

import os

import pytest

from ui_service.config import Settings, clear_settings_cache, get_settings


@pytest.mark.unit
def test_config_loads_from_yaml(tmp_path):
    """Config loads correctly from a valid YAML file."""
    config_content = """
service:
  name: "ui"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
logging:
  level: "INFO"
  directory: "data/logs"
frontend:
  web_root: "data/web"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.service.name == "ui"
    assert settings.server.port == 8008
    assert settings.frontend.web_root == "data/web"
    assert settings.request.max_body_size == 1572864

    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_rejects_extra_fields(tmp_path):
    """Config with extra fields causes validation error."""
    config_content = """
service:
  name: "ui"
  version: "0.1.0"
  extra_field: "should fail"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
logging:
  level: "INFO"
  directory: "data/logs"
frontend:
  web_root: "data/web"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    with pytest.raises(Exception):  # noqa: B017
        get_settings()

    os.environ.pop("CONFIG_PATH", None)
