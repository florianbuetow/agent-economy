"""Integration tests for explicit disposable and ordinary key bootstrap (T-114)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from scripts.isolated_test_runtime import IsolatedRuntime

if TYPE_CHECKING:
    from collections.abc import Iterator


pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def runtime(tmp_path: Path) -> Iterator[IsolatedRuntime]:
    instance = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    try:
        yield instance
    finally:
        instance.stop()


def _configured_handles(runtime: IsolatedRuntime) -> tuple[str, ...]:
    config = yaml.safe_load(runtime.agent_config_path.read_text())
    roster_path = Path(config["data"]["roster_path"])
    roster = yaml.safe_load(roster_path.read_text())
    return tuple(sorted(roster["agents"]))


def _assert_matching_pair(private_path: Path, public_path: Path) -> None:
    private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    public_key = serialization.load_pem_public_key(public_path.read_bytes())
    assert isinstance(private_key, Ed25519PrivateKey)
    assert isinstance(public_key, Ed25519PublicKey)
    assert private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ) == public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _register_platform_agents(
    runtime: IsolatedRuntime,
    output_path: Path,
    count: int,
) -> dict[str, list[str]]:
    code = (
        "import asyncio,json,os,pathlib;"
        "from base_agent.factory import AgentFactory\n"
        "async def run():\n"
        " factory=AgentFactory(config_path=pathlib.Path(os.environ['AGENT_CONFIG_PATH']));\n"
        f" agents=[factory.platform_agent() for _ in range({count})];\n"
        " try:\n"
        "  await asyncio.gather(*(agent.register() for agent in agents));\n"
        "  data={'agent_ids':[str(agent.agent_id) for agent in agents],"
        "'fingerprints':[agent.get_public_key_b64() for agent in agents]};\n"
        f"  pathlib.Path({str(output_path)!r}).write_text(json.dumps(data));\n"
        " finally:\n"
        "  await asyncio.gather(*(agent.close() for agent in agents));\n"
        "asyncio.run(run())"
    )
    status = runtime.run_command(
        ["uv", "run", "--no-sync", "python", "-c", code],
        cwd=REPO_ROOT / "agents",
    )
    assert status == 0
    result = json.loads(output_path.read_text())
    assert isinstance(result, dict)
    return {
        "agent_ids": [str(value) for value in result["agent_ids"]],
        "fingerprints": [str(value) for value in result["fingerprints"]],
    }


def test_direct_start_service_bootstraps_only_disposable_configured_keys(
    runtime: IsolatedRuntime,
) -> None:
    keys_dir = runtime.run_dir / "keys"
    agent_handles = _configured_handles(runtime)
    gateway_config = yaml.safe_load(runtime.gateway_config_path.read_text())
    gateway_handles = tuple(sorted(gateway_config["auth"]["trusted_callers"]))
    handles = (*agent_handles, *gateway_handles)
    assert list(keys_dir.iterdir()) == []

    runtime.start_service("db-gateway")

    assert {path.name for path in keys_dir.iterdir()} == {
        f"{handle}.{suffix}" for handle in handles for suffix in ("key", "pub", "lock")
    }
    for handle in handles:
        private_path = keys_dir / f"{handle}.key"
        public_path = keys_dir / f"{handle}.pub"
        assert stat.S_IMODE(private_path.stat().st_mode) == 0o600
        _assert_matching_pair(private_path, public_path)


def test_concurrent_platform_registration_converges_per_runtime_and_isolates_runtimes(
    tmp_path: Path,
) -> None:
    first = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway", "identity"),
    )
    second = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway", "identity"),
    )
    try:
        first.start()
        second.start()
        first_result = _register_platform_agents(first, tmp_path / "first.json", 4)
        second_result = _register_platform_agents(second, tmp_path / "second.json", 2)

        assert len(set(first_result["agent_ids"])) == 1
        assert len(set(first_result["fingerprints"])) == 1
        assert len(set(second_result["agent_ids"])) == 1
        assert len(set(second_result["fingerprints"])) == 1
        assert first_result["agent_ids"][0] != second_result["agent_ids"][0]
        assert first_result["fingerprints"][0] != second_result["fingerprints"][0]
    finally:
        first.stop()
        second.stop()


def test_runtime_bootstrap_failure_prevents_service_launch(runtime: IsolatedRuntime) -> None:
    keys_dir = runtime.run_dir / "keys"
    handles = _configured_handles(runtime)
    damaged_handle = handles[0]
    (keys_dir / f"{damaged_handle}.key").write_bytes(b"partial private material\n")
    original_bytes = (keys_dir / f"{damaged_handle}.key").read_bytes()

    with pytest.raises(RuntimeError, match="Key provisioning failed"):
        runtime.start_service("db-gateway")

    assert runtime.processes == {}
    assert (keys_dir / f"{damaged_handle}.key").read_bytes() == original_bytes
    assert not (keys_dir / f"{damaged_handle}.pub").exists()


def test_bootstrap_owned_runner_drains_child_after_parent_exit(
    runtime: IsolatedRuntime,
    tmp_path: Path,
) -> None:
    child_pid_path = tmp_path / "bootstrap-child.pid"
    child_code = (
        "import os,pathlib,signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(os.getpid()));"
        "time.sleep(120)"
    )
    parent_code = (
        "import pathlib,subprocess,sys,time;"
        f"ready=pathlib.Path({str(child_pid_path)!r});"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        "deadline=time.monotonic()+10;"
        "\nwhile not ready.exists() and time.monotonic()<deadline: time.sleep(0.01);"
        "\nif not ready.exists(): raise RuntimeError('child readiness timeout');"
        "\nsys.exit(0)"
    )
    runner = getattr(runtime, "_run_key_bootstrap_command", None)
    assert callable(runner), "bootstrap must use the reviewed exact-owned command runner"

    result = runner(
        [
            "uv",
            "run",
            "--directory",
            str(REPO_ROOT / "services" / "db-gateway"),
            "python",
            "-c",
            parent_code,
        ],
        cwd=REPO_ROOT,
    )
    child_pid = int(child_pid_path.read_text())

    assert result == 0
    assert runtime.command_records == []
    assert len(runtime.bootstrap_records) == 1
    record = runtime.bootstrap_records[0]
    assert record["leader_returncode"] == 0
    assert record["group_drained"]
    assert not record["running"]
    manifest = json.loads((runtime.run_dir / "runtime.json").read_text())
    persisted = manifest["bootstrap_commands"][0]
    assert persisted["leader_returncode"] == 0
    assert persisted["group_drained"]
    assert not persisted["running"]
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_start_all_checks_keys_before_first_service_launch() -> None:
    root_justfile = (REPO_ROOT / "justfile").read_text()
    start_all = root_justfile.index("start-all:")
    preflight = root_justfile.index("key_provision_cli check", start_all)
    first_service = root_justfile.index("Starting tier 1 (DB Gateway)", start_all)

    assert preflight < first_service
    assert "just provision-keys" in root_justfile[start_all:first_service]
