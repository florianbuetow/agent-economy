"""Current-format backup restores submitted work that settles exactly once."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from scripts.backup import create_backup, restore_backup
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from service_auth import AgentFactory
from service_auth.financial_identity import operation_id_for
from tests.runtime.test_recovery_live_restore import DELIVERABLE, _public_workload

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


async def _resume(config: Path, receipt: dict[str, object]) -> None:
    factory = AgentFactory(config_path=config)
    poster = factory.user_agent("operator")
    worker = factory.create_agent("alice")
    try:
        await poster.register()
        await worker.register()
        assert poster.agent_id == receipt["poster_id"]
        assert worker.agent_id == receipt["worker_id"]
        task_id = str(receipt["task_id"])
        task = await poster.get_task(task_id)
        assert task["status"] == "submitted"
        assert await poster.download_asset(task_id, str(receipt["asset_id"])) == DELIVERABLE
        assert (await poster.get_balance())["balance"] == receipt["balance"]
        assert (await worker.get_balance())["balance"] == 0
        operation_id = operation_id_for("restore-resume-approval", (task_id,))
        for _ in range(2):
            response = await poster.approve_task(
                task_id,
                operation_id=operation_id,
                expected_lifecycle_version=task["lifecycle_version"],
            )
            assert response.status_code == 200
            assert (await poster.get_task(task_id))["status"] == "approved"
            assert (await worker.get_balance())["balance"] == 90
            assert (await poster.get_balance())["balance"] == int(str(receipt["balance"])) + 10
        worker_transactions = await worker.get_transactions()
        payouts = [row for row in worker_transactions if row["amount"] == 90]
        assert len(payouts) == 1
        assert await poster.download_asset(task_id, str(receipt["asset_id"])) == DELIVERABLE
    finally:
        await worker.close()
        await poster.close()


def test_current_backup_restores_and_resumes_pending_submission(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    archive_layout = tmp_path / "archive-layout"
    restored_layout = tmp_path / "restored-layout"
    bindings = {
        "services/db-gateway/data/economy.db": runtime.run_dir / "data" / "economy.db",
        "data/keys": runtime.run_dir / "keys",
        "services/task-board/data/assets": runtime.run_dir / "assets",
        "data/agent-runtime": runtime.run_dir / "data" / "agent-runtime",
    }
    try:
        runtime.start()
        assert runtime.provision() == 0
        receipt = asyncio.run(_public_workload(runtime.agent_config_path))
        for service in reversed(SERVICE_ORDER):
            runtime.stop_service(service)
        for relative, source in bindings.items():
            if not source.exists():
                continue
            target = archive_layout / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        archive = tmp_path / "economy.tar.gz"
        create_backup(archive_layout, archive)
        restore_backup(restored_layout, archive)
        for relative, source in bindings.items():
            if not source.exists():
                continue
            source.rename(source.with_name(f"unavailable-{source.name}"))
            restored = restored_layout / relative
            if restored.is_dir():
                shutil.copytree(restored, source)
            else:
                shutil.copy2(restored, source)
        runtime.start()
        asyncio.run(_resume(runtime.agent_config_path, receipt))
    finally:
        runtime.stop()
