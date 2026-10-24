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
  host: "127.0.0.1"
  port: 8003
  log_level: "info"
logging:
  level: "INFO"
  directory: "data/logs"
database:
  path: "data/task-board.db"
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
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
  agent_config_path: "agents.yaml"
asset_upload:
  stream_chunk_bytes: 262144
  max_multipart_overhead_bytes: 1048576
  max_header_count: 8
  max_header_size: 4224
  max_boundary_bytes: 70
  max_display_filename_utf8_bytes: 255
request:
  max_body_size: 1048576
recovery:
  maintenance_lock_path: "maintenance.lock"
posting_recovery:
  interval_seconds: 2
  batch_size: 100
  diagnostic_limit: 100
financial_effect_recovery:
  interval_seconds: 2.0
  max_delivery_attempts_per_cycle: 10
limits:
  max_file_size: 10485760
  max_assets_per_task: 20
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
  signing:
    kid: "gateway-task-board"
    private_key_path: "gateway-task-board.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
deadline_evaluation:
  actor_id: "system-config-test-deadline"
  evaluation_interval_seconds: 10
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
    assert settings.central_bank.base_url == "http://localhost:8002"
    assert settings.platform.agent_id == "a-platform-id"

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
  host: "127.0.0.1"
  port: 8003
  log_level: "info"
logging:
  level: "INFO"
  directory: "data/logs"
database:
  path: "data/task-board.db"
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
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
  agent_config_path: "agents.yaml"
asset_upload:
  stream_chunk_bytes: 262144
  max_multipart_overhead_bytes: 1048576
  max_header_count: 8
  max_header_size: 4224
  max_boundary_bytes: 70
  max_display_filename_utf8_bytes: 255
request:
  max_body_size: 1048576
recovery:
  maintenance_lock_path: "maintenance.lock"
posting_recovery:
  interval_seconds: 2
  batch_size: 100
  diagnostic_limit: 100
financial_effect_recovery:
  interval_seconds: 2.0
  max_delivery_attempts_per_cycle: 10
limits:
  max_file_size: 10485760
  max_assets_per_task: 20
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
  signing:
    kid: "gateway-task-board"
    private_key_path: "gateway-task-board.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
deadline_evaluation:
  actor_id: "system-config-test-deadline"
  evaluation_interval_seconds: 10
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
  host: "127.0.0.1"
  port: 8003
  log_level: "info"
logging:
  level: "INFO"
  directory: "data/logs"
database:
  path: "data/task-board.db"
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
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
  agent_config_path: "agents.yaml"
asset_upload:
  stream_chunk_bytes: 262144
  max_multipart_overhead_bytes: 1048576
  max_header_count: 8
  max_header_size: 4224
  max_boundary_bytes: 70
  max_display_filename_utf8_bytes: 255
request:
  max_body_size: 1048576
recovery:
  maintenance_lock_path: "maintenance.lock"
posting_recovery:
  interval_seconds: 2
  batch_size: 100
  diagnostic_limit: 100
financial_effect_recovery:
  interval_seconds: 2.0
  max_delivery_attempts_per_cycle: 10
limits:
  max_file_size: 10485760
  max_assets_per_task: 20
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
  signing:
    kid: "gateway-task-board"
    private_key_path: "gateway-task-board.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
deadline_evaluation:
  actor_id: "system-config-test-deadline"
  evaluation_interval_seconds: 10
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert settings.platform.agent_id == "a-platform-id"
    assert settings.central_bank.base_url == "http://localhost:8002"

    os.environ.pop("CONFIG_PATH", None)
