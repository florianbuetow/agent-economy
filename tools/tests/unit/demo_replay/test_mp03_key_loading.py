"""Demo replay loads only an explicit, validated durable platform key pair."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from pydantic import ValidationError
from service_auth.signing import KeyMaterialError, generate_keypair, public_key_to_b64

from demo_replay import clients
from demo_replay.config import DemoReplayConfig, PlatformConfig, load_platform_settings
from demo_replay.engine import ReplayEngine
from demo_replay.wallet import DemoAgent


@pytest.mark.unit
@pytest.mark.parametrize("damage", ["missing_public", "mismatched_public", "unsafe_mode"])
def test_demo_platform_rejects_damaged_pair_without_replacement(
    tmp_path: Path, damage: str
) -> None:
    keys = tmp_path / "keys"
    generate_keypair("platform", keys)
    private_path = keys / "platform.key"
    public_path = keys / "platform.pub"
    if damage == "missing_public":
        public_path.unlink()
    elif damage == "mismatched_public":
        other = tmp_path / "other"
        generate_keypair("different", other)
        public_path.write_bytes((other / "different.pub").read_bytes())
    else:
        private_path.chmod(0o644)
    original = {path.name: path.read_bytes() for path in keys.glob("platform.*")}

    with pytest.raises(KeyMaterialError):
        DemoAgent.from_pem("platform", "Platform", private_path, 300)

    assert {path.name: path.read_bytes() for path in keys.glob("platform.*")} == original


@pytest.mark.unit
def test_demo_platform_loads_the_valid_provisioned_pair(tmp_path: Path) -> None:
    _, public = generate_keypair("platform", tmp_path)

    agent = DemoAgent.from_pem("platform", "Platform", tmp_path / "platform.key", 300)

    assert agent.public_key_string() == f"ed25519:{public_key_to_b64(public)}"
    assert agent.agent_id is None


@pytest.mark.unit
def test_demo_platform_rejects_a_path_for_another_handle(tmp_path: Path) -> None:
    generate_keypair("other", tmp_path)

    with pytest.raises(KeyMaterialError, match="platform.key"):
        DemoAgent.from_pem("platform", "Platform", tmp_path / "other.key", 300)


def _settings(private_path: Path) -> dict[str, object]:
    return {
        "identity_url": "http://identity.test",
        "bank_url": "http://bank.test",
        "task_board_url": "http://board.test",
        "reputation_url": "http://reputation.test",
        "court_url": "http://court.test",
        "platform_key_path": str(private_path),
        "token_ttl_seconds": 300,
    }


@pytest.mark.unit
def test_platform_key_configuration_is_required() -> None:
    settings = _settings(Path("keys/platform.key"))
    settings.pop("platform_key_path")

    with pytest.raises(ValidationError, match="platform_key_path"):
        PlatformConfig(**settings)


@pytest.mark.unit
def test_config_resolves_explicit_platform_key_relative_to_config(tmp_path: Path) -> None:
    config_path = tmp_path / "tools.yaml"
    config_path.write_text(yaml.safe_dump({"platform": _settings(Path("keys/platform.key"))}))

    settings = load_platform_settings(config_path)

    assert settings.platform_key_path == tmp_path / "keys" / "platform.key"


@pytest.mark.unit
async def test_replay_uses_only_the_configured_platform_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = generate_keypair("platform", tmp_path)
    config = PlatformConfig(**_settings(tmp_path / "platform.key"))

    async def _register(_http: object, agent: DemoAgent, _url: str) -> dict[str, str]:
        agent.agent_id = f"a-{agent.handle}"
        return {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "public_key": agent.public_key_string(),
            "registered_at": "2026-10-22T00:00:00Z",
        }

    register = AsyncMock(side_effect=_register)
    monkeypatch.setattr(clients, "register_agent", register)
    monkeypatch.setattr(clients, "create_account", AsyncMock(return_value={"account_id": "a-user"}))
    scenario = {
        "name": "Explicit keys",
        "agents": [{"handle": "user", "name": "User"}],
        "steps": [{"action": "register", "agent": "user"}],
    }
    scenario_bytes = yaml.safe_dump(scenario, sort_keys=False).encode()
    engine = ReplayEngine(scenario, config)
    engine.bind_durable_run(
        run_id="run-00000000-0000-4000-8000-000000000012",
        scenario_bytes=scenario_bytes,
        demo_config=DemoReplayConfig(
            environment_id="key-test",
            state_directory=tmp_path / "state",
            max_record_bytes=1_048_576,
            diagnostic_limit=16,
        ),
        effective_delay_seconds=0.0,
    )

    await engine.run()

    assert engine.platform is not None
    assert engine.platform.public_key_string() == f"ed25519:{public_key_to_b64(public)}"
    assert register.await_count == 2
    assert register.await_args_list[0].args[1] is engine.platform
    assert register.await_args_list[0].args[2] == config.identity_url
