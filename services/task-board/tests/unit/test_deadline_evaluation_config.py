"""Config tests for the Q-5 (GAP-A3) deadline_evaluation section.

deadline_evaluation is Optional at the Settings/Pydantic level (mirroring the
existing db_gateway pattern in config.py) so that test_config.py's frozen
fixtures — which predate this section and don't include it — keep loading.
The "no code default, fail fast when missing" requirement is enforced instead
in lifespan.py, exactly like the existing db_gateway required-check; this file
proves both halves: the schema loads/validates the section when present, and
lifespan() refuses to start without it.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import yaml
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair as provision_keypair

from task_board_service.app import create_app
from task_board_service.config import Settings, clear_settings_cache, get_settings
from task_board_service.core.lifespan import lifespan
from task_board_service.core.state import reset_app_state

if TYPE_CHECKING:
    from pathlib import Path

_BASE_CONFIG = """\
service:
  name: "task-board"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8003
  log_level: "info"
logging:
  level: "WARNING"
  directory: "{logs_path}"
database:
  path: "{db_path}"
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
  agent_id: "a-platform-test-id"
  agent_config_path: "{agent_config_path}"
request:
  max_body_size: 1048576
recovery:
  maintenance_lock_path: "maintenance.lock"
posting_recovery:
  interval_seconds: 2
  batch_size: 100
  diagnostic_limit: 100
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
  signing:
    kid: "gateway-task-board"
    private_key_path: "gateway-task-board.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
assets:
  storage_path: "{assets_path}"
  max_file_size: 10485760
  max_files_per_task: 10
{deadline_evaluation_section}
"""


def _write_platform_agent_config(tmp_path: Path) -> Path:
    keys_dir = tmp_path / "agent-keys"
    provision_keypair("platform", keys_dir)
    provision_keypair("gateway-task-board", tmp_path)
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump({"agents": {"platform": {"name": "Platform", "type": "platform"}}})
    )
    agent_config_path = tmp_path / "agents.yaml"
    agent_config_path.write_text(
        yaml.safe_dump(
            {
                "platform": {
                    "identity_url": "http://localhost:8001",
                    "bank_url": "http://localhost:8002",
                    "task_board_url": "http://localhost:8003",
                    "reputation_url": "http://localhost:8004",
                    "court_url": "http://localhost:8005",
                },
                "data": {
                    "keys_dir": str(keys_dir),
                    "roster_path": str(roster_path),
                },
                "signing": {
                    "token_ttl_seconds": 300,
                    "max_lifetime_seconds": 300,
                    "clock_skew_seconds": 0,
                },
            }
        )
    )
    return agent_config_path


async def _register_test_platform(agent: PlatformAgent) -> dict[str, str]:
    agent.agent_id = "a-platform-test-id"
    return {"agent_id": agent.agent_id}


@pytest.mark.unit
def test_deadline_evaluation_section_loads(tmp_path) -> None:
    """A config with deadline_evaluation.evaluation_interval_seconds loads it."""
    config_content = _BASE_CONFIG.format(
        db_path=str(tmp_path / "task-board.db"),
        assets_path=str(tmp_path / "assets"),
        agent_config_path="agents.yaml",
        logs_path=str(tmp_path / "logs" / "task-board"),
        deadline_evaluation_section=(
            "deadline_evaluation:\n"
            "  evaluation_interval_seconds: 30\n"
            "  actor_id: system-task-board-test-deadline\n"
        ),
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()

    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.deadline_evaluation is not None
    assert settings.deadline_evaluation.evaluation_interval_seconds == 30

    os.environ.pop("CONFIG_PATH", None)
    clear_settings_cache()


@pytest.mark.unit
def test_deadline_evaluation_missing_interval_field_rejected(tmp_path) -> None:
    """A deadline_evaluation section without evaluation_interval_seconds fails validation."""
    config_content = _BASE_CONFIG.format(
        db_path=str(tmp_path / "task-board.db"),
        assets_path=str(tmp_path / "assets"),
        agent_config_path="agents.yaml",
        logs_path=str(tmp_path / "logs" / "task-board"),
        deadline_evaluation_section=(
            "deadline_evaluation:\n  actor_id: system-task-board-test-deadline\n"
        ),
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()

    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        get_settings()

    os.environ.pop("CONFIG_PATH", None)
    clear_settings_cache()


@pytest.mark.unit
async def test_lifespan_fails_fast_without_deadline_evaluation_section(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config missing deadline_evaluation entirely refuses to start the service.

    No code default exists (Q-5): the section is Optional at the schema level
    (test_config.py's fixtures predate it) but required to actually start,
    exactly mirroring the existing db_gateway required-check in lifespan.py.
    """
    agent_config_path = _write_platform_agent_config(tmp_path)
    config_content = _BASE_CONFIG.format(
        db_path=str(tmp_path / "task-board.db"),
        assets_path=str(tmp_path / "assets"),
        agent_config_path=str(agent_config_path),
        logs_path=str(tmp_path / "logs" / "task-board"),
        deadline_evaluation_section="",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    reset_app_state()
    monkeypatch.setattr(PlatformAgent, "register", _register_test_platform)

    test_app = create_app()
    with pytest.raises(RuntimeError, match="deadline_evaluation"):
        async with lifespan(test_app):
            pass  # pragma: no cover — startup must raise before this runs

    reset_app_state()
    os.environ.pop("CONFIG_PATH", None)
    clear_settings_cache()
