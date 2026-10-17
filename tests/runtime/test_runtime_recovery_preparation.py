"""Recovery preparation keeps every mutable test input inside its owned run."""

from pathlib import Path

import pytest
import yaml
from scripts.isolated_test_runtime import IsolatedRuntime, RuntimeErrorWithLog

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_task_feeder_input_is_run_owned_and_explicitly_unprovisioned(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    try:
        config = yaml.safe_load(runtime.agent_config_path.read_bytes())
        configured = Path(config["task_feeder"]["tasks_file"])
        assert configured == runtime.run_dir / "data" / "math_tasks.jsonl"
        assert not configured.exists()
    finally:
        runtime.stop()


@pytest.mark.parametrize("recovery", [None, "invalid", {"maintenance_lock_path": ""}])
def test_bad_maintenance_preflight_reports_owned_log_and_releases_ports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recovery: object,
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway",),
    )
    config = yaml.safe_load(runtime.config_paths["db-gateway"].read_bytes())
    config["recovery"] = recovery
    config["secret"] = "DO-NOT-LOG-THIS"
    runtime.config_paths["db-gateway"].write_text(yaml.safe_dump(config))
    monkeypatch.setattr(
        runtime,
        "_ensure_keys_provisioned",
        lambda: pytest.fail("provisioned before valid preflight"),
    )
    try:
        with pytest.raises(RuntimeErrorWithLog) as exc_info:
            runtime.start()
        assert "db-gateway" in str(exc_info.value)
        assert str(runtime.log_paths["db-gateway"]) in str(exc_info.value)
        diagnostic = runtime.log_paths["db-gateway"].read_text()
        assert "maintenance" in diagnostic.lower()
        assert "DO-NOT-LOG-THIS" not in diagnostic
        assert runtime.processes == {}
        assert runtime._reservations == {}
    finally:
        runtime.stop()
