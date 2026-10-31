"""Unit tests for the explicit local key bootstrap CLI (T-114)."""

from __future__ import annotations

import importlib
import re
import stat
from copy import deepcopy
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
    try:
        module = importlib.import_module("key_provision_cli.__main__")
    except ModuleNotFoundError as exc:
        pytest.fail(f"MP-03 key_provision_cli surface is absent: {exc}")
    assert hasattr(module, "main")
    return module  # type: ignore[return-value]


def _write_config(tmp_path: Path) -> tuple[Path, Path, tuple[str, ...]]:
    handles = ("alice", "platform")
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump(
            {"agents": {handle: {"name": handle.title(), "type": "worker"} for handle in handles}}
        )
    )
    keys_dir = tmp_path / "keys"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
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
                    "keys_dir": str(keys_dir),
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
    return config_path, keys_dir, handles


def _assert_success_lines(output: str, action: str, handles: tuple[str, ...]) -> None:
    lines = output.splitlines()
    assert len(lines) == len(handles)
    for line, handle in zip(lines, handles, strict=True):
        assert re.fullmatch(
            rf"{action} handle={handle} fingerprint_sha256=[0-9a-f]{{64}}",
            line,
        )


def _write_gateway_config(
    tmp_path: Path,
    *,
    callers: tuple[str, ...] = ("gateway-identity", "gateway-court"),
) -> tuple[Path, Path]:
    keys_dir = tmp_path / "gateway-keys"
    config_path = tmp_path / "gateway.yaml"
    capabilities = {
        "gateway-identity": ["gateway.identity.read"],
        "gateway-court": ["gateway.court.write"],
    }
    config_path.write_text(
        yaml.safe_dump(
            {
                "service": {"name": "ignored-by-key-administration"},
                "auth": {
                    "audience": "db-gateway",
                    "max_lifetime_seconds": 300,
                    "clock_skew_seconds": 30,
                    "trusted_callers": {
                        caller: {
                            "public_key_path": str(keys_dir / f"{caller}.pub"),
                            "capabilities": capabilities[caller],
                        }
                        for caller in callers
                    },
                },
            }
        )
    )
    return config_path, keys_dir


