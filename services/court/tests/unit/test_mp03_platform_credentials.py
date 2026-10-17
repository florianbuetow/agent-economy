"""MP-03 startup regressions for required platform credentials."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from service_auth import KeyMaterialError
from service_auth.signing import generate_keypair

from court_service.config import PlatformConfig, clear_settings_cache
from court_service.core.lifespan import lifespan


@pytest.mark.unit
@pytest.mark.parametrize("agent_config_path", ["", "   "])
def test_platform_agent_config_path_is_required(agent_config_path: str) -> None:
    """Court cannot start without configured platform credentials."""
    with pytest.raises(ValidationError, match="agent_config_path"):
        PlatformConfig(agent_config_path=agent_config_path)


@pytest.mark.unit
def test_platform_agent_config_path_has_no_default() -> None:
    """Omitting the platform credential path is a configuration error."""
    with pytest.raises(ValidationError, match="agent_config_path"):
        PlatformConfig()


@pytest.mark.unit
@pytest.mark.parametrize(
    "key_state",
    ["missing", "partial", "mismatched", "corrupt", "unsafe_mode"],
)
async def test_invalid_platform_keys_fail_before_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key_state: str,
) -> None:
    """Invalid platform key material fails load-only startup."""
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
    service_config_path = tmp_path / "court-config.yaml"
    service_config_path.write_text(service_config)
    monkeypatch.setenv("CONFIG_PATH", str(service_config_path))
    clear_settings_cache()

    with pytest.raises(KeyMaterialError):
        async with lifespan(FastAPI()):
            pytest.fail("startup unexpectedly succeeded")


def _write_agent_config(tmp_path: Path, key_state: str) -> Path:
    keys_dir = tmp_path / "keys"
    _write_key_state("platform", keys_dir, key_state)
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text('agents:\n  platform:\n    name: "Platform"\n')
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
  max_lifetime_seconds: 60
  clock_skew_seconds: 0
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
