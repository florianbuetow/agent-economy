"""GAP-F6: startup-order regression test on disposable runtime resources."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from scripts.isolated_test_runtime import IsolatedRuntime, RuntimeErrorWithLog

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dependent_fails_cleanly_without_identity_then_recovers_once_identity_is_up(
    tmp_path: Path,
) -> None:
    """Central Bank fails before Identity and recovers after the dependency is ready."""
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path,
        services=("db-gateway", "identity", "central-bank"),
    )
    try:
        runtime.start_service("db-gateway")

        with pytest.raises(RuntimeErrorWithLog) as exc_info:
            runtime.start_service("central-bank")
        assert "central-bank exited with status" in str(exc_info.value)
        failed_process = runtime.processes["central-bank"]
        assert failed_process.returncode is not None
        assert failed_process.returncode != 0
        runtime.stop_service("central-bank")

        runtime.start_service("identity")
        runtime.start_service("central-bank")

        response = httpx.get(f"{runtime.service_url('central-bank')}/health", timeout=2.0)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    finally:
        runtime.stop()
