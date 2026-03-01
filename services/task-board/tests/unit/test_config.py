"""Configuration loading tests for the Task Board service."""

from __future__ import annotations

import os

import pytest

from task_board_service.config import Settings, clear_settings_cache, get_settings


@pytest.mark.unit
def test_config_loads_from_yaml(tmp_path):
    """Valid config loads without error."""
    config_content = """\
service:
  name: "task-board"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8003
  log_level: "info"
logging:
  level: "INFO"
  directory: "data/logs"
database:
  path: "data/task-board.db"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/release"
  escrow_split_path: "/escrow/split"
  timeout_seconds: 10
platform:
  agent_id: "a-platform-id"
request:
  max_body_size: 1048576
deadlines:
  default_bidding_seconds: 3600
  default_execution_seconds: 86400
  default_review_seconds: 86400
limits:
  max_title_length: 200
  max_spec_length: 10000
  max_reason_length: 2000
  max_file_size: 10485760
  max_assets_per_task: 20
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.service.name == "task-board"
    assert settings.server.port == 8003
    assert settings.database.path == "data/task-board.db"
    assert settings.identity.base_url == "http://localhost:8001"
    assert settings.central_bank.base_url == "http://localhost:8002"
    assert settings.platform.agent_id == "a-platform-id"
    assert settings.deadlines.default_bidding_seconds == 3600
    assert settings.limits.max_title_length == 200

    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_rejects_extra_fields(tmp_path):
    """Extra keys raise ValidationError (extra='forbid')."""
    config_content = """\
service:
  name: "task-board"
  version: "0.1.0"
  unknown_field: true
server:
  host: "0.0.0.0"
  port: 8003
  log_level: "info"
logging:
  level: "INFO"
  directory: "data/logs"
database:
  path: "data/task-board.db"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/release"
  escrow_split_path: "/escrow/split"
  timeout_seconds: 10
platform:
  agent_id: "a-platform-id"
request:
  max_body_size: 1048576
deadlines:
  default_bidding_seconds: 3600
  default_execution_seconds: 86400
  default_review_seconds: 86400
limits:
  max_title_length: 200
  max_spec_length: 10000
  max_reason_length: 2000
  max_file_size: 10485760
  max_assets_per_task: 20
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()

    with pytest.raises(Exception):  # noqa: B017
        get_settings()

    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_missing_required_section(tmp_path):
    """Missing required sections raise ValidationError."""
    config_content = """\
service:
  name: "task-board"
  version: "0.1.0"
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()

    with pytest.raises(Exception):  # noqa: B017
        get_settings()

    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_platform_agent_id_present(tmp_path):
    """Platform agent_id must be present in config."""
    config_content = """\
service:
  name: "task-board"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8003
  log_level: "info"
logging:
  level: "INFO"
  directory: "data/logs"
database:
  path: "data/task-board.db"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/release"
  escrow_split_path: "/escrow/split"
  timeout_seconds: 10
platform:
  agent_id: "a-platform-id"
request:
  max_body_size: 1048576
deadlines:
  default_bidding_seconds: 3600
  default_execution_seconds: 86400
  default_review_seconds: 86400
limits:
  max_title_length: 200
  max_spec_length: 10000
  max_reason_length: 2000
  max_file_size: 10485760
  max_assets_per_task: 20
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert settings.platform.agent_id == "a-platform-id"
    assert settings.identity.base_url == "http://localhost:8001"
    assert settings.central_bank.base_url == "http://localhost:8002"

    os.environ.pop("CONFIG_PATH", None)
