"""MP-02 service keys are provisioned only inside an owned runtime."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from scripts import isolated_test_runtime as runtime_module
from scripts.isolated_test_runtime import IsolatedRuntime
from service_auth.signing import load_keypair, public_key_to_b64

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
CALLERS = {
    "gateway-identity": ["gateway.identity.read", "gateway.identity.write"],
    "gateway-central-bank": ["gateway.bank.read", "gateway.bank.write"],
    "gateway-task-board": ["gateway.board.read", "gateway.board.write"],
    "gateway-reputation": ["gateway.reputation.read", "gateway.reputation.write"],
    "gateway-court": ["gateway.court.read", "gateway.court.write", "gateway.board.task.read"],
}


@pytest.fixture()
def gateway_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[IsolatedRuntime]:
    original_loader = runtime_module._load_yaml
    source_key_dir = tmp_path / "ordinary-service-keys"

    def source_config(path: Path) -> dict[str, object]:
        config = original_loader(path)
        if path == REPO_ROOT / "services/db-gateway/config.yaml":
            config["auth"] = {
                "audience": "db-gateway",
                "max_lifetime_seconds": 300,
                "clock_skew_seconds": 30,
                "trusted_callers": {
                    kid: {
                        "public_key_path": str(source_key_dir / f"{kid}.pub"),
                        "capabilities": capabilities,
                    }
                    for kid, capabilities in CALLERS.items()
                },
            }
        return config

    monkeypatch.setattr(runtime_module, "_load_yaml", source_config)
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT, artifacts_root=tmp_path, services=("db-gateway",)
    )
    try:
        yield runtime
    finally:
        runtime.stop()
    assert not source_key_dir.exists()


@pytest.mark.integration
def test_runtime_gateway_trust_paths_are_owned(gateway_runtime: IsolatedRuntime) -> None:
    config = yaml.safe_load(gateway_runtime.config_paths["db-gateway"].read_text())
    callers = config["auth"]["trusted_callers"]
    assert set(callers) == set(CALLERS)
    for kid, caller in callers.items():
        assert Path(caller["public_key_path"]) == gateway_runtime.run_dir / "keys" / f"{kid}.pub"


@pytest.mark.integration
def test_runtime_bootstraps_gateway_pairs_without_roster_entries(
    gateway_runtime: IsolatedRuntime,
) -> None:
    config_path = gateway_runtime.config_paths["db-gateway"]
    config = yaml.safe_load(config_path.read_text())
    for kid, caller in config["auth"]["trusted_callers"].items():
        caller["public_key_path"] = str(gateway_runtime.run_dir / "keys" / f"{kid}.pub")
    config_path.write_text(yaml.safe_dump(config))
    gateway_runtime._ensure_keys_provisioned()
    for kid in CALLERS:
        private_path = gateway_runtime.run_dir / "keys" / f"{kid}.key"
        assert private_path.is_file(), f"Gateway credential {kid} was not provisioned"
        private, public = load_keypair(kid, private_path.parent)
        assert public_key_to_b64(private.public_key()) == public_key_to_b64(public)
    agents = yaml.safe_load(gateway_runtime.agent_config_path.read_text())
    roster = yaml.safe_load(Path(agents["data"]["roster_path"]).read_text())
    assert set(CALLERS).isdisjoint(roster["agents"])


@pytest.mark.integration
@pytest.mark.parametrize(
    ("signer_override", "expected_message"),
    [
        ({"audience": "wrong-gateway"}, "audience"),
        ({"token_ttl_seconds": 301}, "token_ttl_seconds"),
    ],
)
def test_runtime_rejects_inconsistent_gateway_signer_before_key_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signer_override: dict[str, object],
    expected_message: str,
) -> None:
    original_loader = runtime_module._load_yaml

    def inconsistent_source(path: Path) -> dict[str, object]:
        config = original_loader(path)
        if path == REPO_ROOT / "services/db-gateway/config.yaml":
            auth = config["auth"]
            assert isinstance(auth, dict)
            auth["max_lifetime_seconds"] = 300
        elif path == REPO_ROOT / "services/identity/config.yaml":
            db_gateway = config["db_gateway"]
            assert isinstance(db_gateway, dict)
            signing = db_gateway["signing"]
            assert isinstance(signing, dict)
            signing.update(signer_override)
        return config

    monkeypatch.setattr(runtime_module, "_load_yaml", inconsistent_source)

    with pytest.raises(ValueError, match=expected_message):
        IsolatedRuntime.create(
            repo_root=REPO_ROOT,
            artifacts_root=tmp_path,
            services=("identity",),
        )
    assert not any(tmp_path.rglob("*.key"))
    assert not any(tmp_path.rglob("*.pub"))