@pytest.mark.unit
def test_provision_command_creates_configured_roster_idempotently(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path, keys_dir, handles = _write_config(tmp_path)
    cli = _cli_module()

    assert cli.main(["provision", "--config", str(config_path)]) == 0
    first_output = capsys.readouterr()
    first_bytes = {
        path.name: path.read_bytes()
        for path in keys_dir.iterdir()
        if path.suffix in {".key", ".pub"}
    }
    assert cli.main(["provision", "--config", str(config_path)]) == 0
    second_output = capsys.readouterr()

    _assert_success_lines(first_output.out, "provisioned", handles)
    assert second_output.out == first_output.out
    assert first_output.err == second_output.err == ""
    assert {
        path.name: path.read_bytes()
        for path in keys_dir.iterdir()
        if path.suffix in {".key", ".pub"}
    } == first_bytes


@pytest.mark.unit
def test_check_command_fails_actionably_without_creating_keys(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path, keys_dir, _ = _write_config(tmp_path)

    assert _cli_module().main(["check", "--config", str(config_path)]) == 1
    output = capsys.readouterr()

    assert output.out == ""
    assert "reason=absent_pair" in output.err
    assert "just provision-keys" in output.err
    assert not keys_dir.exists()


@pytest.mark.unit
def test_adopt_permissions_command_preserves_pair_bytes_and_hardens_mode(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path, keys_dir, handles = _write_config(tmp_path)
    for handle in handles:
        generate_keypair(handle, keys_dir)
        (keys_dir / f"{handle}.key").chmod(0o644)
    original = {
        path.name: path.read_bytes()
        for path in keys_dir.iterdir()
        if path.suffix in {".key", ".pub"}
    }

    assert _cli_module().main(["adopt-permissions", "--config", str(config_path)]) == 0
    output = capsys.readouterr()

    _assert_success_lines(output.out, "adopted", handles)
    assert output.err == ""
    assert {
        path.name: path.read_bytes()
        for path in keys_dir.iterdir()
        if path.suffix in {".key", ".pub"}
    } == original
    assert all(
        stat.S_IMODE((keys_dir / f"{handle}.key").stat().st_mode) == 0o600 for handle in handles
    )


@pytest.mark.unit
def test_adopt_permissions_command_refuses_partial_pair_without_repair(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path, keys_dir, _ = _write_config(tmp_path)
    generate_keypair("alice", keys_dir)
    private_path = keys_dir / "alice.key"
    private_path.chmod(0o644)
    (keys_dir / "alice.pub").unlink()
    original_private = private_path.read_bytes()

    assert _cli_module().main(["adopt-permissions", "--config", str(config_path)]) == 1
    output = capsys.readouterr()

    assert output.out == ""
    assert "reason=incomplete_pair" in output.err
    assert private_path.read_bytes() == original_private
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o644
    assert not (keys_dir / "alice.pub").exists()


@pytest.mark.unit
def test_gateway_provision_creates_distinct_pairs_idempotently_without_roster_changes(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, agent_keys_dir, agent_handles = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    roster_path = tmp_path / "roster.yaml"
    original_roster = deepcopy(yaml.safe_load(roster_path.read_text()))
    cli = _cli_module()
    arguments = [
        "provision",
        "--config",
        str(agent_config),
        "--gateway-config",
        str(gateway_config),
    ]

    assert cli.main(arguments) == 0
    first_output = capsys.readouterr()
    first_bytes = {
        path.name: path.read_bytes()
        for path in gateway_keys_dir.iterdir()
        if path.suffix in {".key", ".pub"}
    }
    assert cli.main(arguments) == 0
    second_output = capsys.readouterr()

    expected_handles = (*agent_handles, "gateway-court", "gateway-identity")
    _assert_success_lines(first_output.out, "provisioned", expected_handles)
    assert second_output == first_output
    assert set(first_bytes) == {
        "gateway-court.key",
        "gateway-court.pub",
        "gateway-identity.key",
        "gateway-identity.pub",
    }
    assert first_bytes["gateway-court.key"] != first_bytes["gateway-identity.key"]
    assert {
        path.name: path.read_bytes()
        for path in gateway_keys_dir.iterdir()
        if path.suffix in {".key", ".pub"}
    } == first_bytes
    assert yaml.safe_load(roster_path.read_text()) == original_roster
    assert {path.stem for path in agent_keys_dir.glob("*.key")} == set(agent_handles)


@pytest.mark.unit
def test_gateway_check_fails_without_creating_missing_pairs(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, _, agent_handles = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    agent_keys_dir = tmp_path / "keys"
    for handle in agent_handles:
        generate_keypair(handle, agent_keys_dir)

    assert (
        _cli_module().main(
            [
                "check",
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 1
    )
    output = capsys.readouterr()

    assert output.out == ""
    assert "reason=absent_pair" in output.err
    assert "just provision-keys" in output.err
    assert not gateway_keys_dir.exists()


@pytest.mark.unit
def test_gateway_provision_refuses_damaged_pair_without_replacement(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, _, _ = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    gateway_keys_dir.mkdir()
    damaged_private = gateway_keys_dir / "gateway-identity.key"
    damaged_private.write_bytes(b"damaged-private-key")
    damaged_private.chmod(0o600)
    original_bytes = damaged_private.read_bytes()

    assert (
        _cli_module().main(
            [
                "provision",
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 1
    )
    output = capsys.readouterr()

    assert output.out == ""
    assert "reason=incomplete_pair" in output.err
    assert "no key was replaced" in output.err
    assert damaged_private.read_bytes() == original_bytes
    assert not (gateway_keys_dir / "gateway-identity.pub").exists()
    assert not (gateway_keys_dir / "gateway-court.key").exists()
    assert not (tmp_path / "keys").exists()


@pytest.mark.unit
def test_gateway_relative_public_paths_resolve_from_gateway_config_directory(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, _, _ = _write_config(tmp_path)
    gateway_dir = tmp_path / "gateway-config"
    gateway_dir.mkdir()
    gateway_config, _ = _write_gateway_config(gateway_dir)
    raw = yaml.safe_load(gateway_config.read_text())
    for kid, caller in raw["auth"]["trusted_callers"].items():
        caller["public_key_path"] = f"../owned-keys/{kid}.pub"
    gateway_config.write_text(yaml.safe_dump(raw))

    assert (
        _cli_module().main(
            [
                "provision",
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 0
    )
    output = capsys.readouterr()

    assert output.err == ""
    for kid in ("gateway-court", "gateway-identity"):
        assert (tmp_path / "owned-keys" / f"{kid}.key").is_file()
        assert (tmp_path / "owned-keys" / f"{kid}.pub").is_file()


@pytest.mark.unit
def test_gateway_config_duplicate_paths_fail_before_any_key_write(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, agent_keys_dir, _ = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    raw = yaml.safe_load(gateway_config.read_text())
    raw["auth"]["trusted_callers"]["gateway-court"]["public_key_path"] = str(
        gateway_keys_dir / "nested" / ".." / "gateway-identity.pub"
    )
    gateway_config.write_text(yaml.safe_dump(raw))

    assert (
        _cli_module().main(
            [
                "provision",
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 1
    )
    output = capsys.readouterr()

    assert output.out == ""
    assert "gateway" in output.err.lower()
    assert not agent_keys_dir.exists()
    assert not gateway_keys_dir.exists()


@pytest.mark.unit
@pytest.mark.parametrize("action", ["provision", "check", "adopt-permissions"])
def test_gateway_pair_cannot_reuse_roster_identity(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    action: str,
) -> None:
    agent_config, agent_keys_dir, _ = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    cli = _cli_module()
    arguments = [
        "provision",
        "--config",
        str(agent_config),
        "--gateway-config",
        str(gateway_config),
    ]
    assert cli.main(arguments) == 0
    capsys.readouterr()
    (gateway_keys_dir / "gateway-identity.key").write_bytes(
        (agent_keys_dir / "platform.key").read_bytes()
    )
    (gateway_keys_dir / "gateway-identity.pub").write_bytes(
        (agent_keys_dir / "platform.pub").read_bytes()
    )
    if action == "adopt-permissions":
        (agent_keys_dir / "alice.key").chmod(0o644)

    assert (
        cli.main(
            [
                action,
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 1
    )
    output = capsys.readouterr()

    assert output.out == ""
    assert "duplicate" in output.err.lower()
    assert "platform" in output.err
    assert "gateway-identity" in output.err
    if action == "adopt-permissions":
        assert stat.S_IMODE((agent_keys_dir / "alice.key").stat().st_mode) == 0o644


@pytest.mark.unit
def test_gateway_adopt_late_damaged_pair_does_not_chmod_earlier_targets(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, agent_keys_dir, agent_handles = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    for handle in agent_handles:
        generate_keypair(handle, agent_keys_dir)
        (agent_keys_dir / f"{handle}.key").chmod(0o644)
    for handle in ("gateway-court", "gateway-identity"):
        generate_keypair(handle, gateway_keys_dir)
        (gateway_keys_dir / f"{handle}.key").chmod(0o644)
    (gateway_keys_dir / "gateway-identity.pub").unlink()

    assert (
        _cli_module().main(
            [
                "adopt-permissions",
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 1
    )
    output = capsys.readouterr()

    assert output.out == ""
    assert "reason=incomplete_pair" in output.err
    for handle in agent_handles:
        assert stat.S_IMODE((agent_keys_dir / f"{handle}.key").stat().st_mode) == 0o644
    assert stat.S_IMODE((gateway_keys_dir / "gateway-court.key").stat().st_mode) == 0o644


@pytest.mark.unit
def test_gateway_check_late_damaged_pair_preserves_all_earlier_files(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, agent_keys_dir, agent_handles = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    for handle in agent_handles:
        generate_keypair(handle, agent_keys_dir)
    for handle in ("gateway-court", "gateway-identity"):
        generate_keypair(handle, gateway_keys_dir)
    (gateway_keys_dir / "gateway-identity.pub").unlink()
    before = {
        path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for directory in (agent_keys_dir, gateway_keys_dir)
        for path in directory.iterdir()
    }

    assert (
        _cli_module().main(
            [
                "check",
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 1
    )
    output = capsys.readouterr()

    assert output.out == ""
    assert "reason=incomplete_pair" in output.err
    assert {
        path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for directory in (agent_keys_dir, gateway_keys_dir)
        for path in directory.iterdir()
    } == before


@pytest.mark.unit
def test_gateway_callers_cannot_share_one_identity(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, _, _ = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    for handle in ("gateway-court", "gateway-identity"):
        generate_keypair(handle, gateway_keys_dir)
    (gateway_keys_dir / "gateway-identity.key").write_bytes(
        (gateway_keys_dir / "gateway-court.key").read_bytes()
    )
    (gateway_keys_dir / "gateway-identity.pub").write_bytes(
        (gateway_keys_dir / "gateway-court.pub").read_bytes()
    )
    agent_keys_dir = tmp_path / "keys"
    for handle in ("alice", "platform"):
        generate_keypair(handle, agent_keys_dir)

    assert (
        _cli_module().main(
            [
                "check",
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 1
    )
    output = capsys.readouterr()

    assert output.out == ""
    assert "duplicate" in output.err.lower()
    assert "gateway-court" in output.err
    assert "gateway-identity" in output.err


@pytest.mark.unit
def test_gateway_handle_collision_with_roster_fails_before_writes(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, agent_keys_dir, _ = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    raw = yaml.safe_load(gateway_config.read_text())
    caller = raw["auth"]["trusted_callers"].pop("gateway-identity")
    caller["public_key_path"] = str(gateway_keys_dir / "platform.pub")
    raw["auth"]["trusted_callers"]["platform"] = caller
    gateway_config.write_text(yaml.safe_dump(raw))

    assert (
        _cli_module().main(
            [
                "provision",
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 1
    )
    output = capsys.readouterr()

    assert output.out == ""
    assert "handle collides" in output.err
    assert not agent_keys_dir.exists()
    assert not gateway_keys_dir.exists()


@pytest.mark.unit
def test_gateway_symlinked_public_path_fails_before_key_action(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    agent_config, agent_keys_dir, agent_handles = _write_config(tmp_path)
    gateway_config, gateway_keys_dir = _write_gateway_config(tmp_path)
    for handle in agent_handles:
        generate_keypair(handle, agent_keys_dir)
    alias_dir = tmp_path / "aliases"
    alias_dir.mkdir()
    public_alias = alias_dir / "gateway-identity.pub"
    public_alias.symlink_to(agent_keys_dir / "platform.pub")
    raw = yaml.safe_load(gateway_config.read_text())
    raw["auth"]["trusted_callers"]["gateway-identity"]["public_key_path"] = str(public_alias)
    gateway_config.write_text(yaml.safe_dump(raw))

    assert (
        _cli_module().main(
            [
                "check",
                "--config",
                str(agent_config),
                "--gateway-config",
                str(gateway_config),
            ]
        )
        == 1
    )
    output = capsys.readouterr()

    assert output.out == ""
    assert "reason=unsafe_path" in output.err
    assert "symlink" in output.err
    assert not gateway_keys_dir.exists()
