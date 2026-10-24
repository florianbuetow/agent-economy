"""Regression tests for adopting complete keys in a mixed configured store."""

from __future__ import annotations

import importlib
import stat
from typing import TYPE_CHECKING, Protocol

import pytest
import yaml
from service_auth.signing import generate_keypair

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.capture import CaptureFixture


class _CliModule(Protocol):
    @staticmethod
    def main(argv: list[str]) -> int: ...


def _cli_module() -> _CliModule:
    module = importlib.import_module("key_provision_cli.__main__")
    assert hasattr(module, "main")
    return module  # type: ignore[return-value]


def _write_configs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    agent_keys = tmp_path / "agent-keys"
    gateway_keys = tmp_path / "gateway-keys"
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "alice": {"name": "Alice", "type": "worker"},
                    "platform": {"name": "Platform", "type": "platform"},
                }
            }
        )
    )
    agent_config = tmp_path / "agents.yaml"
    agent_config.write_text(
        yaml.safe_dump(
            {
                "platform": {
                    "identity_url": "http://identity.test",
                    "bank_url": "http://bank.test",
                    "task_board_url": "http://task-board.test",
                    "reputation_url": "http://reputation.test",
                    "court_url": "http://court.test",
                },
                "data": {
                    "keys_dir": str(agent_keys),
                    "roster_path": str(roster_path),
                },
                "signing": {
                    "token_ttl_seconds": 300,
                    "max_lifetime_seconds": 300,
                    "clock_skew_seconds": 30,
                },
            }
        )
    )
    gateway_config = tmp_path / "gateway.yaml"
    capabilities = {
        "gateway-court": ["gateway.court.write"],
        "gateway-identity": ["gateway.identity.read"],
    }
    gateway_config.write_text(
        yaml.safe_dump(
            {
                "auth": {
                    "audience": "db-gateway",
                    "max_lifetime_seconds": 300,
                    "clock_skew_seconds": 30,
                    "trusted_callers": {
                        handle: {
                            "public_key_path": str(gateway_keys / f"{handle}.pub"),
                            "capabilities": capabilities[handle],
                        }
                        for handle in ("gateway-court", "gateway-identity")
                    },
                }
            }
        )
    )
    return agent_config, gateway_config, agent_keys, gateway_keys


def _arguments(action: str, agent_config: Path, gateway_config: Path) -> list[str]:
    return [
        action,
        "--config",
        str(agent_config),
        "--gateway-config",
        str(gateway_config),
    ]


def _create_legacy_pair(handle: str, keys_dir: Path) -> dict[str, bytes]:
    generate_keypair(handle, keys_dir)
    (keys_dir / f"{handle}.key").chmod(0o644)
    return {suffix: (keys_dir / f"{handle}{suffix}").read_bytes() for suffix in (".key", ".pub")}


@pytest.mark.unit
def test_adoption_hardens_existing_pairs_and_reports_absent_pairs(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, gateway_config, agent_keys, gateway_keys = _write_configs(tmp_path)
    alice_bytes = _create_legacy_pair("alice", agent_keys)
    gateway_bytes = _create_legacy_pair("gateway-court", gateway_keys)

    result = _cli_module().main(_arguments("adopt-permissions", agent_config, gateway_config))
    output = capsys.readouterr()

    assert result == 0
    assert output.err == ""
    assert "adopted handle=alice fingerprint_sha256=" in output.out
    assert "adopted handle=gateway-court fingerprint_sha256=" in output.out
    assert "absent handle=platform next_action=provision" in output.out
    assert "absent handle=gateway-identity next_action=provision" in output.out
    assert "already" not in output.out
    assert stat.S_IMODE((agent_keys / "alice.key").stat().st_mode) == 0o600
    assert stat.S_IMODE((gateway_keys / "gateway-court.key").stat().st_mode) == 0o600
    assert not (agent_keys / "platform.key").exists()
    assert not (agent_keys / "platform.pub").exists()
    assert not (gateway_keys / "gateway-identity.key").exists()
    assert not (gateway_keys / "gateway-identity.pub").exists()
    assert alice_bytes == {
        suffix: (agent_keys / f"alice{suffix}").read_bytes() for suffix in (".key", ".pub")
    }
    assert gateway_bytes == {
        suffix: (gateway_keys / f"gateway-court{suffix}").read_bytes()
        for suffix in (".key", ".pub")
    }


@pytest.mark.unit
def test_adoption_partial_pair_failure_preserves_all_existing_permissions(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, gateway_config, agent_keys, gateway_keys = _write_configs(tmp_path)
    _create_legacy_pair("alice", agent_keys)
    _create_legacy_pair("gateway-court", gateway_keys)
    damaged_bytes = _create_legacy_pair("gateway-identity", gateway_keys)
    (gateway_keys / "gateway-identity.pub").unlink()

    result = _cli_module().main(_arguments("adopt-permissions", agent_config, gateway_config))
    output = capsys.readouterr()

    assert result == 1
    assert output.out == ""
    assert "reason=incomplete_pair" in output.err
    assert stat.S_IMODE((agent_keys / "alice.key").stat().st_mode) == 0o644
    assert stat.S_IMODE((gateway_keys / "gateway-court.key").stat().st_mode) == 0o644
    assert stat.S_IMODE((gateway_keys / "gateway-identity.key").stat().st_mode) == 0o644
    assert (gateway_keys / "gateway-identity.key").read_bytes() == damaged_bytes[".key"]
    assert not (agent_keys / "platform.key").exists()


@pytest.mark.unit
def test_adopt_then_provision_converges_to_strictly_valid_complete_store(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, gateway_config, agent_keys, gateway_keys = _write_configs(tmp_path)
    alice_bytes = _create_legacy_pair("alice", agent_keys)
    gateway_bytes = _create_legacy_pair("gateway-court", gateway_keys)
    cli = _cli_module()

    assert cli.main(_arguments("adopt-permissions", agent_config, gateway_config)) == 0
    capsys.readouterr()
    assert cli.main(_arguments("provision", agent_config, gateway_config)) == 0
    provision_output = capsys.readouterr()
    assert cli.main(_arguments("check", agent_config, gateway_config)) == 0
    check_output = capsys.readouterr()

    assert provision_output.err == check_output.err == ""
    for handle in ("alice", "platform", "gateway-court", "gateway-identity"):
        assert f"provisioned handle={handle} fingerprint_sha256=" in provision_output.out
        assert f"valid handle={handle} fingerprint_sha256=" in check_output.out
    assert alice_bytes == {
        suffix: (agent_keys / f"alice{suffix}").read_bytes() for suffix in (".key", ".pub")
    }
    assert gateway_bytes == {
        suffix: (gateway_keys / f"gateway-court{suffix}").read_bytes()
        for suffix in (".key", ".pub")
    }
    for keys_dir, handles in (
        (agent_keys, ("alice", "platform")),
        (gateway_keys, ("gateway-court", "gateway-identity")),
    ):
        for handle in handles:
            assert stat.S_IMODE((keys_dir / f"{handle}.key").stat().st_mode) == 0o600
