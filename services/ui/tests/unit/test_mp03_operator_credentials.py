"""MP-03 startup regressions for required operator credentials."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from service_auth import KeyMaterialError, generate_keypair

from ui_service.config import UserAgentConfig, clear_settings_cache
from ui_service.core import lifespan as lifespan_module
from ui_service.core.lifespan import lifespan


@pytest.mark.unit
@pytest.mark.parametrize(
    ("agent_config_path", "handle"),
    [("", "operator"), ("   ", "operator"), ("agents.yaml", ""), ("agents.yaml", "   ")],
)
def test_operator_signing_configuration_is_required(
    agent_config_path: str,
    handle: str,
) -> None:
    """UI requires explicit non-blank operator credential configuration."""
    with pytest.raises(ValidationError):
        UserAgentConfig(agent_config_path=agent_config_path, handle=handle)


@pytest.mark.unit
@pytest.mark.parametrize(
    "key_state",
    ["missing", "partial", "mismatched", "corrupt", "unsafe_mode"],
)
async def test_invalid_operator_keys_abort_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key_state: str,
) -> None:
    """Credential load failures are fatal and never become proxy-unavailable startup."""
    agent_config_path = _write_agent_config(tmp_path, key_state)
    service_config = (
        Path("config.yaml")
        .resolve()
        .read_text()
        .replace(
            "../../agents/config.yaml",
            str(agent_config_path),
        )
    )
    service_config = service_config.replace("data/logs", str(tmp_path / "logs"))
    service_config = service_config.replace(
        "../db-gateway/data/economy.db",
        str(tmp_path / "economy.db"),
    )
    service_config = service_config.replace(
        "../../data/maintenance.lock",
        str(tmp_path / "maintenance.lock"),
    )
    service_config_path = tmp_path / "ui-config.yaml"
    service_config_path.write_text(service_config)
    monkeypatch.setenv("CONFIG_PATH", str(service_config_path))
    connect = MagicMock(side_effect=AssertionError("database opened before credential preflight"))
    monkeypatch.setattr(lifespan_module.aiosqlite, "connect", connect)
    clear_settings_cache()

    with pytest.raises(KeyMaterialError):
        async with lifespan(FastAPI()):
            pytest.fail("startup unexpectedly succeeded")

    connect.assert_not_called()


def _write_agent_config(tmp_path: Path, key_state: str) -> Path:
    keys_dir = tmp_path / "keys"
    _write_key_state("operator", keys_dir, key_state)
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text('agents:\n  operator:\n    name: "Operator"\n')
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        f"""\
platform:
  identity_url: "http://localhost:8001"
  bank_url: "http://localhost:8002"
  task_board_url: "http://localhost:8003"
  reputation_url: "http://localhost:8004"
  court_url: "http://localhost:8005"
data:
  keys_dir: "{keys_dir}"
  roster_path: "{roster_path}"
signing:
  token_ttl_seconds: 60
  max_lifetime_seconds: 300
  clock_skew_seconds: 30
""",
    )
    return config_path


def _write_key_state(handle: str, keys_dir: Path, key_state: str) -> None:
    if key_state == "missing":
        return
    generate_keypair(handle, keys_dir)
    if key_state == "partial":
        (keys_dir / f"{handle}.pub").unlink()
    elif key_state == "mismatched":
        generate_keypair("other", keys_dir)
        (keys_dir / f"{handle}.pub").write_bytes((keys_dir / "other.pub").read_bytes())
    elif key_state == "corrupt":
        (keys_dir / f"{handle}.key").write_text("not-a-private-key")
    elif key_state == "unsafe_mode":
        (keys_dir / f"{handle}.key").chmod(0o644)
